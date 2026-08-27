from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="pldr-p0-tests-"))
os.environ["PLDR_DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'pldr-p0-test.db'}"
os.environ["PLDR_REPORT_DIR"] = str(TEST_ROOT / "reports")
os.environ.pop("PLDR_ADMIN_TOKEN", None)

from fastapi.testclient import TestClient
from pldr_api.database import Base, SessionLocal, engine
from pldr_api.main import app
from pldr_api.models import Evidence, Source
from pldr_api.security import UnsafeUrlError, validate_public_http_url
from pldr_api.seed import counts, seed_database
from sqlalchemy import select


class P0Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            seed_database(session, force=True)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        engine.dispose()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_admin_reseed_disabled_without_token(self):
        response = self.client.post("/pldr-api/v1/admin/reseed")
        self.assertEqual(response.status_code, 503)

    def test_api_contract(self):
        overview = self.client.get("/pldr-api/v1/overview")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["metrics"]["events"], 8)

        event = self.client.get("/pldr-api/v1/events/evt_grounding")
        self.assertEqual(event.status_code, 200)
        self.assertEqual(len(event.json()["claims"]), 3)
        snapshot_urls = [
            evidence["document"]["snapshot_url"]
            for claim in event.json()["claims"]
            for evidence in claim["evidence"]
        ]
        self.assertTrue(snapshot_urls)
        self.assertTrue(all("event_id=evt_grounding" in url for url in snapshot_urls))

        health = self.client.get("/pldr-api/v1/sources/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["count"], 16)

        config = self.client.get("/pldr-api/v1/config")
        self.assertEqual(config.status_code, 200)
        self.assertTrue(config.json()["pldr_mode"])

    def test_counts_and_independence(self):
        with SessionLocal() as session:
            self.assertEqual(
                counts(session),
                {"sources": 16, "documents": 48, "events": 8, "claims": 24, "evidence": 56},
            )
            groups = {source.independence_group for source in session.scalars(select(Source))}
            self.assertEqual(len(groups), 13)

    def test_duplicate_content_preserves_source_provenance(self):
        html = """
        <html><head><title>Shared dispatch</title></head><body><article>
        <p>This sufficiently long public dispatch is intentionally identical across
        two independent source URLs so PLDR can preserve provenance while recording
        that the article content belongs to the same duplicate family.</p>
        </article></body></html>
        """
        first = self.client.post(
            "/pldr-api/v1/import/url",
            json={
                "url": "https://alpha.example.org/shared-dispatch",
                "source_name": "Alpha Example",
                "html": html,
                "language": "en",
            },
        )
        second = self.client.post(
            "/pldr-api/v1/import/url",
            json={
                "url": "https://bravo.example.net/shared-dispatch",
                "source_name": "Bravo Example",
                "html": html,
                "language": "en",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

        first_document = first.json()["document"]
        second_document = second.json()["document"]
        self.assertNotEqual(first_document["id"], second_document["id"])
        self.assertNotEqual(
            first_document["source"]["independence_group"],
            second_document["source"]["independence_group"],
        )
        self.assertEqual(
            second_document["metadata"]["duplicate_of_document_id"],
            first_document["id"],
        )

    def test_evidence_exact_substrings(self):
        with SessionLocal() as session:
            for evidence in session.scalars(select(Evidence)):
                self.assertEqual(
                    evidence.document.body[evidence.start_offset : evidence.end_offset],
                    evidence.snippet,
                )

    def test_private_urls_are_rejected(self):
        for url in [
            "http://127.0.0.1/secret",
            "http://10.0.0.1/secret",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/secret",
        ]:
            with self.assertRaises(UnsafeUrlError):
                validate_public_http_url(url, resolve=False)

    def test_report_generation_is_retrievable(self):
        response = self.client.post(
            "/pldr-api/v1/reports",
            json={"event_ids": ["evt_grounding"], "title": "PLDR CI evidence brief"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreater(payload["evidence_count"], 0)
        report = self.client.get(payload["url"])
        self.assertEqual(report.status_code, 200)
        self.assertIn("E1", report.text)
        self.assertIn("PLDR CI evidence brief", report.text)

    def test_rss_leaf_elements_are_parsed(self):
        xml = """<?xml version='1.0' encoding='UTF-8'?>
        <rss version='2.0'><channel><title>PLDR Test</title><item>
          <title>Leaf title works</title>
          <link>https://example.org/pldr-rss-leaf</link>
          <description>This description is deliberately long enough for PLDR extraction and validates leaf element parsing.</description>
        </item></channel></rss>"""
        response = self.client.post(
            "/pldr-api/v1/import/rss",
            json={"xml": xml, "source_name": "RSS Leaf Test", "language": "en"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["documents"][0]["title"], "Leaf title works")

    def test_workbench_shell_exposes_operational_actions(self):
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        for marker in [
            'id="event-drawer"',
            'id="btn-report"',
            'id="btn-import"',
            'id="import-modal"',
            'id="contested-filter"',
        ]:
            self.assertIn(marker, dashboard.text)

        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200)
        for endpoint in [
            "/pldr-api/v1/reports",
            "/pldr-api/v1/import/url",
            "/pldr-api/v1/import/rss",
        ]:
            self.assertIn(endpoint, script.text)

    def test_initial_selection_keeps_workspace_closed_without_a_deep_link(self):
        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200)
        source = script.text
        init_source = source[source.index("async function init()") :]

        self.assertLess(
            init_source.index("const requestedEvent ="),
            init_source.index("await refreshData("),
        )
        self.assertIn("preferredEventId: requestedEvent", init_source)
        self.assertIn("syncSelectionUrl: false", init_source)
        self.assertIn("requestedEvent && state.selectedId === requestedEvent", init_source)
        self.assertIn("openDrawer();", init_source)


if __name__ == "__main__":
    unittest.main()
