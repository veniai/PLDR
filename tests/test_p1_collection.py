from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

P1_TEST_ROOT = Path(tempfile.mkdtemp(prefix="pldr-p1-collection-tests-"))
# Never inherit a caller's PLDR_DATABASE_URL: this suite drops and recreates tables.
# During whole-suite discovery pldr_api.database may already be bound to test_p0's
# own temporary database; the explicit safety assertion in setUp accepts only either
# test-owned temporary location and fails closed for every other path.
os.environ["PLDR_DATABASE_URL"] = f"sqlite:///{P1_TEST_ROOT / 'collection.db'}"
os.environ.pop("LLM_API_KEY", None)

from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session as OrmSession

from pldr_api.collection import (
    execute_claimed_run,
    claim_next_run,
    enqueue_target_run,
    parse_rss_feed,
    run_once,
    utcnow,
)
from pldr_api.database import Base, SessionLocal, engine
from pldr_api.importers import (
    FetchedPublicText,
    ResponseTooLargeError,
    UnsupportedContentEncodingError,
    UnsupportedContentTypeError,
    fetch_public_text_response,
)
from pldr_api.intake import submit_web_intake
from pldr_api.main import app, ensure_compatible_schema
from pldr_api.models import (
    Claim,
    CollectionDiscoveredItem,
    CollectionRun,
    CollectionTarget,
    Document,
    Entity,
    Event,
    Evidence,
    IntakeItem,
    Snapshot,
    Source,
)


