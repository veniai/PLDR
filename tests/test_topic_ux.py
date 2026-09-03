from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

TOPIC_UX_ROOT = Path(tempfile.mkdtemp(prefix="pldr-topic-ux-tests-"))
os.environ["PLDR_DATABASE_URL"] = f"sqlite:///{TOPIC_UX_ROOT / 'topic-ux.db'}"
os.environ["PLDR_REPORT_DIR"] = str(TOPIC_UX_ROOT / "reports")
os.environ.pop("LLM_API_KEY", None)

from fastapi.testclient import TestClient
from sqlalchemy import select


class TopicUxContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global Base, SessionLocal, engine, app
        global Claim, Entity, Event, EventEntity, IntakeCandidate, IntakeItem, InvestigationLink, ReviewTask
        global run_review_task_once, _structured_task_error

        from pldr_api.database import Base, SessionLocal, engine
        from pldr_api.investigations import _structured_task_error, run_review_task_once
        from pldr_api.main import app
        from pldr_api.models import (
            Claim,
            Entity,
            Event,
            EventEntity,
            IntakeCandidate,
            IntakeItem,
            InvestigationLink,
            ReviewTask,
        )

        database_path = Path(str(engine.url.database)).resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        cls.owns_test_root = TOPIC_UX_ROOT in database_path.parents
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        if cls.owns_test_root:
            engine.dispose()
            shutil.rmtree(TOPIC_UX_ROOT, ignore_errors=True)

    def setUp(self):
        database_path = Path(str(engine.url.database)).resolve()
        if not any(part.startswith("pldr-") and "test" in part for part in database_path.parts):
            self.fail(f"Refusing to reset non-test database: {database_path}")
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def create_topic(self, title: str) -> str:
        response = self.client.post(
            "/pldr-api/v1/investigations",
            json={"title": title, "question": f"What supports {title}?"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def create_intake(self, marker: str, *, published_at: str | None = None) -> dict:
        response = self.client.post(
            "/pldr-api/v1/intake/text",
            json={
                "text": (
                    f"The public dispatch {marker} states that the monitored crossing reopened "
                    "after an independent inspection confirmed safe operating conditions."
                ),
                "source_description": f"Public dispatch {marker}",
                "title": f"Dispatch {marker}",
                "published_at": published_at,
                "language": "en",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["intake_item"]["status"], "candidate_ready")
        item = response.json()["intake_item"]
        candidates = {candidate["object_type"]: candidate for candidate in item["candidates"]}
        self.assertNotEqual(
            candidates["claim"]["machine"]["fields"]["text"],
            candidates["evidence"]["machine"]["fields"]["snippet"],
            "关键信息必须是归纳后的说法，不能直接复制原文依据",
        )
        return item

    def test_topic_starter_material_is_saved_before_background_candidate_generation(self):
        topic = self.create_topic("Background starter material")
        model = AsyncMock(side_effect=TimeoutError("model timeout"))
        retry_env = {
            "LLM_API_KEY": "configured-for-test",
            "LLM_BASE_URL": "https://model.example.test/v1",
            "LLM_MODEL_NAME": "test-model",
            "PLDR_MODEL_AUTO_RETRY_ATTEMPTS": "3",
            "PLDR_MODEL_AUTO_RETRY_BASE_SECONDS": "0",
        }
        with patch.dict(os.environ, retry_env), patch("pldr_api.intake.run_model_task", new=model):
            response = self.client.post(
                "/pldr-api/v1/intake/text?defer_candidates=true",
                json={
                    "text": (
                        "A public bulletin states that the monitored sea lane remained open "
                        "while authorities reviewed the reported incident."
                    ),
                    "source_description": "Public bulletin",
                    "title": "Sea lane bulletin",
                    "published_at": None,
                    "language": "auto",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()["intake_item"]
            self.assertEqual(item["status"], "parsed")
            self.assertEqual(item["candidates"], [])
            model.assert_not_awaited()

            web = self.client.post(
                "/pldr-api/v1/import/url?defer_candidates=true",
                json={
                    "url": "https://example.org/deferred-web",
                    "source_name": "Deferred public page",
                    "title": "Deferred web material",
                    "html": (
                        "<html><body><article><p>A public page records enough source text "
                        "to preserve the material before any candidate model runs.</p></article></body></html>"
                    ),
                    "language": "auto",
                },
            )
            self.assertEqual(web.status_code, 200, web.text)
            self.assertEqual(web.json()["intake_item"]["status"], "parsed")

            rss = self.client.post(
                "/pldr-api/v1/import/rss?defer_candidates=true",
                json={
                    "xml": (
                        "<rss><channel><item><title>Deferred RSS item</title>"
                        "<link>https://example.org/deferred-rss</link>"
                        "<description>A public RSS entry contains enough preserved source text "
                        "to be processed later by the durable topic queue.</description>"
                        "</item></channel></rss>"
                    ),
                    "source_name": "Deferred public feed",
                    "language": "auto",
                },
            )
            self.assertEqual(rss.status_code, 200, rss.text)
            self.assertEqual(rss.json()["intake_items"][0]["status"], "parsed")

            uploaded = self.client.post(
                "/pldr-api/v1/intake/files?defer_candidates=true",
                data={"source_description": "Deferred public file", "language": "auto"},
                files={
                    "file": (
                        "starter.txt",
                        b"A persisted local file remains available before background candidate generation.",
                        "text/plain",
                    )
                },
            )
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertEqual(uploaded.json()["intake_item"]["status"], "parsed")
            model.assert_not_awaited()

            linked = self.client.post(
                f"/pldr-api/v1/investigations/{topic}/links",
                json={"object_type": "intake", "object_id": item["id"]},
            )
            self.assertEqual(linked.status_code, 201, linked.text)
            task = linked.json()["review_task"]["task"]
            self.assertEqual(task["status"], "queued")
            model.assert_not_awaited()

            asyncio.run(run_review_task_once(worker_id="topic-onboarding-worker"))
            model.assert_awaited_once()

        waiting = self.client.get(f"/pldr-api/v1/tasks/{task['id']}")
        self.assertEqual(waiting.status_code, 200, waiting.text)
        self.assertEqual(waiting.json()["status"], "queued")
        self.assertTrue(waiting.json()["waiting_for_model_retry"])
        self.assertEqual(waiting.json()["model_retry"]["retry_number"], 1)

    def link(self, topic_id: str, object_type: str, object_id: str) -> None:
        response = self.client.post(
            f"/pldr-api/v1/investigations/{topic_id}/links",
            json={"object_type": object_type, "object_id": object_id},
        )
        self.assertEqual(response.status_code, 201, response.text)

    @staticmethod
    def confirmation(item: dict, *, disposition: str = "create", merge_event_id=None) -> dict:
        candidates = {candidate["object_type"]: candidate for candidate in item["candidates"]}
        claim = candidates["claim"]
        evidence = candidates["evidence"]
        event = candidates["event"]["machine"]["fields"]
        return {
            "disposition": disposition,
            "analyst": "topic-reviewer",
            "merge_event_id": merge_event_id,
            "event": {
                "title": event.get("title") or "Confirmed dispatch event",
                "summary": event.get("summary") or "Human-confirmed public dispatch.",
                "event_type": "incident",
                "start_at": None,
                "location_name": "",
                "importance": "medium",
            },
            "entities": [],
            "claims": [{
                "candidate_key": claim["candidate_key"],
                "action": "create",
                "text": claim["machine"]["fields"]["text"],
                "status": "unverified",
                "confidence": 0.6,
                "temporal_scope": "",
                "merge_claim_id": None,
            }],
            "evidence": [{
                "candidate_key": evidence["candidate_key"],
                "action": "include",
                "snippet": evidence["machine"]["fields"]["snippet"],
                "stance": "supports",
                "strength": 0.8,
                "note": "",
            }],
        }

    def test_topic_queue_and_merge_targets_are_strict_unless_reuse_is_explicit(self):
        first = self.create_topic("First topic")
        second = self.create_topic("Second topic")
        first_item = self.create_intake("alpha")
        second_item = self.create_intake("beta")
        self.link(first, "intake", first_item["id"])
        self.link(second, "intake", second_item["id"])

        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            session.add_all([
                Event(id="event_first", title="First event", summary="First", event_type="incident", start_at=now, location_name="", importance="medium", status="confirmed", confidence=0.7, metadata_json={}),
                Event(id="event_second", title="Second event", summary="Second", event_type="incident", start_at=now, location_name="", importance="medium", status="confirmed", confidence=0.7, metadata_json={}),
            ])
            session.commit()
        self.link(first, "event", "event_first")
        self.link(second, "event", "event_second")

        first_tasks = self.client.get(
            f"/pldr-api/v1/investigations/{first}/tasks"
        ).json()
        self.assertEqual(first_tasks["count"], 1)
        self.assertEqual(first_tasks["items"][0]["intake_item"]["id"], first_item["id"])
        self.assertNotIn("material", first_tasks["items"][0]["intake_item"])
        self.assertNotIn("candidates", first_tasks["items"][0]["intake_item"])
        scoped_detail = self.client.get(
            f"/pldr-api/v1/investigations/{first}/intake/{first_item['id']}"
        )
        self.assertEqual(scoped_detail.status_code, 200, scoped_detail.text)
        self.assertIn("material", scoped_detail.json())
        self.assertTrue(scoped_detail.json()["candidates"])
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/investigations/{second}/intake/{first_item['id']}"
            ).status_code,
            404,
        )

        strict = self.client.get(
            f"/pldr-api/v1/investigations/{first}/review-options"
        ).json()
        self.assertEqual([event["id"] for event in strict["events"]], ["event_first"])
        self.assertEqual(strict["scope"]["mode"], "investigation-only")

        reusable = self.client.get(
            f"/pldr-api/v1/investigations/{first}/review-options?include_reusable=true"
        ).json()
        foreign = next(event for event in reusable["events"] if event["id"] == "event_second")
        self.assertTrue(foreign["reusable"])
        self.assertEqual(foreign["investigations"][0]["id"], second)

        request = self.confirmation(first_item, disposition="merge", merge_event_id="event_second")
        blocked = self.client.post(
            f"/pldr-api/v1/investigations/{first}/intake/{first_item['id']}/preview",
            json=request,
        )
        self.assertEqual(blocked.status_code, 200, blocked.text)
        self.assertFalse(blocked.json()["confirmable"])
        self.assertIn("outside this investigation", " ".join(blocked.json()["errors"]))

        explicit = self.client.post(
            f"/pldr-api/v1/investigations/{first}/intake/{first_item['id']}/preview?allow_cross_investigation=true",
            json=request,
        )
        self.assertTrue(explicit.json()["confirmable"], explicit.text)
        self.assertEqual(explicit.json()["scope"]["merge_event"]["investigations"][0]["id"], second)

    def test_investigation_directory_and_activity_support_complete_paging(self):
        topics = [self.create_topic(f"Paged topic {index}") for index in range(3)]
        first_page = self.client.get("/pldr-api/v1/investigations?offset=0&limit=2")
        second_page = self.client.get("/pldr-api/v1/investigations?offset=2&limit=2")
        self.assertEqual(first_page.status_code, 200, first_page.text)
        self.assertEqual(second_page.status_code, 200, second_page.text)
        self.assertEqual(first_page.json()["count"], 3)
        self.assertEqual(second_page.json()["count"], 3)
        returned_ids = {item["id"] for item in first_page.json()["items"] + second_page.json()["items"]}
        self.assertEqual(returned_ids, set(topics))

        for marker in ("one", "two", "three"):
            item = self.create_intake(f"activity-{marker}")
            self.link(topics[0], "intake", item["id"])
        activity_ids = set()
        offset = 0
        total = None
        while total is None or offset < total:
            page = self.client.get(
                f"/pldr-api/v1/investigations/{topics[0]}/activity?offset={offset}&limit=2"
            )
            self.assertEqual(page.status_code, 200, page.text)
            payload = page.json()
            total = payload["count"]
            activity_ids.update(item["id"] for item in payload["items"])
            offset += len(payload["items"])
            self.assertTrue(payload["items"] or offset >= total)
        self.assertGreaterEqual(total, 3)
        self.assertEqual(len(activity_ids), total)

    def test_semantic_preview_and_confirmation_return_navigation_and_next_task(self):
        topic = self.create_topic("Review flow")
        first = self.create_intake("one")
        second = self.create_intake("two")
        self.link(topic, "intake", first["id"])
        self.link(topic, "intake", second["id"])
        request = self.confirmation(first)
        request["event"].update({
            "event_type": "transport-disruption",
            "start_at": "2026-08-30T08:30:00Z",
            "location_name": "North crossing",
            "importance": "high",
        })
        request["claims"][0].update({
            "status": "contested",
            "confidence": 0.65,
            "temporal_scope": "2026-08-30 morning",
        })
        request["evidence"][0].update({
            "stance": "context",
            "strength": 0.75,
            "note": "Analyst checked the exact quote.",
        })

        preview = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{first['id']}/preview",
            json=request,
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        semantic = preview.json()["semantic_preview"]
        for key in ("source", "document", "snapshot", "event", "claims", "evidence", "relations", "actions"):
            self.assertIn(key, semantic)
        self.assertTrue(semantic["candidate_generation"]["degraded"])
        self.assertEqual(semantic["event"]["event_type"], "transport-disruption")
        self.assertEqual(semantic["event"]["importance"], "high")
        self.assertEqual(semantic["claims"][0]["status"], "contested")
        self.assertEqual(semantic["claims"][0]["confidence"], 0.65)
        self.assertEqual(semantic["claims"][0]["temporal_scope"], "2026-08-30 morning")
        self.assertEqual(semantic["evidence"][0]["strength"], 0.75)
        self.assertEqual(semantic["evidence"][0]["note"], "Analyst checked the exact quote.")

        confirmed = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{first['id']}/confirm",
            json=request,
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        payload = confirmed.json()
        self.assertTrue(payload["final_event_id"])
        self.assertEqual(payload["result"]["final_event_id"], payload["final_event_id"])
        self.assertEqual(payload["event_url"], f"/pldr-api/v1/events/{payload['final_event_id']}")
        self.assertEqual(payload["next_task"]["intake_item"]["id"], second["id"])

    def test_topic_event_time_window_blocks_out_of_scope_confirmation(self):
        topic_response = self.client.post(
            "/pldr-api/v1/investigations",
            json={
                "title": "August incident window",
                "question": "Which incidents occurred in the selected period?",
                "tracking_mode": "one_time",
                "event_start_at": "2026-08-15T00:00:00Z",
                "event_end_at": "2026-08-31T23:59:59Z",
            },
        )
        self.assertEqual(topic_response.status_code, 201, topic_response.text)
        topic = topic_response.json()["id"]
        item = self.create_intake("time-window")
        self.link(topic, "intake", item["id"])
        request = self.confirmation(item)

        request["event"]["start_at"] = "2026-08-11T00:00:00Z"
        before = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/preview",
            json=request,
        )
        self.assertEqual(before.status_code, 200, before.text)
        self.assertFalse(before.json()["confirmable"])
        self.assertIn("earlier than investigation start", " ".join(before.json()["errors"]))

        request["event"]["start_at"] = "2026-09-01T00:00:00Z"
        after = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/preview",
            json=request,
        )
        self.assertFalse(after.json()["confirmable"])
        self.assertIn("later than investigation end", " ".join(after.json()["errors"]))

        request["event"]["start_at"] = "2026-08-20T00:00:00Z"
        within = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/preview",
            json=request,
        )
        self.assertTrue(within.json()["confirmable"], within.text)

    def test_topic_outcome_contains_only_confirmed_results_and_tracks_report_changes(self):
        topic = self.create_topic("User-facing outcome")
        first = self.create_intake("outcome-one")
        self.link(topic, "intake", first["id"])

        empty = self.client.get(f"/pldr-api/v1/investigations/{topic}/outcome")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["current_answer"]["status"], "empty")
        self.assertEqual(empty.json()["events"], [])
        self.assertEqual(empty.json()["claims"], [])
        self.assertEqual(empty.json()["counts"]["waiting_for_review"], 1)

        confirmed = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{first['id']}/confirm",
            json=self.confirmation(first),
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        first_event_id = confirmed.json()["final_event_id"]

        outcome = self.client.get(f"/pldr-api/v1/investigations/{topic}/outcome")
        self.assertEqual(outcome.status_code, 200, outcome.text)
        payload = outcome.json()
        self.assertEqual(payload["current_answer"]["status"], "available")
        self.assertEqual([event["id"] for event in payload["events"]], [first_event_id])
        self.assertEqual(payload["counts"]["events"], 1)
        self.assertEqual(payload["counts"]["claims"], 1)
        self.assertEqual(payload["counts"]["evidence"], 1)
        self.assertEqual(payload["claims"][0]["event_id"], first_event_id)
        self.assertTrue(payload["claims"][0]["evidence"][0]["snapshot_url"])
        self.assertEqual(payload["current_answer"]["basis"], "single_source_claims")
        self.assertIn("据当前仅有一个独立来源支持的材料", payload["current_answer"]["text"])
        self.assertIn(payload["claims"][0]["text"], payload["current_answer"]["text"])

        report = self.client.post(
            "/pldr-api/v1/reports",
            json={"investigation_id": topic, "title": "Outcome baseline"},
        )
        self.assertEqual(report.status_code, 200, report.text)
        report_page = self.client.get(report.json()["url"])
        self.assertEqual(report_page.status_code, 200, report_page.text)
        self.assertIn("这是生成时的冻结版本", report_page.text)
        self.assertIn("1 条关键信息目前只有一个独立来源", report_page.text)
        self.assertIn("关键发现", report_page.text)
        self.assertIn("来源附录", report_page.text)
        self.assertIn("据当前仅有一个独立来源支持的材料", report_page.text)
        self.assertIn("The public dispatch outcome-one states", report_page.text)
        self.assertNotIn("SHA-256", report_page.text)
        self.assertNotIn(
            'target="_blank" rel="noopener">查看保存的原文',
            report_page.text,
        )
        baseline = self.client.get(f"/pldr-api/v1/investigations/{topic}/outcome").json()
        self.assertEqual(baseline["changes"]["basis"], "latest_report")
        self.assertEqual(baseline["changes"]["new_event_count"], 0)

        second = self.create_intake("outcome-two")
        self.link(topic, "intake", second["id"])
        second_confirmation = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{second['id']}/confirm",
            json=self.confirmation(second),
        )
        self.assertEqual(second_confirmation.status_code, 200, second_confirmation.text)
        changed = self.client.get(f"/pldr-api/v1/investigations/{topic}/outcome").json()
        self.assertEqual(changed["changes"]["new_event_count"], 1)
        self.assertEqual(
            changed["changes"]["new_event_ids"],
            [second_confirmation.json()["final_event_id"]],
        )

    def test_topic_reorganization_groups_pages_and_report_does_not_repeat_articles(self):
        topic = self.create_topic("Grouped topic")
        confirmed_event_ids = []
        for marker in ("source-one", "source-two"):
            item = self.create_intake(marker)
            self.link(topic, "intake", item["id"])
            response = self.client.post(
                f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/confirm",
                json=self.confirmation(item),
            )
            self.assertEqual(response.status_code, 200, response.text)
            confirmed_event_ids.append(response.json()["final_event_id"])

        before = self.client.get(f"/pldr-api/v1/investigations/{topic}/outcome").json()
        self.assertEqual(before["counts"]["events"], 2)
        evidence_ids = [claim["evidence"][0]["id"] for claim in before["claims"]]
        model_result = {
            "mode": "api",
            "model": "test-synthesis-model",
            "result": {
                "current_answer": "两份独立公开资料均显示该通道在检查后恢复运行。",
                "groups": [{
                    "title": "受监测通道在检查后恢复运行",
                    "summary": "两份资料描述的是同一次通道恢复事件，而不是两个网页事件。",
                    "event_time": None,
                    "location_name": None,
                    "source_event_ids": confirmed_event_ids,
                    "findings": [{
                        "text": "受监测通道在独立检查确认安全后恢复运行。",
                        "evidence_ids": evidence_ids,
                    }],
                }],
                "information_gaps": ["仍需确认恢复运行的准确时间。"],
            },
        }
        with patch("pldr_api.llm.run_model_task", new=AsyncMock(return_value=model_result)):
            preview = self.client.post(
                f"/pldr-api/v1/investigations/{topic}/reorganization/preview"
            )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["source_event_count"], 2)
        self.assertEqual(preview.json()["proposed_event_count"], 1)
        unchanged = self.client.get(f"/pldr-api/v1/investigations/{topic}/outcome").json()
        self.assertEqual(unchanged["counts"]["events"], 2)

        confirmed = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/reorganization/confirm",
            json={"draft_id": preview.json()["draft_id"], "actor": "topic-user"},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertTrue(confirmed.json()["created"])
        after = self.client.get(f"/pldr-api/v1/investigations/{topic}/outcome").json()
        self.assertEqual(after["counts"]["events"], 1)
        self.assertEqual(after["current_answer"]["basis"], "confirmed_topic_synthesis")
        self.assertEqual(after["claims"][0]["status"], "supported")
        self.assertEqual(after["claims"][0]["source_verification"]["independent_source_count"], 2)
        self.assertEqual(after["claims"][0]["text"], "受监测通道在独立检查确认安全后恢复运行。")

        report = self.client.post(
            "/pldr-api/v1/reports",
            json={"investigation_id": topic, "title": "Grouped report"},
        )
        self.assertEqual(report.status_code, 200, report.text)
        page = self.client.get(report.json()["url"]).text
        self.assertIn("两份独立公开资料均显示", page)
        self.assertIn("多源印证", page)
        self.assertIn("按真实事件归并资料，不按网页逐篇复述", page)
        # Source pages appear only as exact evidence excerpts, never as one
        # report section per article.
        self.assertEqual(page.count("The public dispatch source-one states"), 1)
        self.assertEqual(page.count("The public dispatch source-two states"), 1)
        self.assertIn('<div class="evidence supports">', page)

        repeated = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/reorganization/confirm",
            json={"draft_id": preview.json()["draft_id"], "actor": "topic-user"},
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertFalse(repeated.json()["created"])

    def test_preview_uses_the_same_defaults_as_confirmed_formal_objects(self):
        topic = self.create_topic("Preview defaults")
        item = self.create_intake("defaults", published_at="2026-08-29T09:30:00Z")
        # Publication time belongs to the source document. The deterministic
        # fallback must not suggest it as event time, and a blank analyst field
        # must remain unknown through preview and confirmation.
        event_candidate = next(candidate for candidate in item["candidates"] if candidate["object_type"] == "event")
        self.assertIsNone(event_candidate["machine"]["fields"]["event_time"])
        self.link(topic, "intake", item["id"])
        request = self.confirmation(item)
        request["event"].update({"summary": "", "start_at": None, "location_name": "Unknown"})
        request["evidence"][0]["note"] = ""

        invalid_time = {**request, "event": {**request["event"], "start_at": "not-a-date"}}
        invalid_preview = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/preview",
            json=invalid_time,
        )
        self.assertEqual(invalid_preview.status_code, 200, invalid_preview.text)
        self.assertFalse(invalid_preview.json()["confirmable"])
        self.assertIn("ISO-8601", " ".join(invalid_preview.json()["errors"]))

        preview = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/preview",
            json=request,
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        semantic = preview.json()["semantic_preview"]
        self.assertEqual(semantic["event"]["summary"], "Analyst-confirmed intake material.")
        self.assertIsNone(semantic["event"]["start_at"])
        self.assertEqual(semantic["event"]["location_name"], "")
        self.assertEqual(
            semantic["evidence"][0]["note"],
            "Human-confirmed from isolated intake candidate; machine candidate retained in intake.",
        )

        confirmed = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/confirm",
            json=request,
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        event = self.client.get(confirmed.json()["event_url"]).json()
        self.assertEqual(event["summary"], semantic["event"]["summary"])
        self.assertEqual(event["start_at"], semantic["event"]["start_at"])
        self.assertEqual(event["location"]["name"], semantic["event"]["location_name"])
        self.assertEqual(event["claims"][0]["evidence"][0]["note"], semantic["evidence"][0]["note"])
        with SessionLocal() as session:
            stored_event = session.get(Event, confirmed.json()["final_event_id"])
            self.assertFalse(stored_event.metadata_json["start_at_known"])

    def test_merge_preview_shows_the_reused_entity_and_claim_values(self):
        topic = self.create_topic("Merge preview")
        item = self.create_intake("merge-preview")
        self.link(topic, "intake", item["id"])
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            session.add(Event(
                id="event_merge_preview", title="Existing formal event", summary="Existing event summary",
                event_type="inspection", start_at=now, location_name="Existing location",
                importance="high", status="confirmed", confidence=0.8, metadata_json={"start_at_known": True},
            ))
            session.add(Entity(
                id="entity_merge_preview", name="Existing entity", entity_type="organization",
                aliases=["Existing alias"],
            ))
            session.add(EventEntity(
                event_id="event_merge_preview", entity_id="entity_merge_preview", role="existing-role",
            ))
            session.add(Claim(
                id="claim_merge_preview", event_id="event_merge_preview", text="Existing formal claim",
                status="supported", confidence=0.91, origin="human-confirmed", temporal_scope="existing period",
            ))
            session.add(IntakeCandidate(
                id=f"{item['id']}:entity:manual", item_id=item["id"], candidate_key="entity:manual",
                object_type="entity", source_mode="fallback",
                machine_data={"fields": {"name": "Candidate entity"}},
            ))
            session.commit()
        self.link(topic, "event", "event_merge_preview")

        request = self.confirmation(item, disposition="merge", merge_event_id="event_merge_preview")
        request["entities"] = [{
            "candidate_key": "entity:manual", "action": "merge", "name": "Wrong candidate name",
            "entity_type": "person", "aliases": ["Wrong alias"], "role": "subject",
            "merge_entity_id": "entity_merge_preview",
        }]
        request["claims"][0].update({
            "action": "merge", "text": "Wrong candidate claim", "status": "contested",
            "confidence": 0.1, "temporal_scope": "wrong period", "merge_claim_id": "claim_merge_preview",
        })
        preview = self.client.post(
            f"/pldr-api/v1/investigations/{topic}/intake/{item['id']}/preview",
            json=request,
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["confirmable"], preview.text)
        semantic = preview.json()["semantic_preview"]
        self.assertEqual(semantic["entities"][0]["name"], "Existing entity")
        self.assertEqual(semantic["entities"][0]["entity_type"], "organization")
        self.assertEqual(semantic["entities"][0]["aliases"], ["Existing alias"])
        self.assertEqual(semantic["entities"][0]["role"], "subject")
        self.assertEqual(semantic["claims"][0]["text"], "Existing formal claim")
        self.assertEqual(semantic["claims"][0]["status"], "supported")
        self.assertEqual(semantic["claims"][0]["confidence"], 0.91)
        self.assertEqual(semantic["claims"][0]["temporal_scope"], "existing period")

    def test_error_contract_and_legacy_generation_failure_remain_reviewable(self):
        cases = [
            ("unsafe_url", "Non-public address is blocked: 2001::1", "dns_policy_blocked"),
            ("http_401", "HTTP 401", "http_401"),
            ("http_403", "HTTP 403", "http_403"),
            ("http_429", "HTTP 429", "http_429"),
            ("unsupported_content_type", "application/octet-stream", "unsupported_content_type"),
            ("extraction", "Extracted page body is too short", "empty_or_short_body"),
            ("model_fallback", "Model request exceeded 90 second total deadline", "model_timeout_fallback"),
            ("model_fallback", "Model returned invalid output", "model_error_fallback"),
            ("rule_fallback", None, "rule_fallback"),
        ]
        for error_class, message, code in cases:
            with self.subTest(code=code):
                error = _structured_task_error(error_class, message, task_status="failed")
                self.assertEqual(error["code"], code)
                self.assertTrue(error["title"])
                self.assertTrue(error["impact"])
                self.assertTrue(error["next_action"])
                if code == "dns_policy_blocked":
                    self.assertFalse(error["retryable"])

        topic = self.create_topic("Legacy recovery")
        item = self.create_intake("legacy")
        self.link(topic, "intake", item["id"])
        with SessionLocal() as session:
            stored = session.get(IntakeItem, item["id"])
            task = session.scalar(select(ReviewTask).where(ReviewTask.intake_item_id == item["id"]))
            stored.status = "generation_failed"
            stored.candidate_mode = "failed"
            stored.candidate_error = "legacy model generation failed"
            task.status = "failed"
            task.error_class = "intake_failed"
            task.error_message = stored.candidate_error
            task.active_key = None
            session.commit()
            task_id = task.id

        retried = self.client.post(f"/pldr-api/v1/tasks/{task_id}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        with patch(
            "pldr_api.intake.run_model_task",
            new=AsyncMock(side_effect=TimeoutError("model timeout")),
        ):
            asyncio.run(run_review_task_once(worker_id="topic-ux-worker"))
        failed = self.client.get(f"/pldr-api/v1/tasks/{task_id}").json()
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["fallback_used"])
        self.assertTrue(failed["retryable"])
        self.assertEqual(failed["error"]["code"], "model_timeout")
        self.assertEqual(failed["error"]["trace_id"], task_id)
        self.assertEqual(failed["error_message"], failed["last_error"])
        self.assertEqual(failed["intake_item"]["candidate_generation"]["mode"], "failed")
        self.assertEqual(failed["intake_item"]["candidates"], [])

    def test_topic_review_task_retry_uses_canonical_task_state_machine(self):
        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200, script.text)
        source = script.text

        render_start = source.index("function renderTaskRows(")
        render_end = source.index("\nfunction renderMiniMap(", render_start)
        render_task_rows = source[render_start:render_end]

        canonical_retry = '} else if (canRetryTask) {'
        legacy_intake_retry = 'intake?.status === "generation_failed"'
        legacy_search_retry = 'intake?.search?.result_id'
        self.assertIn(canonical_retry, render_task_rows)
        self.assertLess(
            render_task_rows.index(canonical_retry),
            render_task_rows.index(legacy_intake_retry),
        )
        self.assertLess(
            render_task_rows.index(canonical_retry),
            render_task_rows.index(legacy_search_retry),
        )
        self.assertIn(
            'data-investigation-action="retry-task" data-task-id=',
            render_task_rows,
        )

        retry_start = source.index("async function retryInvestigationTask(")
        retry_end = source.index("\nasync function generateInvestigationReport(", retry_start)
        retry_handler = source[retry_start:retry_end]
        self.assertIn(
            "await api(API_ROUTES.taskRetry(taskId),",
            retry_handler,
        )
        self.assertIn(
            'taskRetry: (id) => `/pldr-api/v1/tasks/${encodeURIComponent(id)}/retry`',
            source,
        )
        self.assertIn('task?.waiting_for_model_retry ? "等待 AI 重试"', source)
        self.assertIn("后台会继续处理，无需手动操作", source)

    def test_topic_onboarding_presents_one_clear_human_confirmation_flow(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200, page.text)
        html = page.text
        for label in ("专题成果", "待处理", "资料与来源"):
            self.assertIn(label, html)
        self.assertIn("进行中专题待处理", html)
        self.assertIn("已删除专题和系统待归类不会混入", html)
        self.assertIn("只汇总当前专题", html)
        self.assertIn("全局待确认", html)
        self.assertLess(
            html.index('data-investigation-tab="outcomes"'),
            html.index('data-investigation-tab="overview"'),
        )
        self.assertLess(
            html.index('data-investigation-tab="overview"'),
            html.index('data-investigation-tab="materials"'),
        )
        self.assertNotIn('data-investigation-tab="review"', html)
        self.assertNotIn('id="investigation-more-menu"', html)
        self.assertIn("创建专题并开始收集", html)
        self.assertIn("1 · 调查目标", html)
        self.assertIn("2 · 首批资料", html)
        self.assertIn("3 · 更新方式", html)
        self.assertLess(html.index("1 · 调查目标"), html.index("2 · 首批资料"))
        self.assertLess(html.index("2 · 首批资料"), html.index("3 · 更新方式"))
        self.assertIn("指事件发生时间，不是新闻发布时间", html)
        self.assertIn("四种方式可以同时使用", html)
        self.assertIn('id="investigation-create-source-urls"', html)
        self.assertIn('id="investigation-create-text"', html)
        self.assertIn('id="investigation-create-files"', html)
        self.assertIn("创建专题并开始", html)
        for hidden_language_control in (
            'id="investigation-create-source-language"',
            'id="investigation-create-report-language"',
            'id="search-language"',
            'id="import-language"',
            'id="collection-language"',
        ):
            self.assertNotIn(hidden_language_control, html)

        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200, script.text)
        source = script.text
        self.assertIn("function suggestedInvestigationQuestion", source)
        suggestion = source[
            source.index("function suggestedInvestigationQuestion"):
            source.index("function syncInvestigationStarterSuggestions")
        ]
        self.assertIn("目前发生了什么", suggestion)
        self.assertNotIn("主张和证据", suggestion)
        self.assertIn("function normalizeEventTimeForConfirmation", source)
        self.assertIn("系统无法可靠识别候选时间，已留空", source)
        self.assertIn("事件时间格式无法识别。请填写 YYYY-MM-DD", source)
        self.assertIn("async function startInitialTopicCollection", source)
        self.assertIn("function renderOutcomeHero", source)
        self.assertIn("function renderOutcomeFindings", source)
        self.assertIn("function outcomeSourceSearchSeed", source)
        self.assertIn('data-investigation-find-source="${escapeHtml(outcomeSourceSearchSeed', source)
        self.assertIn("if (keyword && autoStart)", source)
        search_modal = source[
            source.index("async function openExternalSearchModal"):
            source.index("function closeExternalSearchModal")
        ]
        self.assertLess(
            search_modal.index("switchSearchWorkspaceContext(destinationId)"),
            search_modal.index("const openSerial = state.searchRequestSerial"),
        )
        self.assertIn('.filter((event) => event.status === "confirmed")', source)
        self.assertIn("investigationOutcome: (id)", source)
        self.assertIn('data-intake-action="accept"', source)
        self.assertIn('>加入专题</button>', source)
        self.assertIn('data-intake-action="modify">修改', source)
        self.assertIn('data-intake-action="reject-toggle"', source)
        self.assertIn('data-intake-batch="accept"', source)
        self.assertIn('data-intake-batch="reject"', source)
        batch_review = source[
            source.index("async function handleIntakeBatch"):
            source.index("function renderReadonlyCandidate")
        ]
        self.assertIn("await loadInvestigationWorkspace(workspaceId, { quiet: true })", batch_review)
        single_review = source[
            source.index("async function handleIntakeAction"):
            source.index("function collectionMetrics")
        ]
        self.assertLess(
            single_review.index("await loadInvestigationWorkspace(actionScope, { quiet: true })"),
            single_review.index("if (!actionIsCurrent()) return", single_review.index('if (action === "reject")')),
        )
        self.assertIn("function candidateConfirmationDefaults", source)
        self.assertIn("evidence.filter((candidate) => !candidate.validation_error)", source)
        self.assertIn('action: included ? "create" : "exclude"', source)
        self.assertIn('action: defaults.evidenceKeys.has(candidate.candidate_key) ? "include" : "exclude"', source)
        self.assertIn("已自动避开无法回到原文的内容", source)
        self.assertIn("其余有效内容仍可直接加入专题", source)
        self.assertIn("该事件发生在专题设定的开始时间之前", source)
        self.assertIn("系统结合材料年份补全", source)
        self.assertIn("window.location.assign(result.url)", source)
        outcome_findings = source[
            source.index("function renderOutcomeFindings"):
            source.index("function renderOutcomeTimeline")
        ]
        outcome_reports = source[
            source.index("function renderOutcomeReportHistory"):
            source.index("function renderInvestigationOutcomes")
        ]
        self.assertNotIn('target="_blank"', outcome_findings)
        self.assertNotIn('target="_blank"', outcome_reports)
        self.assertIn('data-investigation-action="archive-topic"', source)
        self.assertIn('data-investigation-action="restore-topic"', source)
        self.assertIn('ACTIVE_INTAKE_STATUSES.has(item.status)', source)
        self.assertNotIn("材料指纹", source)
        self.assertNotIn("提取文本快照（SHA-256", source)
        for deprecated_label in ("待我处理", "待我确认"):
            self.assertNotIn(deprecated_label, html)
            self.assertNotIn(deprecated_label, source)
        self.assertNotIn("MY QUEUE", html)
        self.assertNotIn("MY ACTIONS", source)
        self.assertNotIn("预览入档", source)
        self.assertIn("searchPayloadResults(searchPayload)", source)
        self.assertIn('result.topic_relevance?.level === "likely"', source)
        self.assertIn("function searchResultRelevance", source)
        self.assertIn("function taskBelongsInPending", source)
        self.assertIn('task?.selection_origin !== "topic_onboarding"', source)
        self.assertIn("taskIsActive(task) && taskBelongsInPending(task)", source)
        self.assertIn("taskBelongsInPending(task) || taskIntakeId(task) === preferredItemId", source)
        home_queue = source[
            source.index("function allHomeAssignments()") : source.index("function renderDestinationPickers", source.index("function allHomeAssignments()"))
        ]
        self.assertIn("const hasServerBackedDirectory", home_queue)
        self.assertIn("if (!hasServerBackedDirectory)", home_queue)
        self.assertIn('if (investigation.status === "archived") return', home_queue)
        self.assertIn('["demo", "system"].includes(investigation.sync_mode)', home_queue)
        self.assertLess(
            home_queue.index("seenIntake.add(intakeId)"),
            home_queue.index("if (!taskBelongsInPending(task)) return"),
        )
        archive_handler = source[
            source.index('if (action === "archive-topic")') : source.index('if (action === "search")', source.index('if (action === "archive-topic")'))
        ]
        self.assertIn('status: "archived"', archive_handler)
        self.assertIn("state.investigationTasks.delete(investigation.id)", archive_handler)
        self.assertIn("state.investigationTaskErrors.delete(investigation.id)", archive_handler)
        self.assertLess(
            archive_handler.index("state.investigationTasks.delete(investigation.id)"),
            archive_handler.index("showInvestigationHome()"),
        )
        self.assertIn('item.status !== "archived"', source)
        self.assertIn("相关性存疑", source)
        self.assertIn("未通过相关性初筛的结果仍保留在这里", source)
        self.assertIn('data-investigation-action="open-search-run"', source)
        self.assertIn("await api(API_ROUTES.searchSelect", source)
        self.assertIn('`${feed ? "/pldr-api/v1/import/rss" : "/pldr-api/v1/import/url"}?defer_candidates=true`', source)
        self.assertIn('await api("/pldr-api/v1/intake/text?defer_candidates=true"', source)
        self.assertIn('await api("/pldr-api/v1/intake/files?defer_candidates=true"', source)
        self.assertIn('report_language: "zh-CN"', source)
        self.assertIn('source_language: "auto"', source)
        self.assertIn('if (action === "review") return openIntakeModal', source)
        self.assertIn("不会把搜索摘要或 AI 草稿直接写成正式结论", html)


if __name__ == "__main__":
    unittest.main()
