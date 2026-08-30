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

    @staticmethod
    def confirmation_request(item: dict) -> dict:
        candidates = {
            candidate["object_type"]: candidate for candidate in item["candidates"]
        }
        event = candidates["event"]["machine"]["fields"]
        claim = candidates["claim"]
        evidence = candidates["evidence"]
        return {
            "disposition": "create",
            "analyst": "concurrency-reviewer",
            "merge_event_id": None,
            "event": {
                "title": event.get("title") or "Concurrent scope review",
                "summary": event.get("summary") or "Human-confirmed scope race fixture.",
                "event_type": "incident",
                "start_at": None,
                "location_name": "",
                "importance": "medium",
            },
            "entities": [],
            "claims": [
                {
                    "candidate_key": claim["candidate_key"],
                    "action": "create",
                    "text": claim["machine"]["fields"]["text"],
                    "status": "unverified",
                    "confidence": 0.6,
                    "temporal_scope": "",
                    "merge_claim_id": None,
                }
            ],
            "evidence": [
                {
                    "candidate_key": evidence["candidate_key"],
                    "action": "include",
                    "snippet": evidence["machine"]["fields"]["snippet"],
                    "stance": "supports",
                    "strength": 0.8,
                    "note": "",
                }
            ],
        }

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

    def test_worker_preserves_rejection_committed_after_candidate_generation(self):
        from pldr_api.intake import reject_intake
        from pldr_api.investigations import (
            claim_next_review_task,
            execute_claimed_review_task,
            lock_intake_for_mutation,
        )
        from pldr_api.models import SearchSelection, SearchSelectionEvent

        result_id = self.add_search_results(1)[0]
        investigation_id = self.create_investigation("Post-generation decision race")
        queued = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": [result_id], "investigation_id": investigation_id},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        task_id = queued.json()["tasks"][0]["id"]
        intake_item_id = queued.json()["tasks"][0]["intake_item_id"]
        with SessionLocal() as session:
            claimed = claim_next_review_task(
                session, worker_id="post-generation-race-worker"
            )
            assert claimed is not None
            self.assertEqual(claimed.id, task_id)
            selection_id = claimed.selection_id
            assert selection_id is not None
            event_count_before = session.scalar(
                select(func.count())
                .select_from(SearchSelectionEvent)
                .where(SearchSelectionEvent.selection_id == selection_id)
            )

        rejection_committed = False

        def reject_before_worker_final_fence(session, item_id, *, action):
            nonlocal rejection_committed
            if action == "finalizing its review task" and not rejection_committed:
                rejection_committed = True
                with SessionLocal() as concurrent_session:
                    concurrent_item = concurrent_session.get(IntakeItem, item_id)
                    assert concurrent_item is not None
                    rejected = reject_intake(
                        concurrent_session,
                        concurrent_item,
                        "post-generation-analyst",
                        "Analyst rejected after candidate generation committed",
                    )
                    review = dict(rejected.review or {})
                    review["post_generation_decision"] = "must survive worker finalization"
                    rejected.review = review
                    concurrent_session.commit()
            return lock_intake_for_mutation(session, item_id, action=action)

        with patch(
            "pldr_api.investigations.fetch_public_text_response",
            new=AsyncMock(
                return_value=self.fetched(
                    "https://public.example.org/1", "post-generation-race"
                )
            ),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ), patch(
            "pldr_api.investigations.lock_intake_for_mutation",
            new=reject_before_worker_final_fence,
        ):
            completed = asyncio.run(execute_claimed_review_task(task_id))

        self.assertTrue(rejection_committed)
        self.assertEqual(completed.status, "rejected")
        with SessionLocal() as session:
            item = session.get(IntakeItem, intake_item_id)
            task = session.get(ReviewTask, task_id)
            selection = session.get(SearchSelection, selection_id)
            assert item is not None and task is not None and selection is not None
            self.assertEqual(item.status, "rejected")
            self.assertEqual(item.disposition, "reject")
            self.assertEqual(item.reviewed_by, "post-generation-analyst")
            self.assertEqual(
                item.review.get("post_generation_decision"),
                "must survive worker finalization",
            )
            self.assertTrue(item.candidates)
            self.assertTrue(
                all(candidate.disposition == "rejected" for candidate in item.candidates)
            )
            self.assertEqual(task.status, "rejected")
            self.assertEqual(selection.status, "rejected")
            self.assertEqual(selection.outcome, "rejected")
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(SearchSelectionEvent)
                    .where(SearchSelectionEvent.selection_id == selection_id)
                ),
                event_count_before,
            )

    def test_worker_preserves_rejection_committed_during_model_await(self):
        from pldr_api.intake import reject_intake
        from pldr_api.investigations import (
            claim_next_review_task,
            execute_claimed_review_task,
        )
        from pldr_api.models import SearchSelection

        result_id = self.add_search_results(1)[0]
        investigation_id = self.create_investigation("Model-await decision race")
        queued = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": [result_id], "investigation_id": investigation_id},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        task_id = queued.json()["tasks"][0]["id"]
        intake_item_id = queued.json()["tasks"][0]["intake_item_id"]
        with SessionLocal() as session:
            claimed = claim_next_review_task(session, worker_id="model-await-race-worker")
            assert claimed is not None
            self.assertEqual(claimed.id, task_id)
            selection_id = claimed.selection_id
            assert selection_id is not None

        async def reject_while_model_is_awaited(*_args, **_kwargs):
            with SessionLocal() as concurrent_session:
                concurrent_item = concurrent_session.get(IntakeItem, intake_item_id)
                assert concurrent_item is not None
                reject_intake(
                    concurrent_session,
                    concurrent_item,
                    "model-await-analyst",
                    "Analyst rejected while the worker awaited the model",
                )
            return {"mode": "fallback"}

        with patch(
            "pldr_api.investigations.fetch_public_text_response",
            new=AsyncMock(
                return_value=self.fetched(
                    "https://public.example.org/1", "model-await-race"
                )
            ),
        ), patch(
            "pldr_api.intake.run_model_task",
            new=reject_while_model_is_awaited,
        ):
            completed = asyncio.run(execute_claimed_review_task(task_id))

        self.assertEqual(completed.status, "rejected")
        with SessionLocal() as session:
            item = session.get(IntakeItem, intake_item_id)
            task = session.get(ReviewTask, task_id)
            selection = session.get(SearchSelection, selection_id)
            assert item is not None and task is not None and selection is not None
            self.assertEqual(item.status, "rejected")
            self.assertEqual(item.disposition, "reject")
            self.assertEqual(item.reviewed_by, "model-await-analyst")
            self.assertEqual(
                item.rejection_reason,
                "Analyst rejected while the worker awaited the model",
            )
            self.assertEqual(task.status, "rejected")
            self.assertIsNone(task.error_class)
            self.assertEqual(selection.status, "rejected")
            self.assertEqual(selection.outcome, "rejected")
            self.assertEqual(len(item.candidates), 0)

    def test_worker_preserves_terminal_decisions_during_fetch_success_and_failure(self):
        from pldr_api.intake import archive_intake, reject_intake
        from pldr_api.investigations import (
            claim_next_review_task,
            execute_claimed_review_task,
        )
        from pldr_api.models import SearchSelection

        result_ids = self.add_search_results(3)
        investigation_id = self.create_investigation("Fetch decision races")
        queued = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": result_ids, "investigation_id": investigation_id},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        tasks = queued.json()["tasks"]

        for index, mode in enumerate(("success", "failure", "failure_archived")):
            task_id = tasks[index]["id"]
            intake_item_id = tasks[index]["intake_item_id"]
            with SessionLocal() as session:
                claimed = claim_next_review_task(
                    session, worker_id=f"fetch-{mode}-race-worker"
                )
                assert claimed is not None
                self.assertEqual(claimed.id, task_id)
                selection_id = claimed.selection_id
                assert selection_id is not None

            async def decide_during_fetch(url, *, _mode=mode):
                with SessionLocal() as concurrent_session:
                    concurrent_item = concurrent_session.get(
                        IntakeItem, intake_item_id
                    )
                    assert concurrent_item is not None
                    rejected = reject_intake(
                        concurrent_session,
                        concurrent_item,
                        f"fetch-{_mode}-analyst",
                        f"Analyst rejected while the {_mode} fetch was awaited",
                    )
                    if _mode == "failure_archived":
                        archive_intake(
                            concurrent_session,
                            rejected,
                            analyst="fetch-race-archiver",
                            reason="Hide the terminal decision before the fetch fails",
                        )
                if _mode != "success":
                    raise RuntimeError("Fetch failed after the analyst decision")
                return self.fetched(url, f"fetch-{_mode}-race")

            with patch(
                "pldr_api.investigations.fetch_public_text_response",
                new=decide_during_fetch,
            ), patch(
                "pldr_api.intake.run_model_task",
                new=AsyncMock(
                    side_effect=AssertionError(
                        "a superseded fetch must not start candidate generation"
                    )
                ),
            ):
                completed = asyncio.run(execute_claimed_review_task(task_id))

            self.assertEqual(completed.status, "rejected")
            with SessionLocal() as session:
                item = session.get(IntakeItem, intake_item_id)
                task = session.get(ReviewTask, task_id)
                selection = session.get(SearchSelection, selection_id)
                assert item is not None and task is not None and selection is not None
                self.assertEqual(item.status, "rejected")
                self.assertEqual(item.disposition, "reject")
                self.assertEqual(item.reviewed_by, f"fetch-{mode}-analyst")
                self.assertEqual(
                    item.rejection_reason,
                    f"Analyst rejected while the {mode} fetch was awaited",
                )
                self.assertEqual(item.raw_snapshot, "")
                self.assertEqual(item.extracted_snapshot, "")
                self.assertEqual(len(item.candidates), 0)
                self.assertEqual(item.archived_at is not None, mode == "failure_archived")
                self.assertEqual(task.status, "rejected")
                self.assertEqual(selection.status, "rejected")
                self.assertEqual(selection.outcome, "rejected")

    def test_worker_accepts_terminal_decision_between_claim_and_execution(self):
        from pldr_api.intake import archive_intake, reject_intake
        from pldr_api.investigations import (
            claim_next_review_task,
            execute_claimed_review_task,
        )
        from pldr_api.models import SearchSelection

        result_id = self.add_search_results(1)[0]
        investigation_id = self.create_investigation("Claim handoff decision race")
        queued = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": [result_id], "investigation_id": investigation_id},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        task_id = queued.json()["tasks"][0]["id"]
        intake_item_id = queued.json()["tasks"][0]["intake_item_id"]
        with SessionLocal() as session:
            claimed = claim_next_review_task(session, worker_id="claim-handoff-worker")
            assert claimed is not None
            self.assertEqual(claimed.id, task_id)
        with SessionLocal() as analyst_session:
            item = analyst_session.get(IntakeItem, intake_item_id)
            assert item is not None
            rejected = reject_intake(
                analyst_session,
                item,
                "claim-handoff-analyst",
                "Decision committed after claim and before worker execution",
            )
            archive_intake(
                analyst_session,
                rejected,
                analyst="claim-handoff-archiver",
                reason="Archive after the terminal handoff decision",
            )

        completed = asyncio.run(execute_claimed_review_task(task_id))
        self.assertEqual(completed.status, "rejected")
        with SessionLocal() as session:
            item = session.get(IntakeItem, intake_item_id)
            task = session.get(ReviewTask, task_id)
            selection = session.get(SearchSelection, task.selection_id) if task else None
            assert item is not None and task is not None and selection is not None
            self.assertEqual(item.status, "rejected")
            self.assertIsNotNone(item.archived_at)
            self.assertEqual(task.status, "rejected")
            self.assertEqual(selection.status, "rejected")
            self.assertEqual(selection.outcome, "rejected")

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

    def test_intake_can_be_removed_and_restored_per_topic_without_losing_history(self):
        first_topic = self.create_investigation("First archive topic")
        second_topic = self.create_investigation("Second archive topic")
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            created = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": "This public note contains enough exact text to verify reversible topic removal.",
                    "source_description": "Topic removal contract",
                    "title": "Reversible topic removal",
                    "language": "en",
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        item_id = created.json()["intake_item"]["id"]
        task_ids: dict[str, str] = {}
        for topic_id in (first_topic, second_topic):
            linked = self.client.post(
                f"/pldr-api/v1/investigations/{topic_id}/links",
                json={
                    "object_type": "intake",
                    "object_id": item_id,
                    "role": "reference" if topic_id == first_topic else "member",
                },
            )
            self.assertEqual(linked.status_code, 201, linked.text)
            task_ids[topic_id] = linked.json()["review_task"]["task"]["id"]

        removed = self.client.post(
            f"/pldr-api/v1/investigations/{first_topic}/intake/{item_id}/remove"
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["changed"])
        repeated_remove = self.client.post(
            f"/pldr-api/v1/investigations/{first_topic}/intake/{item_id}/remove",
            json={"analyst": "topic-tester", "reason": "Repeated removal"},
        )
        self.assertEqual(repeated_remove.status_code, 200, repeated_remove.text)
        self.assertFalse(repeated_remove.json()["changed"])

        first_active = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/tasks"
        ).json()
        first_removed = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/tasks?visibility=removed"
        ).json()
        first_all = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/tasks?visibility=all"
        ).json()
        second_active = self.client.get(
            f"/pldr-api/v1/investigations/{second_topic}/tasks"
        ).json()
        self.assertEqual(first_active["count"], 0)
        self.assertEqual(first_removed["count"], 1)
        self.assertEqual(first_removed["items"][0]["id"], task_ids[first_topic])
        self.assertTrue(first_removed["items"][0]["removed_from_investigation"])
        self.assertEqual(first_removed["items"][0]["allowed_actions"], ["restore"])
        self.assertEqual(
            first_removed["items"][0]["intake_item"]["allowed_actions"],
            ["archive"],
        )
        self.assertEqual(first_all["count"], 1)
        self.assertEqual(second_active["count"], 1)
        self.assertEqual(second_active["items"][0]["id"], task_ids[second_topic])
        self.assertEqual(
            second_active["items"][0]["allowed_actions"],
            ["remove_from_investigation"],
        )
        hidden_detail = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/intake/{item_id}"
        )
        removed_detail = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/intake/{item_id}",
            params={"visibility": "removed"},
        )
        historical_detail = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/intake/{item_id}",
            params={"visibility": "all"},
        )
        self.assertEqual(hidden_detail.status_code, 404, hidden_detail.text)
        self.assertEqual(removed_detail.status_code, 200, removed_detail.text)
        self.assertEqual(removed_detail.json()["id"], item_id)
        self.assertEqual(historical_detail.status_code, 200, historical_detail.text)
        with SessionLocal() as session:
            removed_task = session.get(ReviewTask, task_ids[first_topic])
            assert removed_task is not None
            removed_task.status = "failed"
            removed_task.error_class = "fetch_failed"
            removed_task.error_message = "Synthetic retry guard failure"
            session.commit()
        hidden_retry = self.client.post(
            f"/pldr-api/v1/tasks/{task_ids[first_topic]}/retry",
            json={"actor": "topic-tester"},
        )
        self.assertEqual(hidden_retry.status_code, 409, hidden_retry.text)
        self.assertIn("Restore", hidden_retry.json()["detail"])
        with SessionLocal() as session:
            removed_task = session.get(ReviewTask, task_ids[first_topic])
            assert removed_task is not None
            removed_task.status = "ready"
            removed_task.error_class = None
            removed_task.error_message = None
            session.commit()
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{first_topic}"
            ).json()["counts"]["intake_items"],
            0,
        )
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{second_topic}"
            ).json()["counts"]["intake_items"],
            1,
        )
        from pldr_api.investigations import _next_review_task

        with SessionLocal() as session:
            self.assertIsNone(
                _next_review_task(
                    session,
                    first_topic,
                    exclude_intake_id="not-the-removed-item",
                )
            )

        restored = self.client.post(
            f"/pldr-api/v1/investigations/{first_topic}/intake/{item_id}/restore"
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["changed"])
        self.assertEqual(restored.json()["task"]["id"], task_ids[first_topic])
        self.assertEqual(restored.json()["link"]["role"], "reference")
        repeated_restore = self.client.post(
            f"/pldr-api/v1/investigations/{first_topic}/intake/{item_id}/restore",
            json={"analyst": "topic-tester", "reason": "Repeated restore"},
        )
        self.assertEqual(repeated_restore.status_code, 200, repeated_restore.text)
        self.assertFalse(repeated_restore.json()["changed"])
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{first_topic}/tasks"
            ).json()["count"],
            1,
        )

        archived = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/archive",
            json={"analyst": "topic-tester", "reason": "Hide from every active inbox"},
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        for topic_id in (first_topic, second_topic):
            self.assertEqual(
                self.client.get(
                    f"/pldr-api/v1/investigations/{topic_id}/tasks"
                ).json()["count"],
                0,
            )
            self.assertEqual(
                self.client.get(
                    f"/pldr-api/v1/investigations/{topic_id}/tasks?visibility=all"
                ).json()["count"],
                1,
            )
            archived_task_payload = self.client.get(
                f"/pldr-api/v1/investigations/{topic_id}/tasks?visibility=all"
            ).json()["items"][0]
            self.assertEqual(archived_task_payload["allowed_actions"], [])
            self.assertEqual(
                archived_task_payload["intake_item"]["allowed_actions"],
                ["restore"],
            )
        with SessionLocal() as session:
            archived_task = session.get(ReviewTask, task_ids[first_topic])
            assert archived_task is not None
            archived_task.status = "failed"
            archived_task.error_class = "fetch_failed"
            archived_task.error_message = "Synthetic archived retry guard failure"
            session.commit()
        archived_retry = self.client.post(
            f"/pldr-api/v1/tasks/{task_ids[first_topic]}/retry",
            json={"actor": "topic-tester"},
        )
        self.assertEqual(archived_retry.status_code, 409, archived_retry.text)
        self.assertIn("Restore", archived_retry.json()["detail"])
        restored_global = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/restore",
            json={"analyst": "topic-tester", "reason": "Return to review"},
        )
        self.assertEqual(restored_global.status_code, 200, restored_global.text)

        activity_items = self.client.get(
            f"/pldr-api/v1/investigations/{first_topic}/activity"
        ).json()["items"]
        activity_actions = {entry["action"] for entry in activity_items}
        self.assertIn("intake.removed_from_investigation", activity_actions)
        self.assertIn("intake.restored_to_investigation", activity_actions)
        self.assertIn("intake.archived", activity_actions)
        self.assertIn("intake.restored", activity_actions)
        membership_reasons = {
            entry["action"]: entry["detail"]["reason"]
            for entry in activity_items
            if entry["action"]
            in {
                "intake.removed_from_investigation",
                "intake.restored_to_investigation",
            }
        }
        self.assertEqual(
            membership_reasons,
            {
                "intake.removed_from_investigation": "Removed from investigation",
                "intake.restored_to_investigation": "Restored to investigation",
            },
        )

        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            session.add(
                IntakeItem(
                    id="active_archive_block",
                    input_type="text",
                    status="parsed",
                    source_description="Active archive guard",
                    language="en",
                    raw_snapshot="Active task material long enough for later candidate generation.",
                    raw_hash="active-raw",
                    extracted_snapshot="Active task material long enough for later candidate generation.",
                    extracted_hash="active-body",
                    review={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        queued_link = self.client.post(
            f"/pldr-api/v1/investigations/{first_topic}/links",
            json={"object_type": "intake", "object_id": "active_archive_block"},
        )
        self.assertEqual(queued_link.status_code, 201, queued_link.text)
        self.assertEqual(queued_link.json()["review_task"]["task"]["status"], "queued")
        self.assertEqual(
            queued_link.json()["review_task"]["task"]["allowed_actions"], []
        )
        blocked_archive = self.client.post(
            "/pldr-api/v1/intake/active_archive_block/archive",
            json={"analyst": "topic-tester", "reason": "Cannot race an active task"},
        )
        self.assertEqual(blocked_archive.status_code, 409, blocked_archive.text)
        blocked_remove = self.client.post(
            f"/pldr-api/v1/investigations/{first_topic}/intake/active_archive_block/remove",
            json={"analyst": "topic-tester", "reason": "Cannot orphan an active task"},
        )
        self.assertEqual(blocked_remove.status_code, 409, blocked_remove.text)

    def test_legacy_intake_link_without_task_remains_private_and_restorable(self):
        topic_id = self.create_investigation("Legacy link topic")
        unrelated_topic_id = self.create_investigation("Unrelated topic")
        item_id = "legacy_link_without_review_task"
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            session.add(
                IntakeItem(
                    id=item_id,
                    input_type="text",
                    status="candidate_ready",
                    source_description="Legacy relationship without a task",
                    language="en",
                    raw_snapshot="Legacy material remains recoverable after topic removal.",
                    raw_hash="legacy-no-task-raw",
                    extracted_snapshot="Legacy material remains recoverable after topic removal.",
                    extracted_hash="legacy-no-task-body",
                    review={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                InvestigationLink(
                    id="link_legacy_without_task",
                    investigation_id=topic_id,
                    object_type="intake",
                    object_id=item_id,
                    role="legacy-reference",
                    metadata_json={"origin": "pre-task-migration"},
                    created_at=now,
                )
            )
            session.commit()

        removed = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}/remove",
            json={"analyst": "migration-tester", "reason": "Temporarily out of scope"},
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["changed"])
        repeated_remove = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}/remove"
        )
        self.assertEqual(repeated_remove.status_code, 200, repeated_remove.text)
        self.assertFalse(repeated_remove.json()["changed"])
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{topic_id}/tasks?visibility=removed"
            ).json()["count"],
            0,
        )
        removed_detail = self.client.get(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}",
            params={"visibility": "removed"},
        )
        self.assertEqual(removed_detail.status_code, 200, removed_detail.text)
        unrelated_detail = self.client.get(
            f"/pldr-api/v1/investigations/{unrelated_topic_id}/intake/{item_id}",
            params={"visibility": "all"},
        )
        self.assertEqual(unrelated_detail.status_code, 404, unrelated_detail.text)
        with SessionLocal() as session:
            bootstrap_legacy_investigations(session)
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id
                        == UNCLASSIFIED_INVESTIGATION_ID,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == item_id,
                    )
                )
            )

        restored = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}/restore",
            json={"analyst": "migration-tester", "reason": "Relevant again"},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["changed"])
        self.assertIsNone(restored.json()["task"])
        self.assertEqual(restored.json()["link"]["role"], "legacy-reference")
        self.assertEqual(
            restored.json()["link"]["metadata"],
            {"origin": "pre-task-migration"},
        )
        repeated_restore = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}/restore"
        )
        self.assertEqual(repeated_restore.status_code, 200, repeated_restore.text)
        self.assertFalse(repeated_restore.json()["changed"])
        self.assertIsNone(repeated_restore.json()["task"])
        with SessionLocal() as session:
            self.assertIsNone(
                session.scalar(
                    select(ReviewTask.id).where(
                        ReviewTask.investigation_id == topic_id,
                        ReviewTask.intake_item_id == item_id,
                    )
                )
            )

    def test_scoped_confirm_rechecks_membership_inside_formal_write_fence(self):
        from pldr_api.intake import lock_intake_for_mutation

        topic_id = self.create_investigation("Scoped confirmation race")
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            created = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": (
                        "This scoped confirmation race fixture is long enough to produce "
                        "exact candidate evidence for a deterministic review decision."
                    ),
                    "source_description": "Scoped confirmation race fixture",
                    "language": "en",
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["intake_item"]
        linked = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/links",
            json={"object_type": "intake", "object_id": item["id"]},
        )
        self.assertEqual(linked.status_code, 201, linked.text)
        with SessionLocal() as session:
            before_events = session.scalar(select(func.count()).select_from(Event))

        removed_before_fence = False

        def remove_membership_before_confirm_fence(session, item_id, *, action):
            nonlocal removed_before_fence
            if action == "confirming it" and not removed_before_fence:
                removed_before_fence = True
                with SessionLocal() as concurrent_session:
                    lock_intake_for_mutation(
                        concurrent_session,
                        item_id,
                        action="removing it from an investigation",
                    )
                    concurrent_link = concurrent_session.scalar(
                        select(InvestigationLink).where(
                            InvestigationLink.investigation_id == topic_id,
                            InvestigationLink.object_type == "intake",
                            InvestigationLink.object_id == item_id,
                        )
                    )
                    assert concurrent_link is not None
                    concurrent_session.delete(concurrent_link)
                    concurrent_session.commit()
            return lock_intake_for_mutation(session, item_id, action=action)

        with patch(
            "pldr_api.intake.lock_intake_for_mutation",
            new=remove_membership_before_confirm_fence,
        ):
            confirmed = self.client.post(
                f"/pldr-api/v1/investigations/{topic_id}/intake/{item['id']}/confirm",
                json=self.confirmation_request(item),
            )
        self.assertTrue(removed_before_fence)
        self.assertEqual(confirmed.status_code, 409, confirmed.text)
        self.assertIn("no longer linked", confirmed.json()["detail"])
        with SessionLocal() as session:
            persisted = session.get(IntakeItem, item["id"])
            assert persisted is not None
            self.assertEqual(persisted.status, "candidate_ready")
            self.assertIsNone(persisted.final_event_id)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(Event)),
                before_events,
            )
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == topic_id,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == item["id"],
                    )
                )
            )

    def test_scoped_idempotent_confirm_rechecks_membership_after_concurrent_confirm(self):
        from pldr_api.investigations import remove_investigation_intake
        from pldr_api.intake import confirm_intake, lock_intake_for_mutation
        from pldr_api.schemas import ArchiveRequest, IntakeConfirmationRequest

        topic_a = self.create_investigation("Concurrent confirmation winner")
        topic_b = self.create_investigation("Concurrent scoped confirmation loser")
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            created = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": (
                        "This idempotent scoped confirmation fixture provides enough exact "
                        "text for the same review payload to race across two topics safely."
                    ),
                    "source_description": "Scoped idempotency race fixture",
                    "language": "en",
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["intake_item"]
        for topic_id in (topic_a, topic_b):
            linked = self.client.post(
                f"/pldr-api/v1/investigations/{topic_id}/links",
                json={"object_type": "intake", "object_id": item["id"]},
            )
            self.assertEqual(linked.status_code, 201, linked.text)
        confirmation_payload = self.confirmation_request(item)
        confirmation_request = IntakeConfirmationRequest.model_validate(
            confirmation_payload
        )
        concurrent_confirmed = False

        def remove_scope_and_confirm_elsewhere(session, item_id, *, action):
            nonlocal concurrent_confirmed
            if action == "confirming it" and not concurrent_confirmed:
                concurrent_confirmed = True
                with SessionLocal() as removal_session:
                    removed = remove_investigation_intake(
                        topic_b,
                        item_id,
                        ArchiveRequest(
                            analyst="scope-race-remover",
                            reason="Remove B before the competing confirmation wins",
                        ),
                        removal_session,
                    )
                    self.assertTrue(removed["changed"])
                with SessionLocal() as confirmation_session:
                    concurrent_item = confirmation_session.get(IntakeItem, item_id)
                    assert concurrent_item is not None
                    _, _, created_objects = confirm_intake(
                        confirmation_session,
                        concurrent_item,
                        confirmation_request,
                    )
                    self.assertTrue(created_objects)
            return lock_intake_for_mutation(session, item_id, action=action)

        with patch(
            "pldr_api.intake.lock_intake_for_mutation",
            new=remove_scope_and_confirm_elsewhere,
        ):
            scoped = self.client.post(
                f"/pldr-api/v1/investigations/{topic_b}/intake/{item['id']}/confirm",
                json=confirmation_payload,
            )
        self.assertTrue(concurrent_confirmed)
        self.assertEqual(scoped.status_code, 409, scoped.text)
        self.assertIn("no longer linked", scoped.json()["detail"])
        with SessionLocal() as session:
            persisted = session.get(IntakeItem, item["id"])
            assert persisted is not None and persisted.final_event_id is not None
            self.assertEqual(persisted.status, "confirmed")
            self.assertIsNotNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == topic_a,
                        InvestigationLink.object_type == "event",
                        InvestigationLink.object_id == persisted.final_event_id,
                    )
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == topic_b,
                        InvestigationLink.object_type == "event",
                        InvestigationLink.object_id == persisted.final_event_id,
                    )
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == topic_b,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == item["id"],
                    )
                )
            )

    def test_restore_rechecks_confirmed_state_and_concurrent_restore_is_idempotent(self):
        from pldr_api.investigations import (
            lock_intake_for_mutation,
            restore_investigation_intake,
        )
        from pldr_api.intake import confirm_intake, get_intake_item
        from pldr_api.schemas import ArchiveRequest, IntakeConfirmationRequest

        def create_removed_item(marker: str) -> tuple[str, dict]:
            topic_id = self.create_investigation(f"Restore race {marker}")
            with patch(
                "pldr_api.intake.run_model_task",
                new=AsyncMock(return_value={"mode": "fallback"}),
            ):
                created = self.client.post(
                    "/pldr-api/v1/intake/text",
                    json={
                        "text": (
                            f"Restore race {marker} contains enough durable exact text for "
                            "candidate generation and deterministic concurrency review."
                        ),
                        "source_description": f"Restore race fixture {marker}",
                        "language": "en",
                    },
                )
            self.assertEqual(created.status_code, 200, created.text)
            item = created.json()["intake_item"]
            linked = self.client.post(
                f"/pldr-api/v1/investigations/{topic_id}/links",
                json={"object_type": "intake", "object_id": item["id"]},
            )
            self.assertEqual(linked.status_code, 201, linked.text)
            removed = self.client.post(
                f"/pldr-api/v1/investigations/{topic_id}/intake/{item['id']}/remove"
            )
            self.assertEqual(removed.status_code, 200, removed.text)
            return topic_id, item

        confirmed_topic, confirmed_item = create_removed_item("confirm-first")
        confirmation = self.confirmation_request(confirmed_item)
        confirmed_before_restore = False

        def confirm_before_restore_fence(session, item_id, *, action):
            nonlocal confirmed_before_restore
            if action == "restoring it to an investigation" and not confirmed_before_restore:
                confirmed_before_restore = True
                with SessionLocal() as concurrent_session:
                    concurrent_item = get_intake_item(concurrent_session, item_id)
                    assert concurrent_item is not None
                    confirm_intake(
                        concurrent_session,
                        concurrent_item,
                        IntakeConfirmationRequest.model_validate(confirmation),
                    )
            return lock_intake_for_mutation(session, item_id, action=action)

        with patch(
            "pldr_api.investigations.lock_intake_for_mutation",
            new=confirm_before_restore_fence,
        ):
            restored_after_confirm = self.client.post(
                f"/pldr-api/v1/investigations/{confirmed_topic}/intake/{confirmed_item['id']}/restore"
            )
        self.assertTrue(confirmed_before_restore)
        self.assertEqual(restored_after_confirm.status_code, 409, restored_after_confirm.text)
        self.assertIn("confirmed", restored_after_confirm.json()["detail"])
        with SessionLocal() as session:
            persisted = session.get(IntakeItem, confirmed_item["id"])
            assert persisted is not None
            self.assertEqual(persisted.status, "confirmed")
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == confirmed_topic,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == confirmed_item["id"],
                    )
                )
            )

        duplicate_topic, duplicate_item = create_removed_item("double-restore")
        concurrent_restore_done = False

        def restore_once_before_outer_fence(session, item_id, *, action):
            nonlocal concurrent_restore_done
            if action == "restoring it to an investigation" and not concurrent_restore_done:
                concurrent_restore_done = True
                with SessionLocal() as concurrent_session:
                    restored = restore_investigation_intake(
                        duplicate_topic,
                        item_id,
                        ArchiveRequest(analyst="concurrent-restorer"),
                        concurrent_session,
                    )
                    self.assertTrue(restored["changed"])
            return lock_intake_for_mutation(session, item_id, action=action)

        with patch(
            "pldr_api.investigations.lock_intake_for_mutation",
            new=restore_once_before_outer_fence,
        ):
            repeated = self.client.post(
                f"/pldr-api/v1/investigations/{duplicate_topic}/intake/{duplicate_item['id']}/restore"
            )
        self.assertTrue(concurrent_restore_done)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertFalse(repeated.json()["changed"])
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(InvestigationLink)
                    .where(
                        InvestigationLink.investigation_id == duplicate_topic,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == duplicate_item["id"],
                    )
                ),
                1,
            )

    def test_restore_and_direct_regenerate_synchronize_existing_review_task(self):
        topic_id = self.create_investigation("Restore task state synchronization")
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            created = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": (
                        "This restore synchronization fixture has enough exact durable text "
                        "to test rejection and candidate regeneration task states."
                    ),
                    "source_description": "Restore task synchronization fixture",
                    "language": "en",
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        item_id = created.json()["intake_item"]["id"]
        linked = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/links",
            json={"object_type": "intake", "object_id": item_id},
        )
        self.assertEqual(linked.status_code, 201, linked.text)
        task_id = linked.json()["review_task"]["task"]["id"]
        self.assertEqual(linked.json()["review_task"]["task"]["status"], "ready")
        removed = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}/remove"
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        rejected = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/reject",
            json={
                "analyst": "restore-sync-analyst",
                "reason": "Reject globally while the topic membership is removed",
            },
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        with SessionLocal() as session:
            stale_task = session.get(ReviewTask, task_id)
            assert stale_task is not None
            self.assertEqual(stale_task.status, "ready")
        restored = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}/restore"
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["task"]["status"], "rejected")
        with SessionLocal() as session:
            task = session.get(ReviewTask, task_id)
            assert task is not None
            self.assertEqual(task.status, "rejected")
            self.assertIsNone(task.active_key)

        failed_topic = self.create_investigation("Removed failed task synchronization")
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(side_effect=TimeoutError("Initial model timeout")),
        ):
            failed_created = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": (
                        "This removed failed fixture is long enough for a later successful "
                        "global regeneration to be reconciled when membership is restored."
                    ),
                    "source_description": "Removed failed task fixture",
                    "language": "en",
                },
            )
        self.assertEqual(failed_created.status_code, 200, failed_created.text)
        failed_item_id = failed_created.json()["intake_item"]["id"]
        self.assertEqual(
            failed_created.json()["intake_item"]["status"], "generation_failed"
        )
        failed_link = self.client.post(
            f"/pldr-api/v1/investigations/{failed_topic}/links",
            json={"object_type": "intake", "object_id": failed_item_id},
        )
        self.assertEqual(failed_link.status_code, 201, failed_link.text)
        failed_task_id = failed_link.json()["review_task"]["task"]["id"]
        self.assertEqual(failed_link.json()["review_task"]["task"]["status"], "failed")
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            regenerated_linked = self.client.post(
                f"/pldr-api/v1/intake/{failed_item_id}/regenerate"
            )
        self.assertEqual(regenerated_linked.status_code, 200, regenerated_linked.text)
        self.assertEqual(regenerated_linked.json()["status"], "candidate_ready")
        with SessionLocal() as session:
            item = session.get(IntakeItem, failed_item_id)
            task = session.get(ReviewTask, failed_task_id)
            assert item is not None and task is not None
            self.assertEqual(task.status, "ready")
            self.assertIsNone(task.error_class)
            # Prepare another legitimate failed review state so the same
            # durable task can exercise regeneration while membership is absent.
            item.status = "generation_failed"
            item.candidate_mode = "failed"
            item.candidate_error = "Second model timeout before removal"
            item.updated_at = datetime.now(timezone.utc)
            task.status = "failed"
            task.active_key = None
            task.error_class = "intake_failed"
            task.error_message = item.candidate_error
            task.updated_at = item.updated_at
            session.commit()
        removed_failed = self.client.post(
            f"/pldr-api/v1/investigations/{failed_topic}/intake/{failed_item_id}/remove"
        )
        self.assertEqual(removed_failed.status_code, 200, removed_failed.text)
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            regenerated_removed = self.client.post(
                f"/pldr-api/v1/intake/{failed_item_id}/regenerate"
            )
        self.assertEqual(regenerated_removed.status_code, 200, regenerated_removed.text)
        self.assertEqual(regenerated_removed.json()["status"], "candidate_ready")
        with SessionLocal() as session:
            stale_task = session.get(ReviewTask, failed_task_id)
            assert stale_task is not None
            self.assertEqual(stale_task.status, "failed")
        restored_failed = self.client.post(
            f"/pldr-api/v1/investigations/{failed_topic}/intake/{failed_item_id}/restore"
        )
        self.assertEqual(restored_failed.status_code, 200, restored_failed.text)
        self.assertEqual(restored_failed.json()["task"]["status"], "ready")
        with SessionLocal() as session:
            task = session.get(ReviewTask, failed_task_id)
            assert task is not None
            self.assertEqual(task.status, "ready")
            self.assertIsNone(task.error_class)
            self.assertIsNone(task.error_message)

    def test_archived_intake_blocks_mutation_linking_and_worker_execution(self):
        from pldr_api.errors import ArchivedIntakeError
        from pldr_api.investigations import (
            claim_next_review_task,
            ensure_review_task_for_intake,
            execute_claimed_review_task,
        )

        topic_id = self.create_investigation("Archived state machine topic")
        item_id = "archived_state_machine_item"
        task_id = "task_archived_state_machine"
        now = datetime.now(timezone.utc)
        original_snapshot = (
            "Archived candidate source text remains immutable while every write "
            "entry and background worker is blocked."
        )
        with SessionLocal() as session:
            session.add(
                IntakeItem(
                    id=item_id,
                    input_type="text",
                    status="generation_failed",
                    source_description="Archived state machine fixture",
                    language="en",
                    raw_snapshot=original_snapshot,
                    raw_hash="archived-state-raw",
                    extracted_snapshot=original_snapshot,
                    extracted_hash="archived-state-body",
                    candidate_mode="failed",
                    candidate_error="Synthetic model failure",
                    review={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

        archived = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/archive",
            json={"analyst": "state-machine-tester", "reason": "Pause all work"},
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        for action, payload in (
            ("regenerate", None),
            ("reject", {"analyst": "state-machine-tester", "reason": "Must restore first"}),
            ("cancel", {"analyst": "state-machine-tester", "reason": "Must restore first"}),
        ):
            response = self.client.post(
                f"/pldr-api/v1/intake/{item_id}/{action}",
                json=payload,
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("Restore", response.json()["detail"])

        blocked_link = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/links",
            json={"object_type": "intake", "object_id": item_id},
        )
        self.assertEqual(blocked_link.status_code, 409, blocked_link.text)
        self.assertIn("Restore", blocked_link.json()["detail"])
        with SessionLocal() as session:
            item = session.get(IntakeItem, item_id)
            assert item is not None
            with self.assertRaises(ArchivedIntakeError):
                ensure_review_task_for_intake(
                    session,
                    topic_id,
                    item,
                    actor="state-machine-tester",
                )
            session.rollback()
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.investigation_id == topic_id,
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == item_id,
                    )
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(ReviewTask.id).where(
                        ReviewTask.investigation_id == topic_id,
                        ReviewTask.intake_item_id == item_id,
                    )
                )
            )

            # Simulate stale pre-fix data to prove both worker barriers are
            # defensive rather than relying only on public endpoint checks.
            session.add(
                InvestigationLink(
                    id="link_archived_state_machine",
                    investigation_id=topic_id,
                    object_type="intake",
                    object_id=item_id,
                    role="member",
                    metadata_json={"fixture": "stale-hidden-queue"},
                    created_at=now,
                )
            )
            session.add(
                ReviewTask(
                    id=task_id,
                    investigation_id=topic_id,
                    task_type="intake_candidate_generation",
                    subject_type="intake",
                    subject_id=item_id,
                    active_key=f"{topic_id}:intake:{item_id}",
                    status="queued",
                    attempt_number=1,
                    queued_at=now,
                    intake_item_id=item_id,
                    payload_json={"result_fingerprint": f"intake:{item_id}"},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

        with SessionLocal() as session:
            self.assertIsNone(
                claim_next_review_task(session, worker_id="archive-guard-worker")
            )
            hidden_task = session.get(ReviewTask, task_id)
            assert hidden_task is not None
            self.assertEqual(hidden_task.status, "queued")
            hidden_task.status = "fetching"
            hidden_task.lease_owner = "archive-race-worker"
            hidden_task.lease_expires_at = now + timedelta(minutes=2)
            session.commit()

        blocked_execution = asyncio.run(execute_claimed_review_task(task_id))
        self.assertEqual(blocked_execution.status, "failed")
        self.assertEqual(blocked_execution.error_class, "intake_archived")
        with SessionLocal() as session:
            item = session.get(IntakeItem, item_id)
            assert item is not None
            self.assertEqual(item.status, "generation_failed")
            self.assertEqual(item.extracted_snapshot, original_snapshot)
            self.assertEqual(len(item.candidates), 0)

        restored = self.client.post(f"/pldr-api/v1/intake/{item_id}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(return_value={"mode": "fallback"}),
        ):
            regenerated = self.client.post(
                f"/pldr-api/v1/intake/{item_id}/regenerate"
            )
        self.assertEqual(regenerated.status_code, 200, regenerated.text)
        self.assertEqual(regenerated.json()["status"], "candidate_ready")
        with SessionLocal() as session:
            synchronized_task = session.get(ReviewTask, task_id)
            assert synchronized_task is not None
            self.assertEqual(synchronized_task.status, "ready")
            self.assertIsNone(synchronized_task.error_class)
            self.assertIsNone(synchronized_task.error_message)

        removed = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/intake/{item_id}/remove"
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        with SessionLocal() as session:
            unlinked_task = session.get(ReviewTask, task_id)
            assert unlinked_task is not None
            unlinked_task.status = "fetching"
            unlinked_task.lease_owner = "unlinked-race-worker"
            unlinked_task.lease_expires_at = now + timedelta(minutes=2)
            session.commit()
        blocked_unlinked = asyncio.run(execute_claimed_review_task(task_id))
        self.assertEqual(blocked_unlinked.status, "failed")
        self.assertEqual(blocked_unlinked.error_class, "intake_not_linked")
        with SessionLocal() as session:
            item = session.get(IntakeItem, item_id)
            assert item is not None
            self.assertEqual(item.status, "candidate_ready")
            self.assertEqual(item.extracted_snapshot, original_snapshot)

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
