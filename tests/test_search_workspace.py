from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

SEARCH_TEST_ROOT = Path(tempfile.mkdtemp(prefix="pldr-search-workspace-tests-"))
os.environ["PLDR_DATABASE_URL"] = f"sqlite:///{SEARCH_TEST_ROOT / 'search.db'}"
os.environ["PLDR_REPORT_DIR"] = str(SEARCH_TEST_ROOT / "reports")


class SearchWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import lazily so the test suite's process-global PLDR database choice is
        # settled before this module opens a client.
        global Base, SessionLocal, engine, app
        global BackendSearchResponse, ExternalSearchError, _normalize_hit
        global effective_search_language
        from pldr_api.database import Base, SessionLocal, engine
        from pldr_api.main import app
        from pldr_api.search import (
            BackendSearchResponse,
            ExternalSearchError,
            _normalize_hit,
            effective_search_language,
        )

        database_path = Path(str(engine.url.database)).resolve()
        cls.owns_test_root = SEARCH_TEST_ROOT in database_path.parents
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        if cls.owns_test_root:
            engine.dispose()
            shutil.rmtree(SEARCH_TEST_ROOT, ignore_errors=True)

    def setUp(self):
        database_path = Path(str(engine.url.database)).resolve()
        if not any("test" in part for part in database_path.parts):
            self.fail(f"Refusing to reset non-test database: {database_path}")
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.search_env = {
            key: os.environ.get(key)
            for key in ("PLDR_SEARCH_PROVIDER", "PLDR_SEARCH_BASE_URL")
        }
        os.environ["PLDR_SEARCH_PROVIDER"] = "searxng"
        os.environ["PLDR_SEARCH_BASE_URL"] = "http://127.0.0.1:8888"

    def tearDown(self):
        for key, value in self.search_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @staticmethod
    def hit(number: int):
        return _normalize_hit(
            {
                "url": f"https://source.example.org/item/{number}?utm_source=test",
                "title": f"Result {number}",
                "content": f"Search metadata for result {number}",
                "engine": "unit",
            },
            provider="searxng",
        )

    def create_investigation(self, title: str) -> str:
        response = self.client.post(
            "/pldr-api/v1/investigations",
            json={"title": title, "question": "What changed?"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_three_pages_persist_deduplicate_reopen_and_select_25(self):
        self.assertEqual(effective_search_language("中文", "zh", "brave"), "zh-hans")
        self.assertEqual(effective_search_language("中文", "zh-CN", "brave"), "zh-hans")
        self.assertEqual(effective_search_language("中文", "zh-TW", "brave"), "zh-hant")
        self.assertEqual(effective_search_language("中文", "zh", "searxng"), "zh-CN")
        calls: list[tuple[int, str]] = []

        async def backend(_, request):
            calls.append((request.page, request.language))
            if request.page == 1:
                hits = [self.hit(number) for number in range(1, 21)]
                return BackendSearchResponse("searxng", "searxng:web", hits, True)
            if request.page == 2:
                # Provider pages can overlap. The duplicate must not consume a
                # loaded-result slot or create another persisted row.
                hits = [self.hit(20), *[self.hit(number) for number in range(21, 41)]]
                return BackendSearchResponse("searxng", "searxng:web", hits, True)
            hits = [self.hit(40), *[self.hit(number) for number in range(41, 46)]]
            return BackendSearchResponse("searxng", "searxng:web", hits, False)

        investigation_id = self.create_investigation("Workspace topic")
        with patch("pldr_api.search.request_search", new=backend):
            first = self.client.post(
                "/pldr-api/v1/search",
                json={
                    "keyword": "吉隆泥石流",
                    "language": "auto",
                    "scope": "web",
                    "limit": 20,
                    "investigation_id": investigation_id,
                },
            )
            self.assertEqual(first.status_code, 200, first.text)
            run_id = first.json()["query_run_id"]
            second = self.client.post(
                "/pldr-api/v1/search",
                json={
                    "keyword": "吉隆泥石流",
                    "language": "auto",
                    "scope": "web",
                    "limit": 20,
                    "pageno": 2,
                    "query_run_id": run_id,
                    "investigation_id": investigation_id,
                },
            )
            third = self.client.post(
                "/pldr-api/v1/search",
                json={
                    "keyword": "吉隆泥石流",
                    "language": "auto",
                    "scope": "web",
                    "limit": 20,
                    "cursor": "3",
                    "query_run_id": run_id,
                    "investigation_id": investigation_id,
                },
            )

        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(third.status_code, 200, third.text)
        self.assertEqual(calls, [(1, "zh-CN"), (2, "zh-CN"), (3, "zh-CN")])
        self.assertEqual(first.json()["returned_count"], 20)
        self.assertEqual(second.json()["returned_count"], 20)
        payload = third.json()
        self.assertEqual(payload["page"], 3)
        self.assertEqual(payload["loaded_count"], 45)
        self.assertEqual(payload["result_count"], 45)
        self.assertEqual(payload["returned_count"], 5)
        self.assertFalse(payload["has_more"])
        self.assertFalse(payload["total_known"])
        self.assertIsNone(payload["available_count"])
        self.assertEqual(payload["items"], payload["results"])
        self.assertEqual(len({item["canonical_url"] for item in payload["results"]}), 45)
        self.assertEqual([item["rank"] for item in payload["results"]], list(range(1, 46)))

        reopened = self.client.get(
            f"/pldr-api/v1/search/runs/{run_id}",
            params={"investigation_id": investigation_id},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["results"], payload["results"])
        os.environ["PLDR_SEARCH_PROVIDER"] = "brave"
        provider_switched = self.client.get(
            f"/pldr-api/v1/search/runs/{run_id}",
            params={"investigation_id": investigation_id},
        ).json()["provider"]
        self.assertEqual(provider_switched["provider"], "searxng")
        self.assertEqual(provider_switched["current_provider"], "brave")
        self.assertFalse(provider_switched["configured"])
        os.environ["PLDR_SEARCH_PROVIDER"] = "searxng"
        history = self.client.get(
            "/pldr-api/v1/search/runs",
            params={"investigation_id": investigation_id, "limit": 10},
        )
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["count"], 1)
        self.assertEqual(history.json()["runs"][0]["query_run_id"], run_id)
        self.assertEqual(
            self.client.get("/pldr-api/v1/search/runs").status_code, 422
        )
        self.assertEqual(
            self.client.get(f"/pldr-api/v1/search/runs/{run_id}").status_code,
            422,
        )
        continuation_without_topic = self.client.post(
            "/pldr-api/v1/search",
            json={
                "keyword": "吉隆泥石流",
                "query_run_id": run_id,
                "page": 2,
            },
        )
        self.assertEqual(continuation_without_topic.status_code, 422)

        other_id = self.create_investigation("Other topic")
        isolated = self.client.get(
            "/pldr-api/v1/search/runs", params={"investigation_id": other_id}
        )
        self.assertEqual(isolated.json()["count"], 0)
        self.assertEqual(
            self.client.get(
                f"/pldr-api/v1/search/runs/{run_id}",
                params={"investigation_id": other_id},
            ).status_code,
            404,
        )

        selected_ids = [item["id"] for item in payload["results"][:25]]
        legacy_too_large = self.client.post(
            "/pldr-api/v1/search/select",
            json={"result_ids": selected_ids[:21]},
        )
        self.assertEqual(legacy_too_large.status_code, 422)
        selected = self.client.post(
            "/pldr-api/v1/search/select",
            json={
                "result_ids": selected_ids,
                "request_id": "workspace-select-25",
                "investigation_id": investigation_id,
            },
        )
        self.assertEqual(selected.status_code, 202, selected.text)
        self.assertEqual(selected.json()["batch"]["requested_count"], 25)
        self.assertEqual(len(selected.json()["tasks"]), 25)

    def test_search_error_is_structured_and_persisted(self):
        investigation_id = self.create_investigation("Failure topic")
        controlled = ExternalSearchError(
            "controlled upstream 429",
            status_code=429,
            reason="rate_limited",
            upstream_status=429,
            retry_after="10",
        )
        with patch(
            "pldr_api.search.request_search", new=AsyncMock(side_effect=controlled)
        ):
            failed = self.client.post(
                "/pldr-api/v1/search",
                json={
                    "keyword": "rate limited query",
                    "scope": "web",
                    "investigation_id": investigation_id,
                },
            )
        self.assertEqual(failed.status_code, 429, failed.text)
        detail = failed.json()["detail"]
        for key in [
            "code",
            "stage",
            "summary",
            "impact",
            "retryable",
            "recommended_action",
            "technical_message",
            "trace_id",
            "upstream_status",
            "retry_after",
        ]:
            self.assertIn(key, detail)
        self.assertEqual(detail["code"], "search.rate_limited")
        self.assertTrue(detail["retryable"])
        self.assertEqual(detail["upstream_status"], 429)
        self.assertEqual(detail["retry_after"], "10")
        reopened = self.client.get(
            f"/pldr-api/v1/search/runs/{detail['query_run_id']}",
            params={"investigation_id": investigation_id},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(
            reopened.json()["error_detail"]["trace_id"], detail["trace_id"]
        )

    def test_failed_first_page_can_retry_same_run(self):
        investigation_id = self.create_investigation("Retry topic")
        calls = 0

        async def flaky_backend(_, request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ExternalSearchError(
                    "controlled timeout",
                    status_code=504,
                    reason="timeout",
                )
            return BackendSearchResponse(
                "searxng", "searxng:web", [self.hit(1)], False
            )

        body = {
            "keyword": "same run retry",
            "scope": "web",
            "investigation_id": investigation_id,
        }
        with patch("pldr_api.search.request_search", new=flaky_backend):
            failed = self.client.post("/pldr-api/v1/search", json=body)
            self.assertEqual(failed.status_code, 504, failed.text)
            detail = failed.json()["detail"]
            self.assertEqual(detail["attempted_page"], 1)
            retried = self.client.post(
                "/pldr-api/v1/search",
                json={
                    **body,
                    "query_run_id": detail["query_run_id"],
                    "page": 1,
                },
            )
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(calls, 2)
        self.assertEqual(retried.json()["loaded_count"], 1)
        self.assertEqual(retried.json()["status"], "ok")

    def test_selection_state_and_result_ids_do_not_cross_topics(self):
        topic_a = self.create_investigation("Topic A")
        topic_b = self.create_investigation("Topic B")

        async def same_url_backend(_, request):
            return BackendSearchResponse(
                "searxng", "searxng:web", [self.hit(77)], False
            )

        with patch("pldr_api.search.request_search", new=same_url_backend):
            run_a = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "alpha topic", "investigation_id": topic_a},
            ).json()
            run_b = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "beta topic", "investigation_id": topic_b},
            ).json()
        selected_b = self.client.post(
            "/pldr-api/v1/search/select",
            json={
                "result_ids": [run_b["results"][0]["id"]],
                "request_id": "select-topic-b",
                "investigation_id": topic_b,
            },
        )
        self.assertEqual(selected_b.status_code, 202, selected_b.text)
        reopened_a = self.client.get(
            f"/pldr-api/v1/search/runs/{run_a['query_run_id']}",
            params={"investigation_id": topic_a},
        )
        self.assertEqual(reopened_a.status_code, 200, reopened_a.text)
        self.assertIsNone(reopened_a.json()["results"][0]["selection"])

        explicit_reuse = self.client.post(
            "/pldr-api/v1/search/select",
            json={
                "result_ids": [run_a["results"][0]["id"]],
                "request_id": "foreign-result-id",
                "investigation_id": topic_b,
            },
        )
        self.assertEqual(explicit_reuse.status_code, 202, explicit_reuse.text)
        self.assertEqual(explicit_reuse.json()["investigation"]["id"], topic_b)

        with SessionLocal() as session:
            from pldr_api.models import SearchSelection

            selection = session.query(SearchSelection).first()
            selection.status = "candidate_ready"
            selection.last_error = "model timeout after controlled deadline"
            selection.intake_item.status = "candidate_ready"
            selection.intake_item.candidate_mode = "fallback-after-error"
            selection.intake_item.candidate_error = selection.last_error
            session.commit()
        fallback_b = self.client.get(
            f"/pldr-api/v1/search/runs/{run_b['query_run_id']}",
            params={"investigation_id": topic_b},
        ).json()["results"][0]["selection"]
        self.assertEqual(fallback_b["error"]["stage"], "generate")
        self.assertTrue(fallback_b["error"]["degraded"])
        self.assertFalse(fallback_b["retryable"])

        with SessionLocal() as session:
            selection = session.query(SearchSelection).first()
            selection.status = "failed"
            selection.last_error = "Non-public address is blocked: 2001::1"
            selection.intake_item.status = "failed"
            selection.intake_item.error = selection.last_error
            selection.intake_item.candidate_mode = None
            selection.intake_item.candidate_error = None
            session.commit()
        reopened_b = self.client.get(
            f"/pldr-api/v1/search/runs/{run_b['query_run_id']}",
            params={"investigation_id": topic_b},
        )
        selection_payload = reopened_b.json()["results"][0]["selection"]
        self.assertEqual(selection_payload["error"]["code"], "dns_policy_blocked")
        self.assertFalse(selection_payload["retryable"])

    def test_searxng_operator_page_is_not_truncated_or_stranded(self):
        import asyncio
        from pldr_api.schemas import ExternalSearchRequest
        from pldr_api.search import SearchProviderConfig, request_searxng_search

        response = Mock(status_code=200, headers={})
        response.json.return_value = {
            "results": [
                {
                    "url": f"https://source.example.org/searx/{index}",
                    "title": f"SearX result {index}",
                    "content": "operator-sized page",
                }
                for index in range(25)
            ]
        }
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        with patch("pldr_api.search.httpx.AsyncClient", return_value=client):
            result = asyncio.run(
                request_searxng_search(
                    SearchProviderConfig(
                        "searxng", "http://127.0.0.1:8888", "", 1
                    ),
                    ExternalSearchRequest(
                        keyword="分页验证",
                        language="auto",
                        page_size=20,
                    ),
                )
            )
        self.assertEqual(len(result.hits), 25)
        self.assertTrue(result.has_more)

        async def oversized_operator_page(_, request):
            return BackendSearchResponse(
                "searxng",
                f"searxng:{request.scope}",
                [self.hit(index) for index in range(1, 26)],
                True,
            )

        investigation_id = self.create_investigation("Operator page topic")
        with patch("pldr_api.search.request_search", new=oversized_operator_page):
            legacy = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "legacy limit", "limit": 5},
            )
            workspace = self.client.post(
                "/pldr-api/v1/search",
                json={
                    "keyword": "workspace page",
                    "limit": 20,
                    "page_size": 20,
                    "investigation_id": investigation_id,
                },
            )

        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(legacy.json()["result_count"], 5)
        self.assertEqual(len(legacy.json()["results"]), 5)
        self.assertEqual(workspace.status_code, 200, workspace.text)
        self.assertEqual(workspace.json()["page_size"], 20)
        self.assertEqual(workspace.json()["returned_count"], 25)
        self.assertEqual(workspace.json()["loaded_count"], 25)
        self.assertEqual(len(workspace.json()["results"]), 25)
        self.assertTrue(workspace.json()["has_more"])

    def test_additive_migration_upgrades_original_search_tables(self):
        from pldr_api import main

        with tempfile.TemporaryDirectory(prefix="pldr-search-migration-test-") as root:
            migration_engine = create_engine(f"sqlite:///{Path(root) / 'old.db'}")
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE external_search_query_runs ("
                        "id VARCHAR(96) PRIMARY KEY, keyword TEXT NOT NULL, "
                        "normalized_keyword TEXT NOT NULL, scope VARCHAR(20), "
                        "provider VARCHAR(60) NOT NULL, channel VARCHAR(100) NOT NULL, "
                        "language VARCHAR(20), status VARCHAR(20), error TEXT, "
                        "result_count INTEGER, latency_ms INTEGER, created_at DATETIME)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE TABLE external_search_results ("
                        "id VARCHAR(112) PRIMARY KEY, query_run_id VARCHAR(96), "
                        "result_fingerprint VARCHAR(64), provider VARCHAR(60), "
                        "channel VARCHAR(100), original_url VARCHAR(900), "
                        "canonical_url VARCHAR(900), site_name VARCHAR(200), "
                        "title TEXT, snippet TEXT, published_at DATETIME, rank INTEGER, "
                        "engine VARCHAR(120), raw_result JSON, created_at DATETIME)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO external_search_query_runs "
                        "(id, keyword, normalized_keyword, scope, provider, channel, "
                        "language, status, result_count, created_at) VALUES "
                        "('legacy', 'old', 'old', 'web', 'searxng', 'searxng:web', "
                        "'en', 'ok', 20, CURRENT_TIMESTAMP)"
                    )
                )
            original_engine = main.engine
            main.engine = migration_engine
            try:
                main.ensure_compatible_schema()
            finally:
                main.engine = original_engine
            query_columns = {
                column["name"]
                for column in inspect(migration_engine).get_columns(
                    "external_search_query_runs"
                )
            }
            result_columns = {
                column["name"]
                for column in inspect(migration_engine).get_columns(
                    "external_search_results"
                )
            }
            self.assertTrue(
                {
                    "error_detail",
                    "current_page",
                    "page_size",
                    "returned_count",
                    "has_more",
                    "total_known",
                    "total_count",
                    "updated_at",
                }.issubset(query_columns)
            )
            self.assertIn("source_page", result_columns)
            with migration_engine.connect() as connection:
                migrated = connection.execute(
                    text(
                        "SELECT page_size, returned_count, current_page "
                        "FROM external_search_query_runs WHERE id='legacy'"
                    )
                ).one()
            self.assertEqual(tuple(migrated), (20, 20, 1))
            # A second startup must preserve the already migrated values.
            original_engine = main.engine
            main.engine = migration_engine
            try:
                main.ensure_compatible_schema()
            finally:
                main.engine = original_engine
            with migration_engine.connect() as connection:
                self.assertEqual(
                    tuple(
                        connection.execute(
                            text(
                                "SELECT page_size, returned_count, current_page "
                                "FROM external_search_query_runs WHERE id='legacy'"
                            )
                        ).one()
                    ),
                    (20, 20, 1),
                )
            migration_engine.dispose()


if __name__ == "__main__":
    unittest.main()
