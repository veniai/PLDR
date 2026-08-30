from __future__ import annotations

import asyncio
import os
import socket
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

INVESTIGATION_TEST_ROOT = Path(tempfile.mkdtemp(prefix="pldr-investigation-tests-"))
os.environ["PLDR_DATABASE_URL"] = f"sqlite:///{INVESTIGATION_TEST_ROOT / 'investigations.db'}"
os.environ["PLDR_REPORT_DIR"] = str(INVESTIGATION_TEST_ROOT / "reports")
os.environ.pop("LLM_API_KEY", None)

from fastapi.testclient import TestClient
from sqlalchemy import func, select


class InvestigationWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # unittest discovery imports this file before test_p0.py.  Import PLDR
        # lazily so test_p0 can choose the process-global test database first in
        # the full suite, while this file still owns its temp DB when run alone.
        global Base, SessionLocal, engine, app
        global enqueue_target_run, run_once, FetchedPublicText
        global DEMO_INVESTIGATION_ID, UNCLASSIFIED_INVESTIGATION_ID
        global bootstrap_legacy_investigations, recover_expired_review_task_leases
        global run_review_task_once, CollectionTarget, DecisionLog, Event, IntakeItem
        global InvestigationLink, ReviewTask, SearchQueryRun, SearchResult
        global BackendSearchResponse, SearchHit, seed_database

        from pldr_api.collection import enqueue_target_run, run_once
        from pldr_api.database import Base, SessionLocal, engine
        from pldr_api.importers import FetchedPublicText
        from pldr_api.investigations import (
            DEMO_INVESTIGATION_ID,
            UNCLASSIFIED_INVESTIGATION_ID,
            bootstrap_legacy_investigations,
            recover_expired_review_task_leases,
            run_review_task_once,
        )
        from pldr_api.main import app
        from pldr_api.models import (
            CollectionTarget,
            DecisionLog,
            Event,
            IntakeItem,
            InvestigationLink,
            ReviewTask,
            SearchQueryRun,
            SearchResult,
        )
        from pldr_api.search import BackendSearchResponse, SearchHit
        from pldr_api.seed import seed_database

        database_path = Path(str(engine.url.database)).resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        cls.owns_test_root = INVESTIGATION_TEST_ROOT in database_path.parents
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        if cls.owns_test_root:
            engine.dispose()
            shutil.rmtree(INVESTIGATION_TEST_ROOT, ignore_errors=True)

    def setUp(self):
        database_path = Path(str(engine.url.database)).resolve()
        if not any(part.startswith("pldr-") and "test" in part for part in database_path.parts):
            self.fail(f"Refusing to reset non-test database: {database_path}")
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    @staticmethod
    def fetched(url: str, marker: str = "baseline") -> FetchedPublicText:
        body = (
            f"Public monitoring dispatch {marker}. This exact page contains enough durable "
            "text for extraction, candidate review, and independent worker verification."
        )
        html = f"<html><head><title>{marker}</title></head><body><article><p>{body}</p></article></body></html>"
        return FetchedPublicText(url, html, 200, "text/html", len(html.encode()))

    @staticmethod
    def add_search_results(count: int = 2) -> list[str]:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            session.add(
                SearchQueryRun(
                    id="query_test",
                    keyword="test dispatch",
                    normalized_keyword="test dispatch",
                    scope="web",
                    provider="unit",
                    channel="unit:web",
                    language="en",
                    status="ok",
                    result_count=count,
                    latency_ms=1,
                    created_at=now,
                )
            )
            ids = []
            for index in range(count):
                result_id = f"result_{index + 1}"
                ids.append(result_id)
                url = f"https://public.example.org/{index + 1}"
                session.add(
                    SearchResult(
                        id=result_id,
                        query_run_id="query_test",
                        result_fingerprint=(str(index + 1) * 64)[:64],
                        provider="unit",
                        channel="unit:web",
                        original_url=url,
                        canonical_url=url,
                        site_name="public.example.org",
                        title=f"Result {index + 1}",
                        snippet="Public result metadata only",
                        rank=index + 1,
                        engine="unit",
                        raw_result={},
                        created_at=now,
                    )
                )
            session.commit()
            return ids

    def create_investigation(self, title: str = "Port disruption") -> str:
        response = self.client.post(
            "/pldr-api/v1/investigations",
            json={"title": title, "question": "What changed and what supports it?"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_topic_titles_require_visible_characters(self):
        created = self.client.post(
            "/pldr-api/v1/investigations",
            json={"title": "   ", "question": "Should not be persisted"},
        )
        self.assertEqual(created.status_code, 422, created.text)

        investigation_id = self.create_investigation()
        updated = self.client.patch(
            f"/pldr-api/v1/investigations/{investigation_id}",
            json={"title": "   "},
        )
        self.assertEqual(updated.status_code, 422, updated.text)
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{investigation_id}"
            ).json()["title"],
            "Port disruption",
        )

    def test_search_context_and_selection_are_db_only_idempotent_and_nonempty(self):
        hit = SearchHit(
            original_url="https://public.example.org/search-hit",
            canonical_url="https://public.example.org/search-hit",
            fingerprint="a" * 64,
            site_name="public.example.org",
            title="Search hit",
            snippet="metadata",
            published_at=None,
            engine="unit",
            raw_result={},
        )
        with patch(
            "pldr_api.search.request_search",
            new=AsyncMock(return_value=BackendSearchResponse("unit", "unit:web", [hit])),
        ):
            searched = self.client.post(
                "/pldr-api/v1/search",
                json={
                    "keyword": "public port",
                    "scope": "web",
                    "limit": 5,
                    "new_investigation": {"title": "Atomic topic", "question": "Why?"},
                },
            )
        self.assertEqual(searched.status_code, 200, searched.text)
        investigation_id = searched.json()["investigation_id"]
        result_id = searched.json()["results"][0]["id"]

        with patch(
            "pldr_api.investigations.fetch_public_text_response",
            new=AsyncMock(side_effect=AssertionError("selection must not fetch")),
        ) as fetch_mock, patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(side_effect=AssertionError("selection must not call a model")),
        ) as model_mock:
            first = self.client.post(
                "/pldr-api/v1/search/select",
                json={
                    "result_ids": [result_id],
                    "investigation_id": investigation_id,
                    "request_id": "request-atomic-001",
                },
            )
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(fetch_mock.await_count, 0)
        self.assertEqual(model_mock.await_count, 0)
        self.assertEqual(len(first.json()["tasks"]), 1)
        self.assertEqual(first.json()["tasks"][0]["status"], "queued")

        replay = self.client.post(
            "/pldr-api/v1/search/select",
            json={
                "result_ids": [result_id],
                "investigation_id": investigation_id,
                "request_id": "request-atomic-001",
            },
        )
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertEqual(replay.json()["batch"]["id"], first.json()["batch"]["id"])

        deduplicated = self.client.post(
            "/pldr-api/v1/search/select",
            json={
                "result_ids": [result_id],
                "investigation_id": investigation_id,
                "request_id": "request-atomic-002",
            },
        )
        self.assertEqual(deduplicated.status_code, 202, deduplicated.text)
        self.assertEqual(len(deduplicated.json()["tasks"]), 1)
        self.assertEqual(
            deduplicated.json()["tasks"][0]["id"], first.json()["tasks"][0]["id"]
        )
        unclassified = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": [result_id], "request_id": "request-unclassified-001"},
        )
        self.assertEqual(unclassified.status_code, 202, unclassified.text)
        self.assertEqual(
            unclassified.json()["investigation"]["id"], UNCLASSIFIED_INVESTIGATION_ID
        )
        self.assertEqual(unclassified.json()["investigation"]["kind"], "system")

    def test_worker_isolates_failures_and_model_error_has_reviewable_retryable_fallback(self):
        result_ids = self.add_search_results(2)
        investigation_id = self.create_investigation()
        queued = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": result_ids, "investigation_id": investigation_id},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        task_ids = [task["id"] for task in queued.json()["tasks"]]
        good_fetch = self.fetched("https://public.example.org/2", "second")
        with patch(
            "pldr_api.investigations.fetch_public_text_response",
            new=AsyncMock(side_effect=[RuntimeError("first fetch failed"), good_fetch]),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            asyncio.run(run_review_task_once(worker_id="review-worker"))
            asyncio.run(run_review_task_once(worker_id="review-worker"))

        first = self.client.get(f"/pldr-api/v1/tasks/{task_ids[0]}").json()
        second = self.client.get(f"/pldr-api/v1/tasks/{task_ids[1]}").json()
        self.assertEqual(first["status"], "failed")
        self.assertEqual(second["status"], "ready")
        terminal_replay = self.client.post(
            "/pldr-api/v1/search/select",
            json={
                "result_ids": [result_ids[1]],
                "investigation_id": investigation_id,
                "request_id": "terminal-replay-001",
            },
        )
        self.assertEqual(terminal_replay.status_code, 202, terminal_replay.text)
        self.assertEqual(terminal_replay.json()["tasks"][0]["id"], task_ids[1])
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{investigation_id}/tasks"
            ).json()["count"],
            2,
        )

        retried = self.client.post(
            f"/pldr-api/v1/tasks/{task_ids[0]}/retry", json={"actor": "tester"}
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        first_fetch = self.fetched("https://public.example.org/1", "first")
        with patch(
            "pldr_api.investigations.fetch_public_text_response",
            new=AsyncMock(return_value=first_fetch),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(side_effect=TimeoutError("model timeout")),
        ):
            asyncio.run(run_review_task_once(worker_id="review-worker"))
        fallback = self.client.get(f"/pldr-api/v1/tasks/{task_ids[0]}").json()
        self.assertEqual(fallback["status"], "ready")
        self.assertTrue(fallback["fallback_used"])
        self.assertTrue(fallback["retryable"])
        self.assertIn("model timeout", fallback["error"]["message"])

        retry_ai = self.client.post(
            f"/pldr-api/v1/tasks/{task_ids[0]}/retry", json={"actor": "tester"}
        )
        self.assertEqual(retry_ai.status_code, 200, retry_ai.text)
        with patch(
            "pldr_api.investigations.fetch_public_text_response",
            new=AsyncMock(side_effect=AssertionError("AI-only retry must reuse snapshot")),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            asyncio.run(run_review_task_once(worker_id="review-worker"))
        recovered = self.client.get(f"/pldr-api/v1/tasks/{task_ids[0]}").json()
        self.assertEqual(recovered["status"], "ready")
        self.assertFalse(recovered["fallback_used"])
        self.assertTrue(recovered["degraded"])
        self.assertEqual(recovered["degradation"]["code"], "rule_fallback")
        self.assertIsNone(recovered["error"])

        confirmed_topic = self.create_investigation("Confirmed material reused M:N")
        with SessionLocal() as session:
            item = session.get(IntakeItem, second["intake_item_id"])
            now = datetime.now(timezone.utc)
            session.add(
                Event(
                    id="confirmed_reuse_event",
                    title="Confirmed reuse event",
                    summary="A formally confirmed event used to test cross-topic membership.",
                    event_type="incident",
                    # Human confirmation persists this sentinel when no event
                    # time is known; the API contract must expose it as null.
                    start_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
                    location_name="",
                    importance="medium",
                    status="confirmed",
                    confidence=0.8,
                    metadata_json={"start_at_known": False},
                    created_at=now,
                    updated_at=now,
                )
            )
            item.status = "confirmed"
            item.final_event_id = "confirmed_reuse_event"
            session.commit()
        confirmed_reuse = self.client.post(
            "/pldr-api/v1/search/select",
            json={
                "result_ids": [result_ids[1]],
                "investigation_id": confirmed_topic,
                "request_id": "confirmed-reuse-001",
            },
        )
        self.assertEqual(confirmed_reuse.status_code, 202, confirmed_reuse.text)
        self.assertEqual(confirmed_reuse.json()["tasks"][0]["status"], "confirmed")
        confirmed_detail = self.client.get(
            f"/pldr-api/v1/investigations/{confirmed_topic}"
        ).json()
        self.assertEqual(
            [event["id"] for event in confirmed_detail["events"]],
            ["confirmed_reuse_event"],
        )
        self.assertIsNone(confirmed_detail["events"][0]["start_at"])

    def test_expired_lease_is_recovered_and_logged(self):
        result_id = self.add_search_results(1)[0]
        investigation_id = self.create_investigation()
        response = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": [result_id], "investigation_id": investigation_id},
        )
        task_id = response.json()["tasks"][0]["id"]
        with SessionLocal() as session:
            task = session.get(ReviewTask, task_id)
            task.status = "generating"
            task.lease_owner = "dead-worker"
            task.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            session.commit()
            self.assertEqual(recover_expired_review_task_leases(session), 1)
            session.refresh(task)
            self.assertEqual(task.status, "queued")
            self.assertEqual(task.lease_recoveries, 1)
            actions = list(
                session.scalars(
                    select(DecisionLog.action).where(DecisionLog.task_id == task_id)
                )
            )
            self.assertIn("task.lease_recovered", actions)

    def test_migration_is_idempotent_preserves_rows_and_backfills_inbox(self):
        with SessionLocal() as session:
            seed_database(session, force=True)
            now = datetime.now(timezone.utc)
            for item_id, status_value in (
                ("legacy_ready", "candidate_ready"),
                ("legacy_failed", "generation_failed"),
                ("legacy_parsed", "parsed"),
            ):
                session.add(
                    IntakeItem(
                        id=item_id,
                        input_type="text",
                        status=status_value,
                        error="legacy error" if status_value == "generation_failed" else None,
                        source_description="Legacy source",
                        language="en",
                        raw_snapshot="Legacy public material with enough persisted text for safe recovery.",
                        raw_hash=item_id,
                        extracted_snapshot="Legacy public material with enough persisted text for safe recovery.",
                        extracted_hash=item_id,
                        review={},
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.add(
                CollectionTarget(
                    id="legacy_target",
                    name="Legacy target",
                    url="https://public.example.org/legacy",
                    language="en",
                    interval_seconds=3600,
                    enabled=True,
                    health="new",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            before = session.scalar(select(func.count()).select_from(IntakeItem))
            bootstrap_legacy_investigations(session)
            task_count = session.scalar(select(func.count()).select_from(ReviewTask))
            bootstrap_legacy_investigations(session)
            self.assertEqual(
                task_count, session.scalar(select(func.count()).select_from(ReviewTask))
            )
            self.assertEqual(before, session.scalar(select(func.count()).select_from(IntakeItem)))
            statuses = {
                task.intake_item_id: task.status
                for task in session.scalars(
                    select(ReviewTask).where(
                        ReviewTask.investigation_id == UNCLASSIFIED_INVESTIGATION_ID
                    )
                )
            }
            self.assertEqual(statuses["legacy_ready"], "ready")
            self.assertEqual(statuses["legacy_failed"], "failed")
            self.assertEqual(statuses["legacy_parsed"], "queued")
            target_link = session.scalar(
                select(InvestigationLink).where(
                    InvestigationLink.investigation_id == UNCLASSIFIED_INVESTIGATION_ID,
                    InvestigationLink.object_type == "collection_target",
                    InvestigationLink.object_id == "legacy_target",
                )
            )
            self.assertIsNotNone(target_link)
            demo_event_ids = set(
                session.scalars(
                    select(InvestigationLink.object_id).where(
                        InvestigationLink.investigation_id == DEMO_INVESTIGATION_ID,
                        InvestigationLink.object_type == "event",
                    )
                )
            )
            metadata_demo_events = {
                event.id
                for event in session.scalars(select(Event))
                if any(
                    bool((link.document.metadata_json or {}).get("demo"))
                    for link in event.document_links
                )
            }
            self.assertEqual(demo_event_ids, metadata_demo_events)
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{DEMO_INVESTIGATION_ID}"
            ).json()["kind"],
            "demo",
        )
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{UNCLASSIFIED_INVESTIGATION_ID}"
            ).json()["kind"],
            "system",
        )

    def test_manual_and_collection_intakes_become_topic_tasks_but_unchanged_does_not(self):
        investigation_id = self.create_investigation("Collection topic")
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            session.add(
                IntakeItem(
                    id="manual_intake",
                    input_type="text",
                    status="parsed",
                    source_description="Manual public note",
                    language="en",
                    raw_snapshot="Manual persisted text long enough for later candidate generation.",
                    raw_hash="manual-raw",
                    extracted_snapshot="Manual persisted text long enough for later candidate generation.",
                    extracted_hash="manual-body",
                    review={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        linked = self.client.post(
            f"/pldr-api/v1/investigations/{investigation_id}/links",
            json={"object_type": "intake", "object_id": "manual_intake"},
        )
        self.assertEqual(linked.status_code, 201, linked.text)
        self.assertEqual(linked.json()["review_task"]["task"]["status"], "queued")
        linked_again = self.client.post(
            f"/pldr-api/v1/investigations/{investigation_id}/links",
            json={"object_type": "intake", "object_id": "manual_intake"},
        )
        self.assertFalse(linked_again.json()["review_task"]["created"])
        manual_task_id = linked.json()["review_task"]["task"]["id"]
        with patch(
            "pldr_api.investigations.fetch_public_text_response",
            new=AsyncMock(side_effect=AssertionError("generation-only task must not fetch")),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            asyncio.run(run_review_task_once(worker_id="review-worker"))
        self.assertEqual(
            self.client.get(f"/pldr-api/v1/tasks/{manual_task_id}").json()["status"],
            "ready",
        )
        rejected = self.client.post(
            "/pldr-api/v1/intake/manual_intake/reject",
            json={"analyst": "tester", "reason": "Not relevant to this investigation"},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(
            self.client.get(f"/pldr-api/v1/tasks/{manual_task_id}").json()["status"],
            "rejected",
        )
        actions = self.client.get(
            f"/pldr-api/v1/investigations/{investigation_id}/activity"
        ).json()["items"]
        self.assertIn("intake.rejected", {entry["action"] for entry in actions})

        with patch(
            "pldr_api.security.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ],
        ):
            target = self.client.post(
                "/pldr-api/v1/collection/targets",
                json={
                    "name": "Topic monitor",
                    "url": "https://public.example.org/status",
                    "investigation_id": investigation_id,
                },
            )
        self.assertEqual(target.status_code, 201, target.text)
        target_id = target.json()["target"]["id"]
        with SessionLocal() as session:
            enqueue_target_run(session, session.get(CollectionTarget, target_id), trigger="manual")
        fetched = self.fetched("https://public.example.org/status")
        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(return_value=fetched),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            first_run = asyncio.run(run_once(worker_id="collection-worker"))
        self.assertEqual(first_run.outcome, "baseline")
        tasks_after_baseline = self.client.get(
            f"/pldr-api/v1/investigations/{investigation_id}/tasks"
        ).json()["items"]
        collection_tasks = [
            task for task in tasks_after_baseline if task["payload"].get("target_id") == target_id
        ]
        self.assertEqual(len(collection_tasks), 1)
        self.assertEqual(collection_tasks[0]["status"], "ready")

        late_topic = self.create_investigation("Late-linked collection topic")
        late_link = self.client.post(
            f"/pldr-api/v1/investigations/{late_topic}/links",
            json={"object_type": "collection_target", "object_id": target_id},
        )
        self.assertEqual(late_link.status_code, 201, late_link.text)
        self.assertEqual(late_link.json()["version_tasks_created"], 1)
        late_tasks = self.client.get(
            f"/pldr-api/v1/investigations/{late_topic}/tasks"
        ).json()["items"]
        self.assertEqual(len(late_tasks), 1)
        self.assertEqual(late_tasks[0]["status"], "ready")

        with SessionLocal() as session:
            enqueue_target_run(session, session.get(CollectionTarget, target_id), trigger="manual")
        with patch(
            "pldr_api.collection.fetch_public_text_response",
            new=AsyncMock(return_value=fetched),
        ):
            second_run = asyncio.run(run_once(worker_id="collection-worker"))
        self.assertEqual(second_run.outcome, "unchanged")
        tasks_after_unchanged = self.client.get(
            f"/pldr-api/v1/investigations/{investigation_id}/tasks"
        ).json()["items"]
        self.assertEqual(len(tasks_after_unchanged), len(tasks_after_baseline))

    def test_report_scope_and_activity_are_strictly_investigation_derived(self):
        with SessionLocal() as session:
            seed_database(session, force=True)
            event_ids = list(session.scalars(select(Event.id).order_by(Event.id).limit(2)))
            now = datetime.now(timezone.utc)
            session.add(
                Event(
                    id="real_user_topic_event",
                    title="User-confirmed real event",
                    summary="A non-demo event used to verify truthful report labelling.",
                    event_type="incident",
                    start_at=now,
                    location_name="",
                    importance="medium",
                    status="confirmed",
                    confidence=0.8,
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        first_topic = self.create_investigation("First topic")
        second_topic = self.create_investigation("Second topic")
        for topic_id, event_id in zip((first_topic, second_topic), event_ids):
            linked = self.client.post(
                f"/pldr-api/v1/investigations/{topic_id}/links",
                json={"object_type": "event", "object_id": event_id},
            )
            self.assertEqual(linked.status_code, 201, linked.text)

        report = self.client.post(
            "/pldr-api/v1/reports", json={"investigation_id": first_topic}
        )
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()["event_ids"], [event_ids[0]])
        self.assertEqual(report.json()["event_count"], 1)
        self.assertEqual(
            self.client.get(f"/pldr-api/v1/investigations/{first_topic}").json()["kind"],
            "user",
        )
        report_html = self.client.get(report.json()["url"])
        self.assertEqual(report_html.status_code, 200)
        self.assertIn("当前为 P0 演示简报", report_html.text)
        real_topic = self.create_investigation("Pure real-material report")
        self.assertEqual(
            self.client.post(
                f"/pldr-api/v1/investigations/{real_topic}/links",
                json={"object_type": "event", "object_id": "real_user_topic_event"},
            ).status_code,
            201,
        )
        real_report = self.client.post(
            "/pldr-api/v1/reports", json={"investigation_id": real_topic}
        )
        self.assertEqual(real_report.status_code, 200, real_report.text)
        self.assertNotIn(
            "当前为 P0 演示简报",
            self.client.get(real_report.json()["url"]).text,
        )
        outside = self.client.post(
            "/pldr-api/v1/reports",
            json={"investigation_id": first_topic, "event_ids": [event_ids[1]]},
        )
        self.assertEqual(outside.status_code, 400, outside.text)
        activity = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/activity"
        ).json()["items"]
        report_logs = [entry for entry in activity if entry["action"] == "report.generated"]
        self.assertEqual(report_logs[0]["detail"]["event_ids"], [event_ids[0]])
        self.assertEqual(report_logs[0]["detail"]["url"], report.json()["url"])
        refreshed = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}"
        ).json()
        self.assertEqual(refreshed["reports"][0]["url"], report.json()["url"])
        self.assertEqual(refreshed["reports"][0]["event_ids"], [event_ids[0]])


if __name__ == "__main__":
    unittest.main()
