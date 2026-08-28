from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import asyncio
from unittest.mock import patch
from pathlib import Path

import httpx

TEST_ROOT = Path(tempfile.mkdtemp(prefix="pldr-p0-tests-"))
os.environ["PLDR_DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'pldr-p0-test.db'}"
os.environ["PLDR_REPORT_DIR"] = str(TEST_ROOT / "reports")
os.environ.pop("PLDR_ADMIN_TOKEN", None)

from fastapi.testclient import TestClient
from pldr_api.database import Base, SessionLocal, engine
from pldr_api.main import app
from pldr_api.intake import confirm_intake, get_intake_item
from pldr_api.importers import fetch_public_text
from pldr_api.models import (
    Claim,
    Document,
    Entity,
    Event,
    Evidence,
    IntakeItem,
    SearchQueryRun,
    SearchResult,
    SearchSelection,
    Snapshot,
    Source,
)
from pldr_api.schemas import ExternalSearchRequest
from pldr_api.schemas import IntakeConfirmationRequest
from pldr_api.search import (
    BackendSearchResponse,
    ExternalSearchError,
    SearchHit,
    SearchProviderConfig,
    request_brave_search,
    request_searxng_search,
)
from pldr_api.security import UnsafeUrlError, validate_public_http_url
from pldr_api.seed import counts, seed_database
from sqlalchemy import func, select


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

    @staticmethod
    def minimal_pdf(text: str) -> bytes:
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("utf-8")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(content)).encode("utf-8") + b" >>\nstream\n" + content + b"\nendstream",
        ]
        output = b"%PDF-1.4\n"
        offsets: list[int] = []
        for index, obj in enumerate(objects, 1):
            offsets.append(len(output))
            output += f"{index} 0 obj\n".encode("utf-8") + obj + b"\nendobj\n"
        xref = len(output)
        output += (
            b"xref\n0 6\n0000000000 65535 f \n"
            + b"".join(f"{offset:010d} 00000 n \n".encode("utf-8") for offset in offsets)
            + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
            + str(xref).encode("utf-8")
            + b"\n%%EOF\n"
        )
        return output

    @staticmethod
    def candidate_map(item: dict) -> dict[str, dict]:
        return {candidate["object_type"]: candidate for candidate in item["candidates"]}

    @staticmethod
    def search_hit(
        url: str,
        *,
        title: str = "External result",
        snippet: str = "Search-only snippet",
        published_at=None,
    ) -> SearchHit:
        from pldr_api.search import _normalize_hit

        return _normalize_hit(
            {
                "url": url,
                "title": title,
                "description": snippet,
                "publishedDate": published_at,
                "meta_url": {"hostname": url.split("/")[2]},
                "engine": "controlled-test-engine",
            },
            provider="controlled-test",
            engine="controlled-test-engine",
        )

    @staticmethod
    def formal_counts() -> dict[str, int]:
        models = [Source, Document, Snapshot, Event, Entity, Claim, Evidence]
        with SessionLocal() as session:
            return {
                model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in models
            }

    def confirmation_request(self, item: dict, **overrides) -> dict:
        candidates = self.candidate_map(item)
        claim = candidates["claim"]
        evidence = candidates["evidence"]
        event = candidates["event"]["machine"]["fields"]
        request = {
            "disposition": "create",
            "analyst": "analyst-1",
            "merge_event_id": None,
            "event": {
                "title": event.get("title") or "Analyst-confirmed event",
                "summary": event.get("summary") or "Analyst-confirmed intake material.",
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
        request.update(overrides)
        return request

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

    def test_cross_event_claim_merge_is_rejected(self):
        baseline = counts(SessionLocal())
        submitted = self.client.post(
            "/pldr-api/v1/intake/text",
            json={
                "text": "The cross-event audit note states that the port reopened after a safety inspection.",
                "source_description": "Cross-event audit note",
                "language": "en",
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        item = submitted.json()["intake_item"]
        self.assertEqual(item["status"], "candidate_ready")
        with SessionLocal() as session:
            existing_claim = session.scalars(select(Claim).where(Claim.event_id == "evt_grounding")).first()
            assert existing_claim is not None
            existing_claim_id = existing_claim.id

        request = self.confirmation_request(item, disposition="merge", merge_event_id="evt_queue")
        request["claims"] = [
            {
                "candidate_key": self.candidate_map(item)["claim"]["candidate_key"],
                "action": "merge",
                "text": "Cross-event claim",
                "status": "unverified",
                "confidence": 0.5,
                "temporal_scope": "",
                "merge_claim_id": existing_claim_id,
            }
        ]
        preview = self.client.post(f"/pldr-api/v1/intake/{item['id']}/preview", json=request)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertFalse(preview.json()["confirmable"])
        self.assertIn("must belong to the selected final event", "; ".join(preview.json()["errors"]))

        confirmation = self.client.post(f"/pldr-api/v1/intake/{item['id']}/confirm", json=request)
        self.assertEqual(confirmation.status_code, 400, confirmation.text)
        self.assertIn("must belong to the selected final event", confirmation.json()["detail"])
        self.assertEqual(counts(SessionLocal()), baseline)
        reopened = self.client.get(f"/pldr-api/v1/intake/{item['id']}")
        self.assertEqual(reopened.json()["status"], "candidate_ready")
        self.assertIsNone(reopened.json()["final_object_ids"]["event"])

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

        first_item = first.json()["intake_item"]
        second_item = second.json()["intake_item"]
        self.assertEqual(first_item["status"], "candidate_ready")
        self.assertEqual(second_item["status"], "candidate_ready")
        self.assertNotEqual(first_item["id"], second_item["id"])
        self.assertNotEqual(first_item["source"]["url"], second_item["source"]["url"])
        self.assertEqual(first_item["material"]["extracted_hash"], second_item["material"]["extracted_hash"])
        self.assertEqual(
            counts(SessionLocal()),
            {"sources": 16, "documents": 48, "events": 8, "claims": 24, "evidence": 56},
        )
        first_confirmation = self.client.post(
            f"/pldr-api/v1/intake/{first_item['id']}/confirm",
            json=self.confirmation_request(first_item),
        )
        second_confirmation = self.client.post(
            f"/pldr-api/v1/intake/{second_item['id']}/confirm",
            json=self.confirmation_request(second_item),
        )
        self.assertEqual(first_confirmation.status_code, 200, first_confirmation.text)
        self.assertEqual(second_confirmation.status_code, 200, second_confirmation.text)
        first_document_id = first_confirmation.json()["result"]["formal_object_ids"]["document"]
        second_document_id = second_confirmation.json()["result"]["formal_object_ids"]["document"]
        with SessionLocal() as session:
            second_document = session.get(Document, second_document_id)
            assert second_document is not None
            self.assertNotEqual(first_document_id, second_document_id)
            self.assertNotEqual(
                second_document.source.independence_group,
                session.get(Document, first_document_id).source.independence_group,
            )
            self.assertEqual(second_document.metadata_json["duplicate_of_document_id"], first_document_id)

        before_canonical_reuse = counts(SessionLocal())
        repeated = self.client.post(
            "/pldr-api/v1/import/url",
            json={
                "url": "https://alpha.example.org/shared-dispatch",
                "source_name": "Renamed Alpha Example",
                "html": html,
                "language": "en",
            },
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        repeated_item = repeated.json()["intake_item"]
        self.assertEqual(repeated_item["status"], "candidate_ready")
        repeated_confirmation = self.client.post(
            f"/pldr-api/v1/intake/{repeated_item['id']}/confirm",
            json=self.confirmation_request(repeated_item),
        )
        self.assertEqual(repeated_confirmation.status_code, 200, repeated_confirmation.text)
        repeated_result = repeated_confirmation.json()["result"]["formal_object_ids"]
        self.assertEqual(repeated_result["document"], first_document_id)
        self.assertEqual(
            repeated_result["source"],
            first_confirmation.json()["result"]["formal_object_ids"]["source"],
        )
        after_canonical_reuse = counts(SessionLocal())
        self.assertEqual(after_canonical_reuse["sources"], before_canonical_reuse["sources"])
        self.assertEqual(after_canonical_reuse["documents"], before_canonical_reuse["documents"])
        self.assertEqual(after_canonical_reuse["events"], before_canonical_reuse["events"] + 1)
        self.assertEqual(after_canonical_reuse["claims"], before_canonical_reuse["claims"] + 1)
        self.assertEqual(after_canonical_reuse["evidence"], before_canonical_reuse["evidence"] + 1)
        with SessionLocal() as session:
            persisted_repeated = session.get(IntakeItem, repeated_item["id"])
            assert persisted_repeated is not None
            self.assertEqual(persisted_repeated.final_document_id, first_document_id)
            self.assertTrue(persisted_repeated.final_snapshot_id)
            reused_document = session.get(Document, first_document_id)
            assert reused_document is not None
            self.assertEqual(
                reused_document.metadata_json["intake_item_ids"],
                [first_item["id"], repeated_item["id"]],
            )
            orphan_intake_sources = session.scalar(
                select(func.count())
                .select_from(Source)
                .outerjoin(Document, Document.source_id == Source.id)
                .where(Document.id.is_(None), Source.id.like("src_intake_%"))
            )
            self.assertEqual(orphan_intake_sources, 0)

    def test_evidence_exact_substrings(self):
        with SessionLocal() as session:
            for evidence in session.scalars(select(Evidence)):
                self.assertEqual(
                    evidence.document.body[evidence.start_offset : evidence.end_offset],
                    evidence.snippet,
                )
                if evidence.snapshot_id is not None:
                    snapshot = session.get(Snapshot, evidence.snapshot_id)
                    self.assertIsNotNone(snapshot)
                    self.assertEqual(
                        snapshot.excerpt[evidence.start_offset : evidence.end_offset],
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

    def test_intake_submissions_persist_and_negative_paths_stay_isolated(self):
        baseline = counts(SessionLocal())
        html = """
        <html><head><title>Public intake report</title></head><body><article>
        <p>The public report states that the coastal drill involved twelve vessels and was observed by two independent teams.</p>
        </article></body></html>
        """
        web = self.client.post(
            "/pldr-api/v1/import/url",
            json={"url": "https://intake.example.org/public-report", "html": html, "language": "en"},
        )
        self.assertEqual(web.status_code, 200, web.text)
        web_item = web.json()["intake_item"]
        self.assertEqual(web_item["status"], "candidate_ready")
        self.assertEqual(web_item["input_type"], "web")
        self.assertEqual(web_item["title"], "Public intake report")
        self.assertEqual(web_item["source"]["canonical_url"], "https://intake.example.org/public-report")
        self.assertIn("coastal drill", web_item["material"]["extracted_snapshot"])
        self.assertEqual(web_item["candidate_generation"]["mode"], "fallback")
        self.assertTrue(web_item["material"]["raw_hash"])
        self.assertTrue(web_item["material"]["extracted_hash"])

        text = self.client.post(
            "/pldr-api/v1/intake/text",
            json={
                "text": "The pasted field note records that a medical convoy delivered supplies to the riverside clinic.",
                "source_description": "Analyst field note supplied by a local contact",
                "title": None,
                "published_at": None,
                "language": "en",
            },
        )
        self.assertEqual(text.status_code, 200, text.text)
        text_item = text.json()["intake_item"]
        self.assertEqual(text_item["status"], "candidate_ready")
        self.assertIsNone(text_item["title"])
        self.assertIsNone(text_item["published_at"])
        self.assertEqual(text_item["source"]["description"], "Analyst field note supplied by a local contact")
        self.assertIn("riverside clinic", text_item["material"]["extracted_snapshot"])

        pdf = self.client.post(
            "/pldr-api/v1/intake/files",
            files={
                "file": (
                    "intake.pdf",
                    self.minimal_pdf("The uploaded PDF states that twelve vessels joined the coastal drill."),
                    "application/pdf",
                )
            },
            data={"source_description": "Local analyst PDF", "language": "en"},
        )
        self.assertEqual(pdf.status_code, 200, pdf.text)
        pdf_item = pdf.json()["intake_item"]
        self.assertEqual(pdf_item["status"], "candidate_ready")
        self.assertEqual(pdf_item["file"]["name"], "intake.pdf")
        self.assertEqual(pdf_item["file"]["media_type"], "application/pdf")
        self.assertGreater(pdf_item["file"]["size_bytes"], 0)
        self.assertIn("twelve vessels", pdf_item["material"]["extracted_snapshot"])
        self.assertEqual(pdf_item["material"]["raw_encoding"], "base64")

        rss_xml = """<?xml version='1.0' encoding='UTF-8'?>
        <rss version='2.0'><channel><title>Intake RSS</title><item>
          <title>RSS intake item</title>
          <link>https://rss-intake.example.org/item-1</link>
          <description>The RSS item states that the port authority reopened the eastern terminal after inspection.</description>
        </item></channel></rss>"""
        rss = self.client.post(
            "/pldr-api/v1/import/rss",
            json={"xml": rss_xml, "source_name": "Intake RSS", "language": "en"},
        )
        self.assertEqual(rss.status_code, 200, rss.text)
        rss_item = rss.json()["intake_items"][0]
        self.assertEqual(rss_item["input_type"], "rss")
        self.assertEqual(rss_item["status"], "candidate_ready")
        self.assertEqual(rss_item["title"], "RSS intake item")

        for item in [web_item, text_item, pdf_item, rss_item]:
            candidates = self.candidate_map(item)
            self.assertIn("event", candidates)
            self.assertIn("claim", candidates)
            evidence = candidates["evidence"]
            self.assertIsNone(evidence["validation_error"])
            snapshot = item["material"]["extracted_snapshot"]
            self.assertEqual(
                snapshot[
                    evidence["machine"]["fields"]["start_offset"] : evidence["machine"]["fields"]["end_offset"]
                ],
                evidence["machine"]["fields"]["snippet"],
            )

        private = self.client.post("/pldr-api/v1/import/url", json={"url": "http://127.0.0.1/secret"})
        self.assertEqual(private.status_code, 200, private.text)
        self.assertEqual(private.json()["intake_item"]["status"], "failed")
        self.assertIn("blocked", private.json()["intake_item"]["error"])
        short_page = self.client.post(
            "/pldr-api/v1/import/url",
            json={
                "url": "https://short-page.example.org/report",
                "html": "<html><body><p>too short</p></body></html>",
                "language": "en",
            },
        )
        self.assertEqual(short_page.status_code, 200, short_page.text)
        self.assertEqual(short_page.json()["intake_item"]["status"], "failed")
        self.assertTrue(short_page.json()["intake_item"]["material"]["raw_hash"])
        self.assertTrue(short_page.json()["intake_item"]["material"]["raw_snapshot"])
        self.assertTrue(short_page.json()["intake_item"]["material"]["extracted_hash"])
        empty = self.client.post(
            "/pldr-api/v1/intake/text",
            json={"text": "", "source_description": "Empty note", "language": "en"},
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["intake_item"]["status"], "failed")
        self.assertTrue(empty.json()["intake_item"]["material"]["raw_hash"])
        self.assertTrue(empty.json()["intake_item"]["material"]["extracted_hash"])
        damaged = self.client.post(
            "/pldr-api/v1/intake/files",
            files={"file": ("broken.pdf", b"%PDF-1.4 broken", "application/pdf")},
            data={"source_description": "Damaged file", "language": "en"},
        )
        self.assertEqual(damaged.status_code, 200, damaged.text)
        self.assertEqual(damaged.json()["intake_item"]["status"], "failed")
        self.assertIn("damaged", damaged.json()["intake_item"]["error"])
        damaged_item = damaged.json()["intake_item"]
        self.assertEqual(damaged_item["file"]["size_bytes"], len(b"%PDF-1.4 broken"))
        self.assertEqual(damaged_item["file"]["media_type"], "application/pdf")
        self.assertTrue(damaged_item["material"]["raw_hash"])
        self.assertTrue(damaged_item["material"]["raw_snapshot"])
        self.assertEqual(damaged_item["material"]["raw_encoding"], "base64")
        unsupported = self.client.post(
            "/pldr-api/v1/intake/files",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
            data={"source_description": "Unsupported file", "language": "en"},
        )
        self.assertEqual(unsupported.status_code, 200, unsupported.text)
        self.assertEqual(unsupported.json()["intake_item"]["status"], "failed")
        self.assertIn("Unsupported file type", unsupported.json()["intake_item"]["error"])
        unsupported_item = unsupported.json()["intake_item"]
        self.assertEqual(unsupported_item["file"]["size_bytes"], len(b"MZ"))
        self.assertEqual(unsupported_item["file"]["media_type"], "application/octet-stream")
        self.assertTrue(unsupported_item["material"]["raw_hash"])
        self.assertTrue(unsupported_item["material"]["raw_snapshot"])
        oversized = self.client.post(
            "/pldr-api/v1/intake/files",
            files={"file": ("oversized.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
            data={"source_description": "Oversized file", "language": "en"},
        )
        self.assertEqual(oversized.json()["intake_item"]["status"], "failed")
        self.assertIn("exceeds the 5 MiB limit", oversized.json()["intake_item"]["error"])
        self.assertEqual(oversized.json()["intake_item"]["file"]["size_bytes"], 5 * 1024 * 1024 + 1)
        self.assertTrue(oversized.json()["intake_item"]["material"]["raw_hash"])
        malformed_rss = self.client.post(
            "/pldr-api/v1/import/rss",
            json={"xml": "<rss>", "source_name": "Malformed RSS", "language": "en"},
        )
        self.assertEqual(malformed_rss.status_code, 200, malformed_rss.text)
        malformed_item = malformed_rss.json()["intake_items"][0]
        self.assertEqual(malformed_item["status"], "failed")
        self.assertTrue(malformed_item["material"]["raw_hash"])
        self.assertTrue(malformed_item["material"]["raw_snapshot"])

        self.assertEqual(counts(SessionLocal()), baseline)
        listed = self.client.get("/pldr-api/v1/intake?limit=200")
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()["count"], 10)
        ids = {item["id"] for item in listed.json()["items"]}
        self.assertIn(web_item["id"], ids)

        type(self).client.close()
        with TestClient(app) as restarted:
            reopened = restarted.get(f"/pldr-api/v1/intake/{web_item['id']}")
            self.assertEqual(reopened.status_code, 200)
            self.assertEqual(reopened.json()["status"], "candidate_ready")
            self.assertIn("coastal drill", reopened.json()["material"]["extracted_snapshot"])
            self.assertEqual(counts(SessionLocal()), baseline)
        type(self).client = TestClient(app)

    def test_model_candidates_keep_unknowns_and_invalid_evidence_blocked(self):
        baseline = counts(SessionLocal())
        html = """
        <html><head><title>Model input</title></head><body><article>
        <p>The model input states that the warehouse inspection found twelve pallets and one damaged container.</p>
        </article></body></html>
        """

        async def failing_model_task(task: str, payload: dict):
            raise RuntimeError("model unavailable")

        with patch("pldr_api.intake.run_model_task", side_effect=failing_model_task):
            failed = self.client.post(
                "/pldr-api/v1/import/url",
                json={"url": "https://model-failure.example.org/report", "html": html, "language": "en"},
            )
        self.assertEqual(failed.status_code, 200, failed.text)
        failed_item = failed.json()["intake_item"]
        self.assertEqual(failed_item["status"], "generation_failed")
        self.assertEqual(failed_item["candidate_generation"]["mode"], "failed")
        self.assertIn("model unavailable", failed_item["candidate_generation"]["error"])
        self.assertEqual(counts(SessionLocal()), baseline)

        async def recovered_model_task(task: str, payload: dict):
            return {"mode": "fallback"}

        with patch("pldr_api.intake.run_model_task", side_effect=recovered_model_task):
            regenerated = self.client.post(f"/pldr-api/v1/intake/{failed_item['id']}/regenerate")
        self.assertEqual(regenerated.status_code, 200, regenerated.text)
        self.assertEqual(regenerated.json()["status"], "candidate_ready")
        self.assertEqual(regenerated.json()["candidate_generation"]["mode"], "fallback")
        self.assertEqual(counts(SessionLocal()), baseline)

        async def fake_model_task(task: str, payload: dict):
            self.assertEqual(task, "extract_intake_candidates")
            self.assertIn("warehouse inspection", payload["snapshot"])
            return {
                "mode": "api",
                "model": "test-model",
                "result": {
                    "event": {"title": "Warehouse inspection finding", "summary": "Machine-proposed inspection summary.", "event_time": None, "location_name": None},
                    "entities": [{"name": "warehouse inspection", "entity_type": "activity", "aliases": [], "role": "subject"}],
                    "claims": [
                        {
                            "text": "The inspection found twelve pallets.",
                            "uncertainty": "Model inference pending review",
                            "temporal_scope": None,
                            "evidence": [
                                {"snippet": "The model input states that the warehouse inspection found twelve pallets and one damaged container.", "stance": "supports", "strength": 0.8},
                                {"snippet": "This sentence does not exist in the snapshot.", "stance": "supports", "strength": 0.9},
                            ],
                        }
                    ],
                },
            }

        with patch("pldr_api.intake.run_model_task", side_effect=fake_model_task):
            response = self.client.post(
                "/pldr-api/v1/import/url",
                json={"url": "https://model-candidate.example.org/report", "html": html, "language": "en"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["intake_item"]
        self.assertEqual(item["status"], "candidate_ready")
        self.assertEqual(item["candidate_generation"]["mode"], "api")
        self.assertEqual(item["candidate_generation"]["model"], "test-model")
        candidates = {candidate["candidate_key"]: candidate for candidate in item["candidates"]}
        self.assertIsNone(candidates["event"]["machine"]["fields"]["event_time"])
        self.assertIsNone(candidates["event"]["machine"]["fields"]["location_name"])
        self.assertEqual(len([c for c in candidates.values() if c["object_type"] == "entity"]), 1)
        invalid = candidates["evidence:2"]
        self.assertIn("not an exact substring", invalid["validation_error"])
        request = self.confirmation_request(item)
        request["evidence"] = [
            {
                "candidate_key": "evidence:2",
                "action": "include",
                "snippet": "Another absent sentence.",
                "stance": "supports",
                "strength": 0.9,
                "note": "",
            }
        ]
        blocked = self.client.post(f"/pldr-api/v1/intake/{item['id']}/confirm", json=request)
        self.assertEqual(blocked.status_code, 400, blocked.text)
        self.assertIn("not an exact substring", blocked.json()["detail"])
        self.assertEqual(counts(SessionLocal()), baseline)

    def test_review_dispositions_are_atomic_idempotent_and_traceable(self):
        baseline = counts(SessionLocal())

        def submit_web(url: str, sentence: str) -> dict:
            page = f"<html><head><title>{url}</title><body><article><p>{sentence}</p></article></body></html>"
            response = self.client.post(
                "/pldr-api/v1/import/url",
                json={"url": url, "html": page, "language": "en"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["intake_item"]

        create_item = submit_web(
            "https://review-create.example.org/report",
            "The web material states that the northern bridge reopened after a structural inspection.",
        )
        create_request = self.confirmation_request(
            create_item,
            event={
                "title": "Northern bridge reopened",
                "summary": "Analyst confirmed the reopening based on the submitted webpage.",
                "event_type": "incident",
                "start_at": None,
                "location_name": "",
                "importance": "medium",
            },
        )
        created = self.client.post(f"/pldr-api/v1/intake/{create_item['id']}/confirm", json=create_request)
        self.assertEqual(created.status_code, 200, created.text)
        create_result = created.json()["result"]
        self.assertTrue(created.json()["created"])
        self.assertEqual(create_result["disposition"], "create")
        self.assertIn("title", create_result["human_changes"]["event"])

        paste = self.client.post(
            "/pldr-api/v1/intake/text",
            json={
                "text": "The pasted note states that the clinic received additional medical supplies on Monday.",
                "source_description": "Clinic staff note",
                "language": "en",
            },
        ).json()["intake_item"]
        merge_request = self.confirmation_request(paste, disposition="merge", merge_event_id="evt_grounding")
        merged = self.client.post(f"/pldr-api/v1/intake/{paste['id']}/confirm", json=merge_request)
        self.assertEqual(merged.status_code, 200, merged.text)
        self.assertTrue(merged.json()["created"])

        file_item = self.client.post(
            "/pldr-api/v1/intake/files",
            files={
                "file": (
                    "modify.pdf",
                    self.minimal_pdf("The PDF note states that the warehouse inspection found one damaged container."),
                    "application/pdf",
                )
            },
            data={"source_description": "Warehouse inspection PDF", "language": "en"},
        ).json()["intake_item"]
        modify_request = self.confirmation_request(
            file_item,
            disposition="modify",
            event={
                "title": "Warehouse container damage",
                "summary": "Analyst renamed and confirmed the machine candidate.",
                "event_type": "incident",
                "start_at": None,
                "location_name": "",
                "importance": "low",
            },
        )
        modified = self.client.post(f"/pldr-api/v1/intake/{file_item['id']}/confirm", json=modify_request)
        self.assertEqual(modified.status_code, 200, modified.text)

        rejected_item = submit_web(
            "https://review-reject.example.org/report",
            "The rejected page states that an unverified shipment crossed the northern checkpoint.",
        )
        rejected = self.client.post(
            f"/pldr-api/v1/intake/{rejected_item['id']}/reject",
            json={"analyst": "analyst-1", "reason": "The material is out of scope for this topic."},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["intake_item"]["status"], "rejected")
        self.assertEqual(rejected.json()["intake_item"]["rejection_reason"], "The material is out of scope for this topic.")

        atomic_item = submit_web(
            "https://review-atomic.example.org/report",
            "The atomic material states that the control system remained online during the power interruption.",
        )
        atomic_request = self.confirmation_request(atomic_item)
        before_failure = counts(SessionLocal())
        with SessionLocal() as session:
            item = get_intake_item(session, atomic_item["id"])
            assert item is not None
            with self.assertRaises(RuntimeError):
                confirm_intake(
                    session,
                    item,
                    IntakeConfirmationRequest(**atomic_request),
                    failure_hook=lambda: (_ for _ in ()).throw(RuntimeError("injected midway failure")),
                )
            session.rollback()
            self.assertEqual(item.status, "candidate_ready")
        self.assertEqual(counts(SessionLocal()), before_failure)

        retried = self.client.post(f"/pldr-api/v1/intake/{atomic_item['id']}/confirm", json=atomic_request)
        self.assertEqual(retried.status_code, 200, retried.text)
        repeat = self.client.post(f"/pldr-api/v1/intake/{atomic_item['id']}/confirm", json=atomic_request)
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertFalse(repeat.json()["created"])

        after = counts(SessionLocal())
        self.assertEqual(after["events"], baseline["events"] + 3)
        self.assertEqual(after["documents"], baseline["documents"] + 4)
        self.assertEqual(after["claims"], baseline["claims"] + 4)
        self.assertEqual(after["evidence"], baseline["evidence"] + 4)
        self.assertEqual(after["sources"], baseline["sources"] + 4)

        with SessionLocal() as session:
            confirmed = list(session.scalars(select(IntakeItem).where(IntakeItem.status == "confirmed")))
            self.assertGreaterEqual(len(confirmed), 4)
            for item in confirmed:
                self.assertTrue(item.confirmation_result)
                self.assertTrue(item.final_event_id)
                self.assertTrue(item.final_document_id)
                self.assertTrue(any(candidate.final_object_id for candidate in item.candidates))

        refreshed_overview = self.client.get("/pldr-api/v1/overview")
        self.assertEqual(refreshed_overview.status_code, 200, refreshed_overview.text)
        self.assertGreater(refreshed_overview.json()["intake"]["confirmed"], 0)

    def test_traceable_output_reports_and_snapshot_trace_only_use_formal_objects(self):
        rejected_text = "This rejected sentence must never enter a confirmed report."
        rejected = self.client.post(
            "/pldr-api/v1/intake/text",
            json={"text": rejected_text, "source_description": "Rejected report note", "language": "en"},
        ).json()["intake_item"]
        reject = self.client.post(
            f"/pldr-api/v1/intake/{rejected['id']}/reject",
            json={"analyst": "analyst-1", "reason": "Not relevant"},
        )
        self.assertEqual(reject.status_code, 200)

        with SessionLocal() as session:
            confirmed = list(session.scalars(select(IntakeItem).where(IntakeItem.status == "confirmed")))
            created = next(item for item in confirmed if item.disposition == "create")
            modified = next(item for item in confirmed if item.disposition == "modify")
            created_event_id = created.final_event_id
            modified_event_id = modified.final_event_id
            created_document_id = created.final_document_id

        for event_id in [created_event_id, modified_event_id]:
            detail_response = self.client.get(f"/pldr-api/v1/events/{event_id}")
            self.assertEqual(detail_response.status_code, 200)
            detail = detail_response.json()
            self.assertTrue(detail["claims"])
            self.assertEqual(detail["claims"][-1]["origin"], "human-confirmed")
            evidence = detail["claims"][-1]["evidence"][0]
            self.assertIn(evidence["document"]["id"], {created_document_id, detail["documents"][-1]["id"]})
            self.assertGreaterEqual(evidence["start_offset"], 0)
            self.assertIsNotNone(evidence["snapshot_id"])
            self.assertIn(evidence["snapshot_id"], evidence["snapshot_url"])
            snapshot = self.client.get(evidence["snapshot_url"])
            self.assertEqual(snapshot.status_code, 200)
            self.assertIn("<mark", snapshot.text)
            self.assertIn("来源：", snapshot.text)
            self.assertIn(f"Snapshot：{evidence['snapshot_id']}", snapshot.text)
            self.assertNotIn("1970-01-01", snapshot.text)
            if event_id == created_event_id:
                self.assertIn("https://", snapshot.text)
                self.assertIn("原始地址：", snapshot.text)
            else:
                self.assertIn("原始地址：未知", snapshot.text)

        report = self.client.post(
            "/pldr-api/v1/reports",
            json={"event_ids": [created_event_id], "title": "P0.3 confirmed intake report"},
        )
        self.assertEqual(report.status_code, 200, report.text)
        report_text = self.client.get(report.json()["url"]).text
        self.assertIn("human-confirmed", report_text)
        self.assertIn("打开证据快照", report_text)
        self.assertIn("发布时间未知", report_text)
        self.assertNotIn(rejected_text, report_text)

        modified_report = self.client.post(
            "/pldr-api/v1/reports",
            json={"event_ids": [modified_event_id], "title": "P0.3 modified intake report"},
        )
        self.assertEqual(modified_report.status_code, 200, modified_report.text)
        modified_report_text = self.client.get(modified_report.json()["url"]).text
        self.assertIn("未知标题", modified_report_text)
        self.assertIn("human-confirmed", modified_report_text)

    def test_external_search_normalizes_scopes_and_does_not_fake_failures(self):
        before = self.formal_counts()

        async def controlled_backend(_, request):
            if request.keyword == "definitely no PLDR matches":
                return BackendSearchResponse("brave", f"brave-search-api:{request.scope}", [])
            if request.keyword == "backend failure":
                raise ExternalSearchError("Controlled rate limit", status_code=429, reason="rate_limited")
            hits = [
                self.search_hit(
                    "https://news.example.org/story?utm_source=test",
                    title="<script>alert('title')</script>Real title",
                    snippet="<strong>Search-only</strong> abstract",
                ),
                self.search_hit(
                    "https://news.example.org/story?utm_source=test",
                    title="Duplicate canonical URL",
                ),
            ]
            return BackendSearchResponse("brave", f"brave-search-api:{request.scope}", hits)

        with patch("pldr_api.search.request_search", controlled_backend):
            news = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "Suez canal", "scope": "news", "limit": 5, "language": "en"},
            )
            web = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "Suez canal", "scope": "web", "limit": 5, "language": "en"},
            )
            empty = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "definitely no PLDR matches", "scope": "web"},
            )
            failure = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "backend failure", "scope": "news"},
            )

        self.assertEqual(news.status_code, 200, news.text)
        self.assertEqual(web.status_code, 200, web.text)
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(failure.status_code, 429, failure.text)
        self.assertEqual(failure.json()["detail"]["reason"], "rate_limited")
        news_payload = news.json()
        web_payload = web.json()
        self.assertEqual(news_payload["scope"], "news")
        self.assertEqual(web_payload["scope"], "web")
        self.assertEqual(news_payload["channel"], "brave-search-api:news")
        self.assertEqual(web_payload["channel"], "brave-search-api:web")
        self.assertNotEqual(news_payload["id"], web_payload["id"])
        self.assertEqual(news_payload["result_count"], 1)
        self.assertEqual(empty.json()["result_count"], 0)
        result = news_payload["results"][0]
        self.assertEqual(result["title"], "Real title")
        self.assertEqual(result["snippet"], "Search-only abstract")
        self.assertEqual(result["original_url"], "https://news.example.org/story?utm_source=test")
        self.assertEqual(result["canonical_url"], "https://news.example.org/story")
        self.assertEqual(result["site"], "news.example.org")
        self.assertEqual(result["rank"], 1)
        self.assertIsNone(result["selection"])

        config = self.client.get("/pldr-api/v1/config").json()
        self.assertIn("external_keyword_discovery", config["features"])
        self.assertTrue(config["external_search"]["external_request"])
        self.assertIn("version", config["external_search"])
        self.assertIn("license", config["external_search"])
        self.assertIn("deployment_boundary", config["external_search"])

        with SessionLocal() as session:
            runs = {
                run_id: session.get(SearchQueryRun, run_id)
                for run_id in [
                    news_payload["id"],
                    web_payload["id"],
                    empty.json()["id"],
                    failure.json()["detail"].get("query_run_id"),
                ]
            }
            self.assertEqual(runs[news_payload["id"]].status, "ok")
            self.assertEqual(runs[web_payload["id"]].status, "ok")
            self.assertEqual(runs[empty.json()["id"]].status, "ok")
            self.assertEqual(runs[failure.json()["detail"].get("query_run_id")].status, "failed")
            self.assertEqual(runs[failure.json()["detail"].get("query_run_id")].channel, "brave-search-api:news")
            self.assertIn("Controlled rate limit", runs[failure.json()["detail"].get("query_run_id")].error)
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(SearchResult).where(
                                SearchResult.query_run_id.in_([news_payload["id"], web_payload["id"]])
                            )
                        )
                    )
                ),
                2,
            )
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(SearchSelection).where(
                                SearchSelection.result_id.in_(
                                    [result["id"] for result in news_payload["results"]]
                                )
                            )
                        )
                    )
                ),
                0,
            )
        self.assertEqual(self.formal_counts(), before)

    def test_search_provider_adapters_call_real_backend_contracts(self):
        calls = []

        class ControlledResponse:
            status_code = 200

            def __init__(self, section):
                self.section = section

            def json(self):
                return {
                    "web": {
                        "results": [
                            {
                                "url": "https://adapter.example.org/from-brave",
                                "title": "<b>Brave result</b>",
                                "description": "Brave description",
                                "meta_url": {"hostname": "adapter.example.org"},
                            }
                        ]
                    },
                    "news": {
                        "results": [
                            {
                                "url": "https://adapter.example.org/from-brave-news",
                                "title": "Brave news result",
                                "description": "Brave news description",
                                "meta_url": {"hostname": "adapter.example.org"},
                                "page_age": "2026-08-28T01:02:03Z",
                            }
                        ]
                    },
                    "searxng": {
                        "results": [
                            {
                                "url": "https://adapter.example.org/from-searxng",
                                "title": "SearXNG result",
                                "content": "SearXNG description",
                                "publishedDate": "2026-08-28T01:02:03Z",
                                "engine": "brave",
                            }
                        ]
                    },
                }[self.section]

        class ControlledClient:
            def __init__(self, timeout=None):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, endpoint, params=None, headers=None):
                calls.append(("POST", endpoint, params, headers))
                return ControlledResponse("news" if "/news/" in endpoint else "web")

            async def get(self, endpoint, params=None):
                calls.append(("GET", endpoint, params, None))
                return ControlledResponse("searxng")

        request = ExternalSearchRequest(keyword="adapter contract", scope="news", limit=5)
        with patch("pldr_api.search.httpx.AsyncClient", ControlledClient):
            brave_news = asyncio.run(
                request_brave_search(
                    SearchProviderConfig("brave", "https://api.search.brave.com", "test-key", 3),
                    request,
                )
            )
            brave_web = asyncio.run(
                request_brave_search(
                    SearchProviderConfig("brave", "https://api.search.brave.com", "test-key", 3),
                    request.model_copy(update={"scope": "web"}),
                )
            )
            searxng_news = asyncio.run(
                request_searxng_search(
                    SearchProviderConfig("searxng", "http://127.0.0.1:8888", "", 3),
                    request,
                )
            )

        self.assertEqual(calls[0][1], "https://api.search.brave.com/v1/news/search")
        self.assertEqual(calls[0][2]["q"], "adapter contract")
        self.assertNotIn("result_filter", calls[0][2])
        self.assertEqual(calls[0][3]["X-Subscription-Token"], "test-key")
        self.assertEqual(calls[1][1], "https://api.search.brave.com/v1/web/search")
        self.assertEqual(calls[1][2]["result_filter"], "web")
        self.assertEqual(calls[2][1], "http://127.0.0.1:8888/search")
        self.assertEqual(calls[2][2]["format"], "json")
        self.assertEqual(calls[2][2]["categories"], "news")

        self.assertEqual(brave_news.channel, "brave-search-api:news")
        self.assertEqual(brave_news.hits[0].title, "Brave news result")
        self.assertEqual(brave_web.hits[0].canonical_url, "https://adapter.example.org/from-brave")
        self.assertEqual(searxng_news.provider, "searxng")
        self.assertEqual(searxng_news.hits[0].snippet, "SearXNG description")
        self.assertEqual(searxng_news.hits[0].published_at.year, 2026)

        class TimeoutClient:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, *_, **__):
                raise httpx.ConnectTimeout("controlled timeout")

        with patch("pldr_api.search.httpx.AsyncClient", TimeoutClient):
            with self.assertRaises(ExternalSearchError) as timeout_context:
                asyncio.run(
                    request_searxng_search(
                        SearchProviderConfig("searxng", "http://127.0.0.1:8888", "", 3),
                        request,
                    )
                )
        self.assertEqual(timeout_context.exception.status_code, 504)
        self.assertEqual(timeout_context.exception.reason, "timeout")

    def test_selected_search_results_selectively_enter_intake_and_can_retry(self):
        before = self.formal_counts()
        fetched = []

        async def controlled_backend(_, request):
            hits = [
                self.search_hit("https://alpha.example.org/selected", title="Alpha search headline"),
                self.search_hit("https://bravo.example.org/not-selected", title="Bravo search headline"),
                self.search_hit("https://charlie.example.org/selected", title="Charlie search headline"),
            ]
            return BackendSearchResponse("brave", f"brave-search-api:{request.scope}", hits)

        async def controlled_fetch(url, **_):
            fetched.append(url)
            if url.startswith("https://charlie.example.org/"):
                raise RuntimeError("Controlled original-page timeout")
            return url, f"""
                <html><head><title>Selected original page {url.split('/')[3]}</title></head><body><article>
                <p>This sufficiently long original body is fetched only after an analyst explicitly selected
                the corresponding external search result for PLDR intake.</p>
                </article></body></html>
            """

        with patch("pldr_api.search.request_search", controlled_backend):
            search = self.client.post(
                "/pldr-api/v1/search", json={"keyword": "selective intake", "scope": "web"}
            )
        self.assertEqual(search.status_code, 200, search.text)
        results = search.json()["results"]
        selected_ids = [results[0]["id"], results[2]["id"]]
        with patch("pldr_api.intake.validate_public_http_url", lambda url, resolve=True: url), patch(
            "pldr_api.intake.fetch_public_text", controlled_fetch
        ), patch(
            "pldr_api.search.fetch_public_text", controlled_fetch
        ):
            selected = self.client.post("/pldr-api/v1/search/select", json={"result_ids": selected_ids})
            self.assertEqual(selected.status_code, 200, selected.text)
            repeated = self.client.post("/pldr-api/v1/search/select", json={"result_ids": selected_ids})
            self.assertEqual(repeated.status_code, 200, repeated.text)

        self.assertEqual(
            fetched,
            ["https://alpha.example.org/selected", "https://charlie.example.org/selected"],
        )
        alpha_response, charlie_response = selected.json()["results"]
        self.assertEqual(alpha_response["intake_status"], "candidate_ready")
        self.assertEqual(charlie_response["intake_status"], "failed")
        self.assertIn("Controlled original-page timeout", charlie_response["error"])
        self.assertEqual(
            [entry["outcome"] for entry in repeated.json()["results"]],
            ["already_added", "already_added"],
        )
        self.assertEqual(
            [entry["intake_item_id"] for entry in repeated.json()["results"]],
            [alpha_response["intake_item_id"], charlie_response["intake_item_id"]],
        )

        with SessionLocal() as session:
            search_items = list(
                session.scalars(
                    select(IntakeItem).where(
                        IntakeItem.source_url.in_(
                            [
                                "https://alpha.example.org/selected",
                                "https://charlie.example.org/selected",
                            ]
                        )
                    )
                )
            )
            self.assertEqual(len(search_items), 2)
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(IntakeItem).where(
                                IntakeItem.source_url == "https://bravo.example.org/not-selected"
                            )
                        )
                    )
                ),
                0,
            )
            alpha = session.get(IntakeItem, alpha_response["intake_item_id"])
            self.assertEqual(alpha.status, "candidate_ready")
            self.assertEqual(alpha.title, "Selected original page selected")
            trace = alpha.review["external_search"]
            self.assertEqual(trace["keyword"], "selective intake")
            self.assertEqual(trace["scope"], "web")
            self.assertEqual(trace["channel"], "brave-search-api:web")
            self.assertEqual(trace["result_id"], results[0]["id"])
            self.assertEqual(trace["query_run_id"], search.json()["id"])
            self.assertEqual(trace["original_url"], "https://alpha.example.org/selected")
            self.assertEqual(trace["search_title"], "Alpha search headline")

        async def recovered_fetch(url, **_):
            return url, """
                <html><head><title>Recovered original page</title></head><body><article>
                <p>This recovered body is long enough to pass extraction and becomes the only evidence
                snapshot considered by deterministic candidate generation.</p>
                </article></body></html>
            """

        with patch("pldr_api.importers.validate_public_http_url", lambda url, resolve=True: url), patch(
            "pldr_api.intake.fetch_public_text", recovered_fetch
        ), patch(
            "pldr_api.search.fetch_public_text", recovered_fetch
        ):
            retried = self.client.post(f"/pldr-api/v1/search/results/{results[2]['id']}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["intake_status"], "candidate_ready")
        self.assertEqual(retried.json()["intake_item_id"], charlie_response["intake_item_id"])
        self.assertEqual(self.formal_counts(), before)

    def test_external_search_stays_evidence_first_and_idempotent_after_confirmation(self):
        async def controlled_backend(_, request):
            return BackendSearchResponse(
                "brave",
                f"brave-search-api:{request.scope}",
                [
                    self.search_hit(
                        "https://formal.example.org/confirmed",
                        title="Search-only headline",
                        snippet="Search-only abstract that must never become Evidence",
                    )
                ],
            )

        async def controlled_fetch(url, **_):
            return url, """
                <html><head><title>Original confirmed page</title></head><body><article>
                <p>This original page body is the only snapshot allowed to support a human-confirmed
                claim after external keyword discovery and selective intake.</p>
                </article></body></html>
            """

        with patch("pldr_api.search.request_search", controlled_backend):
            search = self.client.post(
                "/pldr-api/v1/search", json={"keyword": "formal confirmation", "scope": "news"}
            )
            self.assertEqual(search.status_code, 200, search.text)
            result_id = search.json()["results"][0]["id"]
        with patch("pldr_api.intake.validate_public_http_url", lambda url, resolve=True: url), patch(
            "pldr_api.intake.fetch_public_text", controlled_fetch
        ):
            selected = self.client.post("/pldr-api/v1/search/select", json={"result_ids": [result_id]})
        self.assertEqual(selected.status_code, 200, selected.text)
        intake_payload = self.client.get(
            f"/pldr-api/v1/intake/{selected.json()['results'][0]['intake_item_id']}"
        ).json()
        self.assertIsNotNone(intake_payload["search"])
        self.assertEqual(intake_payload["title"], "Original confirmed page")

        before = self.formal_counts()
        request = self.confirmation_request(intake_payload)
        confirmed = self.client.post(
            f"/pldr-api/v1/intake/{intake_payload['id']}/confirm", json=request
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertTrue(confirmed.json()["created"])
        after_confirm = self.formal_counts()
        expected_delta = {
            "sources": 1,
            "documents": 1,
            "snapshots": 1,
            "events": 1,
            "entities": 0,
            "claims": 1,
            "evidence": 1,
        }
        for key, delta in expected_delta.items():
            self.assertEqual(after_confirm[key], before[key] + delta, key)

        confirmed_payload = confirmed.json()["intake_item"]
        confirmation_trace = confirmed_payload["confirmation_result"]["trace"]
        self.assertEqual(confirmation_trace["intake_item_id"], intake_payload["id"])
        self.assertEqual(confirmation_trace["external_search"]["query_run_id"], search.json()["id"])
        self.assertEqual(confirmation_trace["external_search"]["result_id"], result_id)
        evidence_id = confirmed_payload["confirmation_result"]["formal_object_ids"]["evidence"][0]
        with SessionLocal() as session:
            evidence = session.get(Evidence, evidence_id)
            snapshot = session.get(Snapshot, evidence.snapshot_id)
            document = session.get(Document, evidence.document_id)
            self.assertEqual(
                evidence.document.body[evidence.start_offset : evidence.end_offset],
                evidence.snippet,
            )
            self.assertEqual(snapshot.excerpt, document.body)
            self.assertEqual(document.canonical_url, "https://formal.example.org/confirmed")
            self.assertNotIn("Search-only", document.body)
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(SearchSelection).where(SearchSelection.intake_item_id == intake_payload["id"])
                        )
                    )
                ),
                1,
            )

        repeat_confirmation = self.client.post(
            f"/pldr-api/v1/intake/{intake_payload['id']}/confirm", json=request
        )
        repeat_selection = self.client.post(
            "/pldr-api/v1/search/select", json={"result_ids": [result_id]}
        )
        self.assertEqual(repeat_confirmation.status_code, 200, repeat_confirmation.text)
        self.assertFalse(repeat_confirmation.json()["created"])
        self.assertEqual(repeat_selection.status_code, 200, repeat_selection.text)
        self.assertEqual(
            repeat_selection.json()["results"][0]["intake_item_id"], intake_payload["id"]
        )
        self.assertEqual(self.formal_counts(), after_confirm)

        report = self.client.post(
            "/pldr-api/v1/reports",
            json={
                "event_ids": [confirmed_payload["final_object_ids"]["event"]],
                "title": "External discovery evidence report",
            },
        )
        self.assertEqual(report.status_code, 200, report.text)
        report_text = self.client.get(report.json()["url"]).text
        self.assertIn("original page body", report_text)
        self.assertNotIn("Search-only headline", report_text)
        self.assertNotIn("Search-only abstract", report_text)

    def test_external_search_and_fetch_failures_leave_formal_area_unchanged(self):
        before = self.formal_counts()

        async def controlled_backend(_, request):
            hits = [
                self.search_hit("http://10.0.0.1/private-result", title="Private result"),
                self.search_hit("https://example.org/no-body", title="No-body result"),
            ]
            return BackendSearchResponse("brave", f"brave-search-api:{request.scope}", hits)

        async def empty_fetch(url, **_):
            return url, "<html><body><p>short</p></body></html>"

        with patch("pldr_api.search.request_search", controlled_backend):
            search = self.client.post(
                "/pldr-api/v1/search", json={"keyword": "failure isolation", "scope": "web"}
            )
        self.assertEqual(search.status_code, 200, search.text)
        result_ids = [item["id"] for item in search.json()["results"]]
        with patch("pldr_api.intake.fetch_public_text", empty_fetch):
            selected = self.client.post("/pldr-api/v1/search/select", json={"result_ids": result_ids})
            retried_private = self.client.post(
                f"/pldr-api/v1/search/results/{result_ids[0]}/retry"
            )

        self.assertEqual(selected.status_code, 200, selected.text)
        statuses = [entry["intake_status"] for entry in selected.json()["results"]]
        self.assertEqual(statuses, ["failed", "failed"])
        self.assertIn("Non-public address", selected.json()["results"][0]["error"])
        self.assertIn("too short", selected.json()["results"][1]["error"])
        self.assertEqual(retried_private.status_code, 200)
        self.assertEqual(retried_private.json()["intake_status"], "failed")
        self.assertIn("Non-public address", retried_private.json()["error"])

        class RedirectResponse:
            status_code = 302
            headers = {"location": "http://127.0.0.1/redirect-target"}

        class RedirectClient:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, *_):
                return RedirectResponse()

        real_validator = validate_public_http_url

        def allow_initial_public_url(url, resolve=True):
            if url == "https://example.org/redirect":
                return url
            return real_validator(url, resolve=False)

        with patch("pldr_api.importers.httpx.AsyncClient", RedirectClient), patch(
            "pldr_api.importers.validate_public_http_url", allow_initial_public_url
        ):
            with self.assertRaises(UnsafeUrlError):
                asyncio.run(fetch_public_text("https://example.org/redirect"))

        with SessionLocal() as session:
            items = list(
                session.scalars(
                    select(IntakeItem).where(
                        IntakeItem.id.in_(
                            [
                                selected.json()["results"][0]["intake_item_id"],
                                selected.json()["results"][1]["intake_item_id"],
                            ]
                        )
                    )
                )
            )
            self.assertEqual({item.status for item in items}, {"failed"})
            self.assertTrue(all(item.review.get("external_search") for item in items))
        self.assertEqual(self.formal_counts(), before)

        with patch.dict(os.environ, {"PLDR_SEARCH_API_KEY": "", "PLDR_SEARCH_PROVIDER": "brave"}):
            unconfigured = self.client.post(
                "/pldr-api/v1/search", json={"keyword": "unconfigured backend", "scope": "web"}
            )
        self.assertEqual(unconfigured.status_code, 503, unconfigured.text)
        self.assertIn("not configured", unconfigured.text)
        self.assertEqual(self.formal_counts(), before)

    def test_workbench_shell_exposes_operational_actions(self):
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        for marker in [
            'id="event-drawer"',
            'id="btn-report"',
            'id="btn-import"',
            'id="import-modal"',
            'id="intake-modal"',
            'id="btn-intake"',
            'id="btn-search"',
            'id="search-modal"',
            'id="search-status"',
            'id="import-file"',
            'id="contested-filter"',
            "外部关键词发现",
            "不是筛选已入档事件",
            "搜索标题、摘要、排名和检索渠道都不是 Evidence",
        ]:
            self.assertIn(marker, dashboard.text)

        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200)
        for endpoint in [
            "/pldr-api/v1/reports",
            "/pldr-api/v1/import/url",
            "/pldr-api/v1/import/rss",
            "/pldr-api/v1/intake/files",
            "/pldr-api/v1/intake/",
            "/pldr-api/v1/search",
            "/pldr-api/v1/search/select",
            "/pldr-api/v1/search/results/",
        ]:
            self.assertIn(endpoint, script.text)
        for control_state in [
            '$("#import-url").disabled = !isUrlMode;',
            '$("#import-text").required = isTextMode;',
            '$("#import-text").disabled = !isTextMode;',
            '$("#import-file").required = isFileMode;',
            '$("#import-file").disabled = !isFileMode;',
        ]:
            self.assertIn(control_state, script.text)
        self.assertIn('href="/snapshots/${escapeHtml(final.snapshot)}"', script.text)
        self.assertIn("没有匹配结果。PLDR 不会用演示数据填充空结果。", script.text)
        self.assertIn("未生成演示结果。", script.text)
        self.assertIn("data-search-retry=", script.text)
        self.assertIn('data-intake-action="retry-search"', script.text)
        self.assertIn('escapeHtml(result.title || "无标题")', script.text)

        styles = self.client.get("/assets/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertIn("@media (max-width: 580px)", styles.text)
        self.assertIn(".intake-layout { grid-template-columns: 1fr;", styles.text)
        self.assertIn(".search-modal", styles.text)
        self.assertIn(".search-result", styles.text)

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