class P1CollectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database_path = engine.url.database
        if database_path and database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        engine.dispose()
        shutil.rmtree(P1_TEST_ROOT, ignore_errors=True)

    def setUp(self):
        database_path = Path(str(engine.url.database)).resolve()
        if not any(
            part.startswith(("pldr-p0-tests-", "pldr-p1-collection-tests-"))
            for part in database_path.parts
        ):
            self.fail(f"Refusing to reset non-test database: {database_path}")
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    @staticmethod
    def fetched(body: str, *, title: str = "Monitored dispatch") -> FetchedPublicText:
        html = (
            f"<html><head><title>{title}</title></head><body><article>"
            f"<p>{body}</p></article></body></html>"
        )
        return FetchedPublicText(
            resolved_url="https://updates.example.org/status",
            text=html,
            status_code=200,
            media_type="text/html",
            size_bytes=len(html.encode("utf-8")),
        )

    @staticmethod
    def rss_fetched(
        first_description: str = "The first controlled feed item contains enough durable summary text for review.",
        second_description: str = "The second controlled feed item contains its own independent summary and source link.",
    ) -> FetchedPublicText:
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Controlled dispatch feed</title>
<item><guid>item-one</guid><link>https://updates.example.org/news/one</link><title>First item</title><description>{first_description}</description></item>
<item><guid>item-two</guid><link>https://updates.example.org/news/two</link><title>Second item</title><description>{second_description}</description></item>
</channel></rss>'''
        return FetchedPublicText(
            resolved_url="https://updates.example.org/feed.xml",
            text=xml,
            status_code=200,
            media_type="application/rss+xml",
            size_bytes=len(xml.encode("utf-8")),
        )

    @staticmethod
    def formal_counts() -> dict[str, int]:
        models = [Source, Document, Snapshot, Event, Entity, Claim, Evidence]
        with SessionLocal() as session:
            return {
                model.__tablename__: int(
                    session.scalar(select(func.count()).select_from(model)) or 0
                )
                for model in models
            }

    def create_target(self, *, run_immediately: bool = True) -> dict:
        with patch(
            "pldr_api.collection_routes.validate_public_http_url", side_effect=lambda url: url
        ):
            response = self.client.post(
                "/pldr-api/v1/collection/targets",
                json={
                    "name": "Controlled status page",
                    "url": "https://updates.example.org/status",
                    "language": "en",
                    "interval_seconds": 300,
                    "run_immediately": run_immediately,
                },
            )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_rss_target(self, *, run_immediately: bool = True) -> dict:
        with patch(
            "pldr_api.collection_routes.validate_public_http_url",
            side_effect=lambda url: url,
        ):
            response = self.client.post(
                "/pldr-api/v1/collection/targets",
                json={
                    "name": "Controlled dispatch feed",
                    "target_type": "rss_feed",
                    "url": "https://updates.example.org/feed.xml",
                    "language": "en",
                    "interval_seconds": 300,
                    "run_immediately": run_immediately,
                },
            )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def run_with(self, fetched: FetchedPublicText | Exception) -> CollectionRun:
        if isinstance(fetched, Exception):
            effect = fetched
        else:
            effect = fetched
        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(side_effect=effect if isinstance(effect, Exception) else None, return_value=None if isinstance(effect, Exception) else effect),
        ):
            result = asyncio.run(run_once(worker_id="test-worker", lease_seconds=30))
        assert result is not None
        return result

    def test_baseline_unchanged_changed_diff_and_formal_isolation(self):
        before = self.formal_counts()
        created = self.create_target(run_immediately=True)
        target_id = created["target"]["id"]
        first_body = (
            "The monitored public bulletin reports a controlled baseline with enough "
            "detail for deterministic evidence candidate generation and human review."
        )
        # A durable success must not depend on a fallible post-commit refresh: that
        # window previously converted a committed capture into a failed run plus an
        # orphan Intake row.
        with patch.object(
            OrmSession,
            "refresh",
            side_effect=RuntimeError("post-commit refresh must not be called"),
        ):
            baseline = self.run_with(self.fetched(first_body))
        self.assertEqual((baseline.status, baseline.outcome, baseline.version_number), ("succeeded", "baseline", 1))
        self.assertIsNone(baseline.previous_intake_item_id)
        self.assertIsNotNone(baseline.current_intake_item_id)

        with SessionLocal() as session:
            baseline_item = session.get(IntakeItem, baseline.current_intake_item_id)
            assert baseline_item is not None
            self.assertEqual(baseline_item.input_type, "collection")
            self.assertEqual(baseline_item.review["collection"]["run_id"], baseline.id)
            self.assertEqual(baseline_item.review["collection"]["version_number"], 1)
            intake_count = session.scalar(select(func.count()).select_from(IntakeItem))
        self.assertEqual(intake_count, 1)
        self.assertEqual(self.formal_counts(), before)

        queued = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/run")
        self.assertEqual(queued.status_code, 200, queued.text)
        same_body_new_raw = self.fetched(first_body, title="A raw-only title change")
        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(return_value=same_body_new_raw),
        ), patch(
            "pldr_api.collection.submit_web_intake",
            new=AsyncMock(side_effect=AssertionError("unchanged content must not create intake")),
        ):
            unchanged = asyncio.run(run_once(worker_id="test-worker"))
        assert unchanged is not None
        self.assertEqual((unchanged.status, unchanged.outcome, unchanged.version_number), ("succeeded", "unchanged", 1))
        self.assertEqual(unchanged.previous_intake_item_id, baseline.current_intake_item_id)
        self.assertEqual(unchanged.current_intake_item_id, baseline.current_intake_item_id)
        with SessionLocal() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(IntakeItem)), 1)

        queued = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/run")
        self.assertEqual(queued.status_code, 200, queued.text)
        changed_body = (
            "The monitored public bulletin now reports a material route closure, a newly "
            "announced inspection window, and enough detail for analyst verification."
        )
        changed = self.run_with(self.fetched(changed_body))
        self.assertEqual((changed.status, changed.outcome, changed.version_number), ("succeeded", "changed", 2))
        self.assertEqual(changed.previous_intake_item_id, baseline.current_intake_item_id)
        self.assertNotEqual(changed.current_intake_item_id, baseline.current_intake_item_id)
        self.assertEqual(self.formal_counts(), before)

        diff = self.client.get(f"/pldr-api/v1/collection/runs/{changed.id}/diff")
        self.assertEqual(diff.status_code, 200, diff.text)
        diff_payload = diff.json()
        self.assertEqual(diff_payload["previous"]["intake_item_id"], baseline.current_intake_item_id)
        self.assertEqual(diff_payload["current"]["intake_item_id"], changed.current_intake_item_id)
        self.assertIn("closure", diff_payload["added_text"])
        self.assertGreater(diff_payload["stats"]["added_words"], 0)

        detail = self.client.get(f"/pldr-api/v1/collection/targets/{target_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["version_count"], 2)
        self.assertEqual(len(detail.json()["versions"]), 2)
        summary = self.client.get("/pldr-api/v1/collection/summary")
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["pending_review"], 2)

    def test_rss_feed_discovers_deduplicates_and_keeps_review_boundary(self):
        before = self.formal_counts()
        created = self.create_rss_target(run_immediately=True)
        target_id = created["target"]["id"]

        first = self.run_with(self.rss_fetched())
        self.assertEqual((first.status, first.outcome, first.version_number), ("succeeded", "items", None))
        self.assertIsNone(first.current_intake_item_id)
        self.assertEqual(
            (first.discovered_count, first.new_item_count, first.duplicate_item_count, first.invalid_item_count),
            (2, 2, 0, 0),
        )

        with SessionLocal() as session:
            items = list(
                session.scalars(
                    select(IntakeItem).where(IntakeItem.input_type == "rss_collection").order_by(IntakeItem.canonical_url)
                )
            )
            self.assertEqual([item.canonical_url for item in items], [
                "https://updates.example.org/news/one",
                "https://updates.example.org/news/two",
            ])
            self.assertTrue(all(item.status == "candidate_ready" for item in items))
            self.assertTrue(all(
                item.review["rss_collection"]["run_id"] == first.id for item in items
            ))
            states = list(session.scalars(select(CollectionDiscoveredItem)))
            self.assertEqual({state.status for state in states}, {"ready"})
            self.assertEqual({state.intake_item_id for state in states}, {item.id for item in items})

        detail = self.client.get(f"/pldr-api/v1/collection/targets/{target_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        self.assertEqual(payload["target_type"], "rss_feed")
        self.assertEqual(payload["version_count"], 0)
        self.assertEqual(payload["discovered_item_count"], 2)
        self.assertEqual(len(payload["discovered_items"]), 2)
        items_page = self.client.get(f"/pldr-api/v1/collection/targets/{target_id}/items?offset=1&limit=1")
        self.assertEqual(items_page.status_code, 200, items_page.text)
        self.assertEqual(items_page.json()["count"], 2)
        self.assertEqual(len(items_page.json()["items"]), 1)

        queued = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/run")
        self.assertEqual(queued.status_code, 200, queued.text)
        second = self.run_with(self.rss_fetched())
        self.assertEqual((second.status, second.outcome), ("succeeded", "items"))
        self.assertEqual(
            (second.discovered_count, second.new_item_count, second.duplicate_item_count, second.invalid_item_count),
            (2, 0, 2, 0),
        )
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IntakeItem)),
                2,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(CollectionDiscoveredItem)),
                2,
            )
        summary = self.client.get("/pldr-api/v1/collection/summary")
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["pending_review"], 2)
        self.assertEqual(summary.json()["discoveries"], 2)
        self.assertEqual(self.formal_counts(), before)

    def test_rss_rejects_unsafe_items_and_malformed_feed_fail_closed(self):
        created = self.create_rss_target(run_immediately=True)
        target_id = created["target"]["id"]
        xml = '''<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Unsafe mixed feed</title>
        <item><guid>safe</guid><link>https://updates.example.org/news/safe</link><title>Safe item</title><description>A public feed item with enough controlled summary text for review.</description></item>
        <item><guid>private</guid><link>http://127.0.0.1/status</link><title>Private item</title><description>This private link must fail closed and must not create an intake.</description></item>
        </channel></rss>'''
        mixed = FetchedPublicText(
            resolved_url="https://updates.example.org/feed.xml",
            text=xml,
            status_code=200,
            media_type="application/rss+xml",
            size_bytes=len(xml.encode("utf-8")),
        )
        first = self.run_with(mixed)
        self.assertEqual((first.status, first.outcome), ("succeeded", "items"))
        self.assertEqual(
            (first.discovered_count, first.new_item_count, first.duplicate_item_count, first.invalid_item_count),
            (1, 1, 0, 1),
        )
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IntakeItem)),
                1,
            )
            item = session.scalar(select(IntakeItem))
            assert item is not None
            self.assertEqual(item.canonical_url, "https://updates.example.org/news/safe")

        queued = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/run")
        self.assertEqual(queued.status_code, 200, queued.text)
        malformed = FetchedPublicText(
            resolved_url="https://updates.example.org/feed.xml",
            text="<rss><channel><title>broken",
            status_code=200,
            media_type="application/rss+xml",
            size_bytes=30,
        )
        failed = self.run_with(malformed)
        self.assertEqual((failed.status, failed.error_class), ("failed", "rss_parse"))
        self.assertIn("malformed", failed.error_message)
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IntakeItem)),
                1,
            )

    def test_rss_lease_replay_adopts_item_committed_before_state_link(self):
        created = self.create_rss_target(run_immediately=True)
        run_id = created["queued_run"]["id"]
        fetched = self.rss_fetched()
        parsed = parse_rss_feed(fetched)
        first = parsed.items[0]

        with SessionLocal() as session:
            claimed = claim_next_run(
                session, worker_id="crashing-rss-worker", lease_seconds=30, now=utcnow()
            )
            assert claimed is not None
            self.assertEqual(claimed.id, run_id)
            state = CollectionDiscoveredItem(
                id="col_item_committed_before_link",
                target_id=created["target"]["id"],
                item_key=first.item_key,
                source_url=first.url,
                title=first.title,
                status="pending",
                first_seen_run_id=run_id,
                last_seen_run_id=run_id,
            )
            session.add(state)
            session.commit()
            orphan = asyncio.run(
                submit_web_intake(
                    session,
                    first.url,
                    "Controlled dispatch feed",
                    first.title,
                    first.html,
                    "en",
                    input_type="rss_collection",
                    review_extra={
                        "rss_collection": {
                            "target_id": created["target"]["id"],
                            "run_id": run_id,
                            "item_key": first.item_key,
                            "feed_url": created["target"]["url"],
                            "source_url": first.url,
                        }
                    },
                )
            )
            orphan_id = orphan.id
            crashed = session.get(CollectionRun, run_id)
            assert crashed is not None
            crashed.lease_expires_at = utcnow() - timedelta(seconds=1)
            session.commit()

        replayed = self.run_with(fetched)
        self.assertEqual((replayed.id, replayed.status, replayed.outcome), (run_id, "succeeded", "items"))
        self.assertEqual((replayed.new_item_count, replayed.duplicate_item_count), (2, 0))
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IntakeItem)),
                2,
            )
            adopted = session.get(IntakeItem, orphan_id)
            assert adopted is not None
            self.assertEqual(adopted.review["rss_collection"]["run_id"], run_id)
            linked_state = session.get(
                CollectionDiscoveredItem, "col_item_committed_before_link"
            )
            assert linked_state is not None
            self.assertEqual((linked_state.status, linked_state.intake_item_id), ("ready", orphan_id))

    def test_archived_intake_keeps_collection_version_chain_and_diff(self):
        created = self.create_target(run_immediately=True)
        target_id = created["target"]["id"]
        baseline = self.run_with(
            self.fetched(
                "The first monitored bulletin establishes an immutable baseline with "
                "enough public text for later version comparison and analyst review."
            )
        )
        baseline_intake_id = baseline.current_intake_item_id
        assert baseline_intake_id is not None

        queued = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/run")
        self.assertEqual(queued.status_code, 200, queued.text)
        changed = self.run_with(
            self.fetched(
                "The second monitored bulletin reports a material closure and a new "
                "inspection window with enough public text for a durable comparison."
            )
        )
        changed_intake_id = changed.current_intake_item_id
        assert changed_intake_id is not None
        self.assertEqual(changed.previous_intake_item_id, baseline_intake_id)

        before_archive = self.client.get(
            f"/pldr-api/v1/intake/{baseline_intake_id}"
        )
        self.assertEqual(before_archive.status_code, 200, before_archive.text)
        original_status = before_archive.json()["status"]
        archived = self.client.post(
            f"/pldr-api/v1/intake/{baseline_intake_id}/archive",
            json={
                "analyst": "collection-archive-test",
                "reason": "Hide an old version from active review without deleting it",
            },
        )
        self.assertEqual(archived.status_code, 200, archived.text)

        archived_detail = self.client.get(
            f"/pldr-api/v1/intake/{baseline_intake_id}"
        )
        self.assertEqual(archived_detail.status_code, 200, archived_detail.text)
        self.assertTrue(archived_detail.json()["archived"])
        self.assertEqual(archived_detail.json()["status"], original_status)

        with SessionLocal() as session:
            persisted_baseline = session.get(CollectionRun, baseline.id)
            persisted_changed = session.get(CollectionRun, changed.id)
            assert persisted_baseline is not None and persisted_changed is not None
            self.assertEqual(
                persisted_baseline.current_intake_item_id, baseline_intake_id
            )
            self.assertEqual(
                persisted_changed.previous_intake_item_id, baseline_intake_id
            )
            self.assertEqual(
                persisted_changed.current_intake_item_id, changed_intake_id
            )

        target_detail = self.client.get(
            f"/pldr-api/v1/collection/targets/{target_id}"
        )
        self.assertEqual(target_detail.status_code, 200, target_detail.text)
        versions_by_id = {
            version["id"]: version for version in target_detail.json()["versions"]
        }
        self.assertEqual(len(versions_by_id), 2)
        self.assertEqual(
            versions_by_id[baseline.id]["intake_chain"]["current"],
            baseline_intake_id,
        )
        self.assertEqual(
            versions_by_id[changed.id]["intake_chain"],
            {"previous": baseline_intake_id, "current": changed_intake_id},
        )

        version_history = self.client.get(
            f"/pldr-api/v1/collection/targets/{target_id}/versions"
        )
        self.assertEqual(version_history.status_code, 200, version_history.text)
        self.assertEqual(version_history.json()["count"], 2)
        self.assertEqual(
            {item["id"] for item in version_history.json()["items"]},
            {baseline.id, changed.id},
        )

        diff = self.client.get(f"/pldr-api/v1/collection/runs/{changed.id}/diff")
        self.assertEqual(diff.status_code, 200, diff.text)
        self.assertEqual(diff.json()["previous"]["intake_item_id"], baseline_intake_id)
        self.assertEqual(diff.json()["current"]["intake_item_id"], changed_intake_id)
        self.assertIn("closure", diff.json()["added_text"])

        investigation = self.client.post(
            "/pldr-api/v1/investigations",
            json={
                "title": "Collection archive attachment topic",
                "question": "Which monitored versions remain active?",
            },
        )
        self.assertEqual(investigation.status_code, 201, investigation.text)
        investigation_id = investigation.json()["id"]
        linked_target = self.client.post(
            f"/pldr-api/v1/investigations/{investigation_id}/links",
            json={"object_type": "collection_target", "object_id": target_id},
        )
        self.assertEqual(linked_target.status_code, 201, linked_target.text)
        # Only the non-archived current version becomes an active review task.
        self.assertEqual(linked_target.json()["version_tasks_created"], 1)
        from pldr_api.investigations import attach_collection_intake_to_investigations
        from pldr_api.models import InvestigationLink, ReviewTask

        with SessionLocal() as session:
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == investigation_id,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == baseline_intake_id,
                    )
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(ReviewTask.id).where(
                        ReviewTask.investigation_id == investigation_id,
                        ReviewTask.intake_item_id == baseline_intake_id,
                    )
                )
            )
            archived_baseline = session.get(IntakeItem, baseline_intake_id)
            assert archived_baseline is not None
            self.assertEqual(
                attach_collection_intake_to_investigations(
                    session,
                    target_id=target_id,
                    item=archived_baseline,
                    run_id=baseline.id,
                    outcome="baseline",
                ),
                0,
            )
            session.commit()

        restored = self.client.post(
            f"/pldr-api/v1/intake/{baseline_intake_id}/restore"
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        with SessionLocal() as session:
            restored_baseline = session.get(IntakeItem, baseline_intake_id)
            assert restored_baseline is not None
            self.assertEqual(
                attach_collection_intake_to_investigations(
                    session,
                    target_id=target_id,
                    item=restored_baseline,
                    run_id=baseline.id,
                    outcome="baseline",
                ),
                1,
            )
            session.commit()
            self.assertIsNotNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == investigation_id,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == baseline_intake_id,
                    )
                )
            )
            self.assertIsNotNone(
                session.scalar(
                    select(ReviewTask.id).where(
                        ReviewTask.investigation_id == investigation_id,
                        ReviewTask.intake_item_id == baseline_intake_id,
                    )
                )
            )

    def test_failed_run_retry_and_no_intake_pollution(self):
        before = self.formal_counts()
        created = self.create_target(run_immediately=True)
        target_id = created["target"]["id"]
        failed = self.run_with(httpx.ReadTimeout("controlled timeout"))
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_class, "timeout")
        self.assertIsNone(failed.current_intake_item_id)
        with SessionLocal() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(IntakeItem)), 0)
        self.assertEqual(self.formal_counts(), before)

        retried = self.client.post(f"/pldr-api/v1/collection/runs/{failed.id}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        retry_run = retried.json()["run"]
        self.assertEqual(retry_run["retry_of_run_id"], failed.id)
        self.assertEqual(retry_run["attempt_number"], 2)
        recovered = self.run_with(
            self.fetched(
                "The recovered public bulletin contains enough verifiable text to create "
                "a first immutable intake version after the failed network attempt."
            )
        )
        self.assertEqual((recovered.status, recovered.outcome), ("succeeded", "baseline"))
        with SessionLocal() as session:
            target = session.get(CollectionTarget, target_id)
            assert target is not None
            self.assertEqual(target.health, "healthy")
            self.assertEqual(target.consecutive_failures, 0)
            self.assertIsNone(target.last_error)
        self.assertEqual(self.formal_counts(), before)

    def test_collection_tail_failure_preserves_committed_analyst_rejection(self):
        before = self.formal_counts()
        self.create_target(run_immediately=True)
        captured: dict[str, str] = {}

        def reject_then_fail(
            _worker_session,
            *,
            target_id: str,
            item: IntakeItem,
            run_id: str,
            outcome: str,
        ) -> int:
            captured.update(
                item_id=item.id,
                target_id=target_id,
                run_id=run_id,
                outcome=outcome,
            )
            # The material and its collection provenance have already committed.
            # Reproduce an analyst decision landing before a later linking failure.
            from pldr_api.intake import reject_intake

            with SessionLocal() as analyst_session:
                analyst_item = analyst_session.get(IntakeItem, item.id)
                assert analyst_item is not None
                reject_intake(
                    analyst_session,
                    analyst_item,
                    "collection-tail-analyst",
                    "Reject after the durable collection capture",
                )
            raise RuntimeError("controlled collection attach tail failure")

        with patch(
            "pldr_api.investigations.attach_collection_intake_to_investigations",
            new=reject_then_fail,
        ):
            failed = self.run_with(
                self.fetched(
                    "This monitored bulletin is committed before an analyst rejects it, "
                    "then the collection attachment tail fails deterministically."
                )
            )

        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_class, "internal")
        self.assertIn("controlled collection attach tail failure", failed.error_message)
        self.assertIsNone(failed.current_intake_item_id)
        self.assertEqual(captured["run_id"], failed.id)
        self.assertEqual(captured["outcome"], "baseline")
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IntakeItem)),
                1,
            )
            preserved = session.get(IntakeItem, captured["item_id"])
            assert preserved is not None
            self.assertEqual(preserved.status, "rejected")
            self.assertEqual(preserved.disposition, "reject")
            self.assertEqual(preserved.reviewed_by, "collection-tail-analyst")
            self.assertEqual(
                preserved.rejection_reason,
                "Reject after the durable collection capture",
            )
            self.assertEqual(
                preserved.review["collection"]["run_id"],
                failed.id,
            )
            self.assertEqual(
                preserved.review["collection"]["target_id"],
                captured["target_id"],
            )
            self.assertTrue(preserved.candidates)
            self.assertTrue(
                all(candidate.disposition == "rejected" for candidate in preserved.candidates)
            )
        self.assertEqual(self.formal_counts(), before)

    def test_collection_tail_failure_preserves_unreviewed_committed_capture(self):
        before = self.formal_counts()
        self.create_target(run_immediately=True)
        captured: dict[str, str] = {}

        def fail_after_provenance(
            _worker_session,
            *,
            target_id: str,
            item: IntakeItem,
            run_id: str,
            outcome: str,
        ) -> int:
            captured.update(
                item_id=item.id,
                target_id=target_id,
                run_id=run_id,
                outcome=outcome,
            )
            raise RuntimeError("controlled unreviewed collection tail failure")

        with patch(
            "pldr_api.investigations.attach_collection_intake_to_investigations",
            new=fail_after_provenance,
        ):
            failed = self.run_with(
                self.fetched(
                    "This unreviewed monitored bulletin is already durable when the "
                    "later investigation attachment fails deterministically."
                )
            )

        self.assertEqual(failed.status, "failed")
        self.assertIn("controlled unreviewed collection tail failure", failed.error_message)
        self.assertIsNone(failed.current_intake_item_id)
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IntakeItem)),
                1,
            )
            preserved = session.get(IntakeItem, captured["item_id"])
            assert preserved is not None
            self.assertEqual(preserved.status, "candidate_ready")
            self.assertIsNone(preserved.disposition)
            self.assertIsNone(preserved.reviewed_by)
            self.assertEqual(preserved.review["collection"]["run_id"], failed.id)
            self.assertEqual(
                preserved.review["collection"]["target_id"],
                captured["target_id"],
            )
            self.assertTrue(preserved.candidates)
        self.assertEqual(self.formal_counts(), before)

    def test_collection_run_preserves_failed_intake_committed_by_submit(self):
        before = self.formal_counts()
        self.create_target(run_immediately=True)
        failed_item_id = "int_collection_committed_failure"

        async def submit_committed_failure(
            session,
            url: str,
            source_name: str | None,
            _title: str | None,
            html: str | None,
            language: str,
            input_type: str = "web",
        ) -> IntakeItem:
            now = utcnow()
            item = IntakeItem(
                id=failed_item_id,
                input_type=input_type,
                status="failed",
                error="controlled committed intake failure",
                source_description=source_name or "",
                source_url=url,
                canonical_url=url,
                language=language,
                raw_snapshot=html or "",
                raw_hash="a" * 64,
                extracted_snapshot="",
                extracted_hash="b" * 64,
                review={"fixture": "committed-before-run-failure"},
                candidate_relations=[],
                confirmation_result={},
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            session.commit()
            return item

        with patch(
            "pldr_api.collection.submit_web_intake",
            new=submit_committed_failure,
        ):
            failed = self.run_with(
                self.fetched(
                    "This otherwise valid collection response forces the intake submit "
                    "boundary to return a durable, user-visible failure record."
                )
            )

        self.assertEqual(failed.status, "failed")
        self.assertIn("controlled committed intake failure", failed.error_message)
        self.assertIsNone(failed.current_intake_item_id)
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IntakeItem)),
                1,
            )
            preserved = session.get(IntakeItem, failed_item_id)
            assert preserved is not None
            self.assertEqual(preserved.status, "failed")
            self.assertEqual(preserved.error, "controlled committed intake failure")
            self.assertEqual(
                preserved.review,
                {"fixture": "committed-before-run-failure"},
            )
        self.assertEqual(self.formal_counts(), before)

    def test_lease_replay_adopts_intake_committed_before_run_link(self):
        created = self.create_target(run_immediately=True)
        run_id = created["queued_run"]["id"]
        with SessionLocal() as session:
            claimed = claim_next_run(
                session, worker_id="crashing-worker", lease_seconds=30, now=utcnow()
            )
            assert claimed is not None
            self.assertEqual(claimed.id, run_id)

        fetched = self.fetched(
            "This durable collection body was committed immediately before a simulated "
            "worker crash and must be adopted rather than submitted a second time."
        )
        with SessionLocal() as session:
            orphan = asyncio.run(
                submit_web_intake(
                    session,
                    fetched.resolved_url,
                    "Controlled status page",
                    "Monitored dispatch",
                    fetched.text,
                    "en",
                    input_type="collection",
                )
            )
            orphan_id = orphan.id
            self.assertIsNone((orphan.review or {}).get("collection"))
        with SessionLocal() as session:
            crashed = session.get(CollectionRun, run_id)
            assert crashed is not None
            crashed.resolved_url = fetched.resolved_url
            crashed.http_status = fetched.status_code
            crashed.media_type = fetched.media_type
            crashed.size_bytes = fetched.size_bytes
            crashed.raw_hash = orphan.raw_hash
            crashed.body_hash = orphan.extracted_hash
            crashed.lease_expires_at = utcnow() - timedelta(seconds=1)
            session.commit()

        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(side_effect=AssertionError("durable replay must not refetch")),
        ):
            replayed = asyncio.run(run_once(worker_id="recovery-worker"))
        assert replayed is not None
        self.assertEqual(replayed.id, run_id)
        self.assertEqual((replayed.status, replayed.outcome), ("succeeded", "baseline"))
        self.assertEqual(replayed.current_intake_item_id, orphan_id)
        with SessionLocal() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(IntakeItem)), 1)
            adopted = session.get(IntakeItem, orphan_id)
            assert adopted is not None
            self.assertEqual(adopted.review["collection"]["run_id"], run_id)
            self.assertEqual(adopted.review["collection"]["current_intake_item_id"], orphan_id)

    def test_database_queue_claim_and_expired_lease_recovery(self):
        created = self.create_target(run_immediately=False)
        target_id = created["target"]["id"]
        base_time = utcnow()
        with SessionLocal() as session:
            target = session.get(CollectionTarget, target_id)
            assert target is not None
            target.next_run_at = base_time + timedelta(hours=1)
            run, was_created = enqueue_target_run(
                session, target, trigger="manual", now=base_time
            )
            self.assertTrue(was_created)
            run_id = run.id
            duplicate, duplicate_created = enqueue_target_run(
                session, target, trigger="manual", now=base_time
            )
            self.assertFalse(duplicate_created)
            self.assertEqual(duplicate.id, run_id)

        # A clean schema keeps exactly one named unique slot guard; compatibility
        # startup must recognize it instead of rewriting the table or adding a
        # redundant active_key index.
        ensure_compatible_schema()
        active_indexes = [
            index
            for index in inspect(engine).get_indexes("collection_runs")
            if set(index.get("column_names") or []) == {"active_key"}
        ]
        self.assertEqual(len(active_indexes), 1)
        self.assertEqual(active_indexes[0]["name"], "uq_collection_run_active_key")
        self.assertTrue(active_indexes[0]["unique"])
        with SessionLocal() as session:
            self.assertEqual(session.get(CollectionRun, run_id).active_key, target_id)

        with SessionLocal() as session:
            first_claim = claim_next_run(
                session,
                worker_id="worker-a",
                lease_seconds=1,
                now=base_time,
            )
            assert first_claim is not None
            self.assertEqual(first_claim.id, run_id)
            self.assertEqual(first_claim.status, "running")
            self.assertEqual(first_claim.lease_owner, "worker-a")

        with SessionLocal() as session:
            recovered_claim = claim_next_run(
                session,
                worker_id="worker-b",
                lease_seconds=30,
                now=base_time + timedelta(seconds=2),
            )
            assert recovered_claim is not None
            self.assertEqual(recovered_claim.id, run_id)
            self.assertEqual(recovered_claim.status, "running")
            self.assertEqual(recovered_claim.lease_owner, "worker-b")
            self.assertEqual(recovered_claim.lease_recoveries, 1)

        paused = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/pause")
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertFalse(paused.json()["enabled"])
        blocked = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/run")
        self.assertEqual(blocked.status_code, 409)
        resumed = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/resume")
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertTrue(resumed.json()["enabled"])
        self.assertIsNotNone(resumed.json()["next_run_at"])

    def test_half_migration_repairs_active_slot_once(self):
        # Simulate SQLite persisting ALTER TABLE while the later unique-index step
        # never completed. The next startup repairs it, and later startups must not
        # rewrite the historical table once an equivalent guard exists.
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE collection_runs"))
            connection.execute(
                text(
                    "CREATE TABLE collection_runs ("
                    "id VARCHAR(96) PRIMARY KEY, "
                    "target_id VARCHAR(80), "
                    "status VARCHAR(20), "
                    "active_key VARCHAR(80))"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO collection_runs (id, target_id, status, active_key) VALUES "
                    "('queued-run', 'target-a', 'queued', NULL), "
                    "('finished-run', 'target-a', 'succeeded', NULL)"
                )
            )

        ensure_compatible_schema()
        active_indexes = [
            index
            for index in inspect(engine).get_indexes("collection_runs")
            if index.get("unique")
            and set(index.get("column_names") or []) == {"active_key"}
        ]
        self.assertEqual(len(active_indexes), 1)
        with engine.begin() as connection:
            rows = connection.execute(
                text("SELECT id, active_key FROM collection_runs ORDER BY id")
            ).all()
            self.assertEqual(
                rows,
                [("finished-run", None), ("queued-run", "target-a")],
            )
            connection.execute(
                text(
                    "CREATE TRIGGER reject_active_rewrite "
                    "BEFORE UPDATE OF active_key ON collection_runs "
                    "BEGIN SELECT RAISE(FAIL, 'unexpected active_key rewrite'); END"
                )
            )

        ensure_compatible_schema()

    def test_additive_migration_preserves_existing_collection_targets(self):
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE collection_targets"))
            connection.execute(
                text(
                    "CREATE TABLE collection_targets ("
                    "id VARCHAR(80) PRIMARY KEY, name VARCHAR(200) NOT NULL, "
                    "url VARCHAR(900) NOT NULL, language VARCHAR(20), "
                    "interval_seconds INTEGER, enabled BOOLEAN, next_run_at DATETIME, "
                    "health VARCHAR(30), consecutive_failures INTEGER, "
                    "last_run_at DATETIME, last_success_at DATETIME, last_error TEXT, "
                    "created_at DATETIME, updated_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO collection_targets "
                    "(id, name, url, language, interval_seconds, enabled, health) "
                    "VALUES ('legacy-target', 'Legacy page', "
                    "'https://example.org/status', 'en', 3600, 1, 'healthy')"
                )
            )

        ensure_compatible_schema()
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("collection_targets")
        }
        self.assertIn("target_type", columns)
        with engine.begin() as connection:
            self.assertEqual(
                connection.execute(
                    text(
                        "SELECT target_type FROM collection_targets "
                        "WHERE id='legacy-target'"
                    )
                ).scalar(),
                "web_page",
            )

    def test_fetch_rejects_non_text_and_oversized_responses(self):
        class BufferedResponse:
            status_code = 200
            encoding = "utf-8"

            def __init__(self, content: bytes, content_type: str):
                self.content = content
                self.text = content.decode("utf-8", errors="replace")
                self.headers = {
                    "content-type": content_type,
                    "content-length": str(len(content)),
                }

            def raise_for_status(self):
                return None

        class BufferedClient:
            response: BufferedResponse

            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, *_):
                return self.response

        with patch(
            "pldr_api.importers.validate_public_http_url", side_effect=lambda url: url
        ), patch("pldr_api.importers.httpx.AsyncClient", BufferedClient):
            BufferedClient.response = BufferedResponse(b"binary", "application/octet-stream")
            with self.assertRaises(UnsupportedContentTypeError):
                asyncio.run(fetch_public_text_response("https://example.org/file", max_bytes=64))

            BufferedClient.response = BufferedResponse(b"x" * 65, "text/plain")
            with self.assertRaises(ResponseTooLargeError):
                asyncio.run(fetch_public_text_response("https://example.org/large", max_bytes=64))

        class StreamingResponse:
            status_code = 200
            encoding = "utf-8"
            headers = {"content-type": "text/html"}

            def raise_for_status(self):
                return None

            async def aiter_bytes(self, **_):
                yield b"x" * 40
                yield b"y" * 40
                raise AssertionError("stream should stop immediately after crossing the limit")

        class StreamContext:
            async def __aenter__(self):
                return StreamingResponse()

            async def __aexit__(self, *_):
                return False

        class StreamingClient:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def stream(self, *_):
                return StreamContext()

        with patch(
            "pldr_api.importers.validate_public_http_url", side_effect=lambda url: url
        ), patch("pldr_api.importers.httpx.AsyncClient", StreamingClient):
            with self.assertRaises(ResponseTooLargeError):
                asyncio.run(fetch_public_text_response("https://example.org/stream", max_bytes=64))

            class CompressedStreamingResponse(StreamingResponse):
                headers = {"content-type": "text/html", "content-encoding": "gzip"}

                async def aiter_bytes(self, **_):
                    raise AssertionError("compressed bytes must be rejected before decoding")
                    yield b""  # pragma: no cover

            class CompressedStreamContext(StreamContext):
                async def __aenter__(self):
                    return CompressedStreamingResponse()

            class CompressedStreamingClient(StreamingClient):
                def stream(self, *_):
                    return CompressedStreamContext()

            with patch("pldr_api.importers.httpx.AsyncClient", CompressedStreamingClient):
                with self.assertRaises(UnsupportedContentEncodingError):
                    asyncio.run(
                        fetch_public_text_response("https://example.org/compressed", max_bytes=64)
                    )

        class SlowStreamingResponse(StreamingResponse):
            async def aiter_bytes(self, **_):
                await asyncio.sleep(0.05)
                yield b"eventually"

        class SlowStreamContext(StreamContext):
            async def __aenter__(self):
                return SlowStreamingResponse()

        class SlowStreamingClient(StreamingClient):
            def stream(self, *_):
                return SlowStreamContext()

        with patch(
            "pldr_api.importers.validate_public_http_url", side_effect=lambda url: url
        ), patch("pldr_api.importers.httpx.AsyncClient", SlowStreamingClient):
            with self.assertRaises(httpx.TimeoutException):
                asyncio.run(
                    fetch_public_text_response(
                        "https://example.org/slow-stream",
                        max_bytes=64,
                        total_timeout_seconds=0.01,
                    )
                )

    def test_candidate_failure_is_visible_without_turning_capture_into_failure(self):
        before = self.formal_counts()
        created = self.create_target(run_immediately=True)
        body = (
            "The controlled bulletin was captured successfully, while this test forces "
            "only the downstream model candidate generation step to fail visibly."
        )
        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(return_value=self.fetched(body)),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(side_effect=RuntimeError("controlled model outage")),
        ):
            run = asyncio.run(run_once(worker_id="candidate-failure-worker"))
        assert run is not None
        self.assertEqual((run.status, run.outcome), ("succeeded", "baseline"))
        with SessionLocal() as session:
            item = session.get(IntakeItem, run.current_intake_item_id)
            assert item is not None
            self.assertEqual(item.status, "generation_failed")
            self.assertIn("controlled model outage", item.candidate_error)
        detail = self.client.get(
            f"/pldr-api/v1/collection/targets/{created['target']['id']}"
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["versions"][0]["intake"]["status"], "generation_failed")
        self.assertEqual(self.client.get("/pldr-api/v1/collection/summary").json()["pending_review"], 1)
        self.assertEqual(self.formal_counts(), before)

    def test_paused_queue_overdue_health_and_inflight_completion_are_truthful(self):
        created = self.create_target(run_immediately=True)
        target_id = created["target"]["id"]
        paused = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/pause")
        self.assertEqual(paused.status_code, 200, paused.text)
        summary = self.client.get("/pldr-api/v1/collection/summary").json()
        self.assertEqual(summary["runs"]["queued"], 0)
        self.assertEqual(summary["targets"]["paused"], 1)
        self.assertEqual(self.client.get(f"/pldr-api/v1/collection/targets/{target_id}").json()["health"], "paused")

        resumed = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/resume")
        self.assertEqual(resumed.status_code, 200, resumed.text)
        with SessionLocal() as session:
            target = session.get(CollectionTarget, target_id)
            assert target is not None
            target.health = "healthy"
            target.next_run_at = utcnow() - timedelta(minutes=10)
            session.commit()
        stale = self.client.get(f"/pldr-api/v1/collection/targets/{target_id}").json()
        self.assertTrue(stale["overdue"])
        self.assertEqual(stale["health"], "stale")
        summary = self.client.get("/pldr-api/v1/collection/summary").json()
        self.assertEqual(summary["targets"]["stale"], 1)
        self.assertEqual(summary["targets"]["healthy"], 0)

        with SessionLocal() as session:
            target = session.get(CollectionTarget, target_id)
            assert target is not None
            target.health = "error"
            target.consecutive_failures = 3
            session.commit()
        failed_and_overdue = self.client.get(
            f"/pldr-api/v1/collection/targets/{target_id}"
        ).json()
        self.assertTrue(failed_and_overdue["overdue"])
        self.assertEqual(failed_and_overdue["health"], "error")

        with SessionLocal() as session:
            target = session.get(CollectionTarget, target_id)
            assert target is not None
            target.health = "healthy"
            target.consecutive_failures = 0
            claimed = claim_next_run(session, worker_id="inflight-worker", lease_seconds=30)
            assert claimed is not None
            run_id = claimed.id
        self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/pause")
        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(
                return_value=self.fetched(
                    "The already claimed bulletin completes after pause and keeps the "
                    "target effectively paused rather than repainting it green."
                )
            ),
        ):
            completed = asyncio.run(execute_claimed_run(run_id))
        self.assertEqual(completed.status, "succeeded")
        with SessionLocal() as session:
            target = session.get(CollectionTarget, target_id)
            assert target is not None
            self.assertFalse(target.enabled)
            self.assertEqual(target.health, "paused")

        # Reverse the ordering: execute reads the paused target, then an analyst
        # resumes it while the network request is in flight. Final health must be
        # derived from the current DB row, not the worker's stale identity map.
        self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/resume")
        queued = self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/run")
        self.assertEqual(queued.status_code, 200, queued.text)
        with SessionLocal() as session:
            second_claim = claim_next_run(
                session, worker_id="resume-race-worker", lease_seconds=30
            )
            assert second_claim is not None
            second_run_id = second_claim.id
        self.client.post(f"/pldr-api/v1/collection/targets/{target_id}/pause")

        async def resume_during_fetch(_url: str):
            resumed = self.client.post(
                f"/pldr-api/v1/collection/targets/{target_id}/resume"
            )
            self.assertEqual(resumed.status_code, 200, resumed.text)
            return self.fetched(
                "The target is resumed while this already claimed request is in flight, "
                "so its final recorded health must match the enabled database row."
            )

        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(side_effect=resume_during_fetch),
        ):
            completed_after_resume = asyncio.run(execute_claimed_run(second_run_id))
        self.assertEqual(completed_after_resume.status, "succeeded")
        with SessionLocal() as session:
            target = session.get(CollectionTarget, target_id)
            assert target is not None
            self.assertTrue(target.enabled)
            self.assertEqual(target.health, "healthy")

    def test_versions_survive_recent_run_window_and_histories_are_pageable(self):
        created = self.create_target(run_immediately=True)
        target_id = created["target"]["id"]
        baseline = self.run_with(
            self.fetched(
                "The durable baseline remains addressable after many later unchanged "
                "checks have rolled out of the recent execution window."
            )
        )
        base_time = utcnow()
        with SessionLocal() as session:
            for index in range(60):
                session.add(
                    CollectionRun(
                        id=f"col_run_history_{index:03d}",
                        target_id=target_id,
                        status="succeeded",
                        outcome="unchanged",
                        trigger="scheduled",
                        attempt_number=1,
                        queued_at=base_time + timedelta(seconds=index + 1),
                        started_at=base_time + timedelta(seconds=index + 1),
                        completed_at=base_time + timedelta(seconds=index + 1),
                        duration_ms=1,
                        body_hash=baseline.body_hash,
                        raw_hash=baseline.raw_hash,
                        version_number=1,
                        previous_intake_item_id=baseline.current_intake_item_id,
                        current_intake_item_id=baseline.current_intake_item_id,
                    )
                )
            session.commit()

        detail = self.client.get(
            f"/pldr-api/v1/collection/targets/{target_id}?run_limit=10&version_limit=1"
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        self.assertEqual(payload["run_count"], 61)
        self.assertEqual(len(payload["runs"]), 10)
        self.assertTrue(payload["runs_truncated"])
        self.assertEqual(payload["version_count"], 1)
        self.assertEqual(payload["versions"][0]["id"], baseline.id)

        run_page = self.client.get(
            f"/pldr-api/v1/collection/targets/{target_id}/runs?offset=10&limit=50"
        )
        self.assertEqual(run_page.status_code, 200, run_page.text)
        self.assertEqual(run_page.json()["count"], 61)
        self.assertEqual(len(run_page.json()["items"]), 50)
        version_page = self.client.get(
            f"/pldr-api/v1/collection/targets/{target_id}/versions?offset=0&limit=10"
        )
        self.assertEqual(version_page.status_code, 200, version_page.text)
        self.assertEqual(version_page.json()["count"], 1)
        self.assertEqual(version_page.json()["items"][0]["id"], baseline.id)

    def test_large_diff_is_bounded_and_disclosed(self):
        created = self.create_target(run_immediately=False)
        target_id = created["target"]["id"]
        old_body = " ".join(f"old{index}." for index in range(4_500))
        new_body = " ".join(f"new{index}." for index in range(4_500))
        with SessionLocal() as session:
            first = asyncio.run(
                submit_web_intake(
                    session,
                    "https://updates.example.org/status",
                    "Controlled status page",
                    "Large V1",
                    f"<html><body><article>{old_body}</article></body></html>",
                    "en",
                    input_type="collection",
                )
            )
            second = asyncio.run(
                submit_web_intake(
                    session,
                    "https://updates.example.org/status",
                    "Controlled status page",
                    "Large V2",
                    f"<html><body><article>{new_body}</article></body></html>",
                    "en",
                    input_type="collection",
                )
            )
            run = CollectionRun(
                id="col_run_large_diff",
                target_id=target_id,
                status="succeeded",
                outcome="changed",
                trigger="manual",
                queued_at=utcnow(),
                started_at=utcnow(),
                completed_at=utcnow(),
                duration_ms=1,
                version_number=2,
                previous_intake_item_id=first.id,
                current_intake_item_id=second.id,
                body_hash=second.extracted_hash,
                raw_hash=second.raw_hash,
            )
            session.add(run)
            session.commit()
        response = self.client.get("/pldr-api/v1/collection/runs/col_run_large_diff/diff")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["truncated"]["exact_word_diff"])
        self.assertTrue(payload["truncated"]["unified_diff"])
        self.assertEqual(payload["truncated"]["limit_lines"], 2_000)
        self.assertLessEqual(len(payload["unified_diff"].splitlines()), 2_000)
        self.assertLessEqual(len(payload["added_text"]), 120_040)
        self.assertEqual(payload["current"]["intake_item_id"], second.id)

    def test_collection_frontend_and_deployment_contract(self):
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        for marker in [
            'id="btn-collection"',
            'id="collection-modal"',
            'id="collection-target-type"',
            "P1 SLICE",
            "监测配置和运行记录不是正式 Source/Evidence",
            "RSS 条目保存来源提供的标题和摘要快照，不冒充已抓取原文",
        ]:
            self.assertIn(marker, dashboard.text)
        script = self.client.get("/assets/app.js").text
        for marker in [
            "/pldr-api/v1/collection/targets",
            "/pldr-api/v1/collection/runs/",
            "/pldr-api/v1/collection/targets/",
            "target_type",
            "discovered_items",
            "RSS 监测条目",
            'data-collection-action="more-runs"',
            'data-collection-action="more-versions"',
            "collectionDiffRequestSerial",
            "当前为有界差异视图",
            "逾期未采集",
            "打开版本材料",
            "scheduleCollectionPoll(collectionRetryDelayMs)",
        ]:
            self.assertIn(marker, script)
        styles = self.client.get("/assets/styles.css").text
        responsive_start = styles.index("@media (max-width: 1180px)")
        responsive_end = styles.index("@media (max-width: 900px)")
        responsive_band = styles[responsive_start:responsive_end]
        for marker in [
            ".collection-command { grid-template-columns: 1fr; }",
            ".collection-source-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
            ".collection-detail-grid { grid-template-columns: 1fr; }",
        ]:
            self.assertIn(marker, responsive_band)
        dockerignore = (Path(__file__).resolve().parents[1] / ".dockerignore").read_text()
        for protected in [".env", ".venv", "data/runtime", "reports", ".git"]:
            self.assertIn(protected, dockerignore)

    def test_collection_target_rejects_credentials_and_blank_names(self):
        with patch(
            "pldr_api.collection_routes.validate_public_http_url",
            wraps=__import__("pldr_api.security", fromlist=["validate_public_http_url"]).validate_public_http_url,
        ):
            credentialed = self.client.post(
                "/pldr-api/v1/collection/targets",
                json={
                    "name": "Credential leak",
                    "url": "https://user:secret@93.184.216.34/status",
                },
            )
        self.assertEqual(credentialed.status_code, 400, credentialed.text)
        blank = self.client.post(
            "/pldr-api/v1/collection/targets",
            json={"name": "   ", "url": "https://example.org/status"},
        )
        self.assertEqual(blank.status_code, 422, blank.text)


if __name__ == "__main__":
    unittest.main()
