from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import asyncio
from datetime import datetime, timezone
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
from pldr_api.intake import confirm_intake, get_intake_item, parse_datetime
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
    SearchSelectionEvent,
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
from sqlalchemy import create_engine, func, inspect, select, text


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
            self.assertIn("at most 5", payload["output_contract"]["entities"])
            self.assertIn("at most 3", payload["output_contract"]["claims"])
            return {
                "mode": "api",
                "model": "test-model",
                "result": {
                    "event": {"title": "Warehouse inspection finding", "summary": "Machine-proposed inspection summary.", "start_at": "2099-01-01T00:00:00Z", "location_name": None},
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
        self.assertNotIn("start_at", candidates["event"]["machine"]["fields"])
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

        exact_quote = "The model input states that the warehouse inspection found twelve pallets and one damaged container."
        duplicate_result = {
            "mode": "api",
            "model": "test-model",
            "result": {
                "event": {"title": "Duplicate wording", "summary": "A model output with duplicated wording."},
                "entities": [],
                "claims": [{
                    "text": exact_quote,
                    "evidence": [{"snippet": exact_quote, "stance": "supports", "strength": 0.8}],
                }],
            },
        }
        with patch("pldr_api.intake.run_model_task", return_value=duplicate_result):
            duplicate = self.client.post(
                "/pldr-api/v1/import/url",
                json={"url": "https://duplicate-claim.example.org/report", "html": html, "language": "en"},
            )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        duplicate_item = duplicate.json()["intake_item"]
        self.assertEqual(duplicate_item["status"], "generation_failed")
        self.assertIn("must not duplicate", duplicate_item["candidate_generation"]["error"])
        self.assertFalse(duplicate_item["candidates"])
        self.assertEqual(counts(SessionLocal()), baseline)

    def test_evidence_terminal_punctuation_is_repaired_only_to_an_exact_quote(self):
        source = (
            "第二枚导弹的袭击目标是试图帮助船员撤离的救援人员，其中两人遇难，"
            "这两人均来自反胡塞武装的也门组织、也门政府的盟军“国家抵抗力量”"
            "（National Resistance Forces）。"
        )

        async def model_task(task: str, payload: dict):
            self.assertEqual(task, "extract_intake_candidates")
            return {
                "mode": "api",
                "model": "test-model",
                "result": {
                    "event": {"title": "红海商船救援人员遇袭", "summary": "救援人员遭到第二枚导弹袭击。"},
                    "entities": [],
                    "claims": [{
                        "text": "第二枚导弹袭击了救援人员，并造成两名国家抵抗力量成员遇难。",
                        "evidence": [{
                            "snippet": (
                                "第二枚导弹的袭击目标是试图帮助船员撤离的救援人员，其中两人遇难，"
                                "这两人均来自反胡塞武装的也门组织、也门政府的盟军“国家抵抗力量”。"
                            ),
                            "paragraph_id": "P999",
                            "stance": "supports",
                            "strength": 0.9,
                        }],
                    }],
                },
            }

        with patch("pldr_api.intake.run_model_task", side_effect=model_task):
            response = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": source,
                    "source_description": "公开报道",
                    "title": "红海商船安全动态",
                    "language": "zh-CN",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["intake_item"]
        candidates = {candidate["candidate_key"]: candidate for candidate in item["candidates"]}
        evidence = candidates["evidence:1"]
        repaired = evidence["machine"]["fields"]["snippet"]
        self.assertEqual(
            repaired,
            "第二枚导弹的袭击目标是试图帮助船员撤离的救援人员，其中两人遇难，"
            "这两人均来自反胡塞武装的也门组织、也门政府的盟军“国家抵抗力量”",
        )
        self.assertIn(repaired, item["material"]["extracted_snapshot"])
        self.assertIsNone(evidence["validation_error"])
        self.assertNotEqual(evidence["machine"]["fields"]["paragraph_id"], "P999")

        confirmed = self.client.post(
            f"/pldr-api/v1/intake/{item['id']}/confirm",
            json=self.confirmation_request(item),
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertTrue(confirmed.json()["created"])

    def test_event_time_is_normalized_or_left_unknown_before_confirmation(self):
        self.assertEqual(
            parse_datetime("2026年8月15日"),
            datetime(2026, 8, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_datetime("2026年8月15日 14时30分"),
            datetime(2026, 8, 15, 14, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_datetime("2026-08-15T06:30:00Z"),
            datetime(2026, 8, 15, 6, 30, tzinfo=timezone.utc),
        )
        with self.assertRaises(ValueError):
            parse_datetime("2026年8月15日至今")

        source_sentence = "公开通报称，事件发生于2026年8月15日，相关部门随后启动调查。"

        async def dated_model_task(task: str, payload: dict):
            return {
                "mode": "api",
                "model": "test-model",
                "result": {
                    "event": {
                        "title": "公开通报事件",
                        "summary": "通报记录了一项待人工确认的事件。",
                        "event_time": "2026年8月15日",
                        "location_name": None,
                    },
                    "entities": [],
                    "claims": [{
                        "text": "公开通报记录了一项事件。",
                        "evidence": [{
                            "snippet": source_sentence,
                            "stance": "supports",
                            "strength": 0.8,
                        }],
                    }],
                },
            }

        with patch("pldr_api.intake.run_model_task", side_effect=dated_model_task):
            response = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": source_sentence,
                    "source_description": "公开通报",
                    "title": "事件时间规范化测试",
                    "language": "zh-CN",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["intake_item"]
        event = self.candidate_map(item)["event"]["machine"]["fields"]
        self.assertEqual(event["event_time"], "2026-08-15T00:00:00Z")
        request = self.confirmation_request(item)
        request["event"]["start_at"] = "2026年8月15日"
        preview = self.client.post(f"/pldr-api/v1/intake/{item['id']}/preview", json=request)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["confirmable"], preview.text)
        self.assertEqual(
            preview.json()["semantic_preview"]["event"]["start_at"],
            "2026-08-15T00:00:00Z",
        )

        partial_sentence = "也门政府表示，胡塞武装周二（8月11日）对红海商船发动袭击。"

        async def partial_date_model_task(task: str, payload: dict):
            return {
                "mode": "api",
                "model": "test-model",
                "result": {
                    "event": {
                        "title": "红海商船遇袭",
                        "summary": "胡塞武装袭击了一艘红海商船。",
                        # Providers often normalize this value even though the
                        # source itself carries only month/day wording.
                        "event_time": "2026年8月11日",
                        "location_name": "红海",
                    },
                    "entities": [],
                    "claims": [{
                        "text": "胡塞武装在8月11日袭击了一艘红海商船。",
                        "evidence": [{
                            "snippet": partial_sentence,
                            "stance": "supports",
                            "strength": 0.8,
                        }],
                    }],
                },
            }

        with patch("pldr_api.intake.run_model_task", side_effect=partial_date_model_task):
            partial_response = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": partial_sentence,
                    "source_description": "公开报道",
                    "title": "部分日期补全年份测试",
                    "published_at": "2026-08-12T00:00:00Z",
                    "language": "zh-CN",
                },
            )
        self.assertEqual(partial_response.status_code, 200, partial_response.text)
        partial_event = self.candidate_map(partial_response.json()["intake_item"])["event"]["machine"]["fields"]
        self.assertEqual(partial_event["event_time"], "2026-08-11T00:00:00Z")
        self.assertEqual(partial_event["event_time_source_text"], "8月11日")
        self.assertEqual(partial_event["event_time_basis"], "source_partial_date_with_document_year")

        cued_day_sentence = (
            "据央视新闻报道，当地时间11日，也门海岸警卫队表示一艘商船在曼德海峡遇袭。"
        )

        async def omitted_date_model_task(task: str, payload: dict):
            return {
                "mode": "api",
                "model": "test-model",
                "result": {
                    "event": {
                        "title": "曼德海峡商船遇袭",
                        "summary": "一艘商船在曼德海峡遇袭。",
                        "event_time": None,
                        "location_name": "曼德海峡",
                    },
                    "entities": [],
                    "claims": [{
                        "text": "也门海岸警卫队表示一艘商船在曼德海峡遇袭。",
                        "evidence": [{
                            "snippet": cued_day_sentence,
                            "stance": "supports",
                            "strength": 0.8,
                        }],
                    }],
                },
            }

        with patch("pldr_api.intake.run_model_task", side_effect=omitted_date_model_task):
            cued_day_response = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": cued_day_sentence,
                    "source_description": "公开报道",
                    "title": "模型漏填日期兜底测试",
                    "published_at": "2026-08-12T00:00:00Z",
                    "language": "zh-CN",
                },
            )
        self.assertEqual(cued_day_response.status_code, 200, cued_day_response.text)
        cued_day_event = self.candidate_map(
            cued_day_response.json()["intake_item"]
        )["event"]["machine"]["fields"]
        self.assertEqual(cued_day_event["event_time"], "2026-08-11T00:00:00Z")
        self.assertEqual(cued_day_event["event_time_source_text"], "当地时间11日")
        self.assertEqual(
            cued_day_event["event_time_basis"],
            "source_cued_day_with_document_month",
        )

        async def verbose_cued_date_model_task(task: str, payload: dict):
            result = await omitted_date_model_task(task, payload)
            result["result"]["event"]["event_time"] = "当地时间11日，胡塞武装当天上午"
            return result

        with patch(
            "pldr_api.intake.run_model_task",
            side_effect=verbose_cued_date_model_task,
        ):
            verbose_cued_response = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": cued_day_sentence,
                    "source_description": "公开报道",
                    "title": "模型返回带说明日期测试",
                    "published_at": "2026-08-12T00:00:00Z",
                    "language": "zh-CN",
                },
            )
        self.assertEqual(
            verbose_cued_response.status_code,
            200,
            verbose_cued_response.text,
        )
        verbose_cued_event = self.candidate_map(
            verbose_cued_response.json()["intake_item"]
        )["event"]["machine"]["fields"]
        self.assertEqual(verbose_cued_event["event_time"], "2026-08-11T00:00:00Z")
        self.assertEqual(verbose_cued_event["event_time_source_text"], "当地时间11日")
        self.assertEqual(
            verbose_cued_event["event_time_basis"],
            "source_cued_day_with_document_month",
        )

        uncued_day_sentence = "公开材料列出11日的值班记录，但没有说明事件发生时间。"

        async def uncued_date_model_task(task: str, payload: dict):
            result = await omitted_date_model_task(task, payload)
            result["result"]["event"] = {
                "title": "值班记录",
                "summary": "材料列出值班记录。",
                "event_time": None,
                "location_name": None,
            }
            result["result"]["claims"] = [{
                "text": "材料列出11日的值班记录。",
                "evidence": [{
                    "snippet": uncued_day_sentence,
                    "stance": "context",
                    "strength": 0.5,
                }],
            }]
            return result

        with patch("pldr_api.intake.run_model_task", side_effect=uncued_date_model_task):
            uncued_day_response = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": uncued_day_sentence,
                    "source_description": "公开材料",
                    "title": "无日期提示不推断测试",
                    "published_at": "2026-08-12T00:00:00Z",
                    "language": "zh-CN",
                },
            )
        self.assertEqual(uncued_day_response.status_code, 200, uncued_day_response.text)
        uncued_day_event = self.candidate_map(
            uncued_day_response.json()["intake_item"]
        )["event"]["machine"]["fields"]
        self.assertIsNone(uncued_day_event["event_time"])

        ranged_sentence = "专题关注范围为2026年8月15日至今，具体事件时间尚未核实。"

        async def ranged_model_task(task: str, payload: dict):
            return {
                "mode": "api",
                "model": "test-model",
                "result": {
                    "event": {
                        "title": "时间范围待核实",
                        "summary": "材料只给出了专题范围。",
                        "event_time": "2026年8月15日至今",
                        "location_name": None,
                    },
                    "entities": [],
                    "claims": [{
                        "text": "材料没有给出明确事件时间。",
                        "evidence": [{
                            "snippet": ranged_sentence,
                            "stance": "context",
                            "strength": 0.6,
                        }],
                    }],
                },
            }

        with patch("pldr_api.intake.run_model_task", side_effect=ranged_model_task):
            ranged = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": ranged_sentence,
                    "source_description": "专题范围说明",
                    "title": "事件时间范围测试",
                    "language": "zh-CN",
                },
            )
        self.assertEqual(ranged.status_code, 200, ranged.text)
        ranged_event = self.candidate_map(ranged.json()["intake_item"])["event"]["machine"]["fields"]
        self.assertIsNone(ranged_event["event_time"])

    def test_intake_archive_is_reversible_idempotent_and_never_changes_processing_state(self):
        with patch(
            "pldr_api.intake.run_model_task",
            return_value={"mode": "fallback"},
        ):
            created = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": "The archived note states that the public bridge remained open after inspection.",
                    "source_description": "Archive contract note",
                    "title": "Bridge inspection note",
                    "language": "en",
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["intake_item"]
        item_id = item["id"]
        original_status = item["status"]
        before_formal = self.formal_counts()

        archived = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/archive"
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        archived_item = archived.json()["intake_item"]
        self.assertTrue(archived.json()["changed"])
        self.assertTrue(archived_item["archived"])
        self.assertEqual(archived_item["status"], original_status)
        self.assertEqual(archived_item["archived_by"], "analyst")
        self.assertEqual(archived_item["archive_reason"], "Removed from active intake inbox")
        self.assertEqual(archived_item["allowed_actions"], ["restore"])
        archived_at = archived_item["archived_at"]

        repeated = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/archive",
            json={"analyst": "another", "reason": "A repeated request must be idempotent"},
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertFalse(repeated.json()["changed"])
        self.assertEqual(repeated.json()["intake_item"]["archived_at"], archived_at)
        self.assertEqual(repeated.json()["intake_item"]["archived_by"], "analyst")

        active_ids = {
            entry["id"] for entry in self.client.get("/pldr-api/v1/intake?limit=500").json()["items"]
        }
        archived_ids = {
            entry["id"]
            for entry in self.client.get(
                "/pldr-api/v1/intake?limit=500&visibility=archived"
            ).json()["items"]
        }
        all_ids = {
            entry["id"]
            for entry in self.client.get(
                "/pldr-api/v1/intake?limit=500&visibility=all"
            ).json()["items"]
        }
        alias_ids = {
            entry["id"]
            for entry in self.client.get(
                "/pldr-api/v1/intake?limit=500&include_archived=true"
            ).json()["items"]
        }
        self.assertNotIn(item_id, active_ids)
        self.assertIn(item_id, archived_ids)
        self.assertIn(item_id, all_ids)
        self.assertIn(item_id, alias_ids)
        detail = self.client.get(f"/pldr-api/v1/intake/{item_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["archived"])
        self.assertEqual(self.formal_counts(), before_formal)
        from pldr_api.investigations import bootstrap_legacy_investigations
        from pldr_api.models import InvestigationLink, ReviewTask

        with SessionLocal() as session:
            bootstrap_legacy_investigations(session)
            self.assertIsNone(
                session.scalar(
                    select(InvestigationLink.id).where(
                        InvestigationLink.object_type == "intake",
                        InvestigationLink.object_id == item_id,
                    )
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(ReviewTask.id).where(
                        ReviewTask.intake_item_id == item_id
                    )
                )
            )

        blocked_confirmation = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/confirm",
            json=self.confirmation_request(item),
        )
        self.assertEqual(blocked_confirmation.status_code, 409, blocked_confirmation.text)
        self.assertIn("Restore", blocked_confirmation.json()["detail"])

        restored = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/restore"
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        restored_item = restored.json()["intake_item"]
        self.assertTrue(restored.json()["changed"])
        self.assertFalse(restored_item["archived"])
        self.assertEqual(restored_item["status"], original_status)
        self.assertIsNone(restored_item["archived_at"])
        self.assertEqual(restored_item["allowed_actions"], ["archive"])
        with SessionLocal() as session:
            persisted = session.get(IntakeItem, item_id)
            assert persisted is not None
            archive_history = persisted.review["archive_history"]
            self.assertEqual(
                [entry["reason"] for entry in archive_history[-2:]],
                [
                    "Removed from active intake inbox",
                    "Restored to active intake inbox",
                ],
            )
        repeated_restore = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/restore",
            json={"analyst": "archive-tester", "reason": "Repeated restore"},
        )
        self.assertFalse(repeated_restore.json()["changed"])
        self.assertIn(
            item_id,
            {
                entry["id"]
                for entry in self.client.get("/pldr-api/v1/intake?limit=500").json()["items"]
            },
        )
        self.assertEqual(self.formal_counts(), before_formal)

        confirmed = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/confirm",
            json=self.confirmation_request(item),
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        forbidden = self.client.post(
            f"/pldr-api/v1/intake/{item_id}/archive",
            json={"analyst": "archive-tester", "reason": "Must preserve formal provenance"},
        )
        self.assertEqual(forbidden.status_code, 409, forbidden.text)

    def test_candidate_generation_rechecks_archive_state_after_model_await(self):
        from pldr_api.models import IntakeCandidate

        item_id = "int_race_archive_after_model"
        candidate_id = f"{item_id}:event"
        now = datetime.now(timezone.utc)
        original_snapshot = (
            "The preserved candidate states that concurrent archival must win over "
            "a model response without changing review data."
        )
        with SessionLocal() as session:
            session.add(
                IntakeItem(
                    id=item_id,
                    input_type="text",
                    status="generation_failed",
                    source_description="Concurrent archive regression",
                    language="en",
                    raw_snapshot=original_snapshot,
                    raw_hash="archive-race-raw",
                    extracted_snapshot=original_snapshot,
                    extracted_hash="archive-race-body",
                    candidate_mode="failed",
                    candidate_error="Preserve this prior model error",
                    candidate_relations=[
                        {"type": "event_claim", "from": "claim:1", "to": "event"}
                    ],
                    review={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                IntakeCandidate(
                    id=candidate_id,
                    item_id=item_id,
                    candidate_key="event",
                    object_type="event",
                    source_mode="fallback",
                    machine_data={"fields": {"title": "Preserved proposal"}},
                    disposition="pending",
                    created_at=now,
                )
            )
            session.commit()

        async def archive_while_model_is_awaited(*_args, **_kwargs):
            with SessionLocal() as concurrent_session:
                raced_item = concurrent_session.get(IntakeItem, item_id)
                assert raced_item is not None
                raced_item.archived_at = datetime.now(timezone.utc)
                raced_item.archived_by = "concurrent-archiver"
                raced_item.archive_reason = "Archive committed while model was running"
                concurrent_session.commit()
            return {"mode": "fallback"}

        with patch(
            "pldr_api.intake.run_model_task",
            new=archive_while_model_is_awaited,
        ):
            response = self.client.post(
                f"/pldr-api/v1/intake/{item_id}/regenerate"
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Restore", response.json()["detail"])

        with SessionLocal() as session:
            item = session.get(IntakeItem, item_id)
            candidate = session.get(IntakeCandidate, candidate_id)
            assert item is not None and candidate is not None
            self.assertIsNotNone(item.archived_at)
            self.assertEqual(item.archived_by, "concurrent-archiver")
            self.assertEqual(item.status, "generation_failed")
            self.assertEqual(item.candidate_mode, "failed")
            self.assertEqual(item.candidate_error, "Preserve this prior model error")
            self.assertEqual(
                item.candidate_relations,
                [{"type": "event_claim", "from": "claim:1", "to": "event"}],
            )
            self.assertEqual(
                candidate.machine_data,
                {"fields": {"title": "Preserved proposal"}},
            )
            self.assertEqual(candidate.disposition, "pending")
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(IntakeCandidate)
                    .where(IntakeCandidate.item_id == item_id)
                ),
                1,
            )

    def test_late_candidate_result_cannot_overwrite_newer_analyst_disposition(self):
        from pldr_api.intake import reject_intake
        from pldr_api.models import IntakeCandidate

        item_id = "int_generation_baseline_conflict"
        candidate_id = f"{item_id}:event"
        now = datetime.now(timezone.utc)
        snapshot = (
            "A late model result must never replace a newer analyst rejection or "
            "clear the candidate decision that was committed while it was running."
        )
        with SessionLocal() as session:
            session.add(
                IntakeItem(
                    id=item_id,
                    input_type="text",
                    status="generation_failed",
                    source_description="Generation baseline conflict fixture",
                    language="en",
                    raw_snapshot=snapshot,
                    raw_hash="generation-baseline-raw",
                    extracted_snapshot=snapshot,
                    extracted_hash="generation-baseline-body",
                    candidate_mode="failed",
                    candidate_error="Retryable provider failure",
                    review={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                IntakeCandidate(
                    id=candidate_id,
                    item_id=item_id,
                    candidate_key="event",
                    object_type="event",
                    source_mode="fallback",
                    machine_data={"fields": {"title": "Preserve rejected candidate"}},
                    disposition="pending",
                    created_at=now,
                )
            )
            session.commit()

        async def reject_while_model_is_running(*_args, **_kwargs):
            with SessionLocal() as concurrent_session:
                concurrent_item = concurrent_session.get(IntakeItem, item_id)
                assert concurrent_item is not None
                reject_intake(
                    concurrent_session,
                    concurrent_item,
                    "baseline-conflict-analyst",
                    "Analyst rejected while the model retry was running",
                )
            return {"mode": "fallback"}

        with patch(
            "pldr_api.intake.run_model_task",
            new=reject_while_model_is_running,
        ):
            response = self.client.post(
                f"/pldr-api/v1/intake/{item_id}/regenerate"
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("changed while work was running", response.json()["detail"])
        with SessionLocal() as session:
            item = session.get(IntakeItem, item_id)
            candidate = session.get(IntakeCandidate, candidate_id)
            assert item is not None and candidate is not None
            self.assertEqual(item.status, "rejected")
            self.assertEqual(item.disposition, "reject")
            self.assertEqual(item.reviewed_by, "baseline-conflict-analyst")
            self.assertEqual(
                item.rejection_reason,
                "Analyst rejected while the model retry was running",
            )
            self.assertEqual(candidate.disposition, "rejected")
            self.assertEqual(
                candidate.machine_data,
                {"fields": {"title": "Preserve rejected candidate"}},
            )
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(IntakeCandidate)
                    .where(IntakeCandidate.item_id == item_id)
                ),
                1,
            )

    def test_submission_routes_preserve_concurrently_archived_item_without_duplicate_failure(self):
        from pldr_api.models import IntakeCandidate

        async def archive_during_submission_model_call(_task, payload):
            item_id = payload["intake_item_id"]
            with SessionLocal() as concurrent_session:
                item = concurrent_session.get(IntakeItem, item_id)
                assert item is not None
                item.archived_at = datetime.now(timezone.utc)
                item.archived_by = "concurrent-submission-archiver"
                item.archive_reason = "Archive committed while submission model was running"
                concurrent_session.commit()
            # Exercise the generic generation-exception path as well as the
            # normal post-await archive check.  Neither path may turn the race
            # into generation_failed or let an outer submit wrapper create a
            # second failed IntakeItem.
            if payload["input_type"] == "text":
                raise RuntimeError("provider failed after concurrent archive")
            return {"mode": "fallback"}

        rss_xml = """<?xml version='1.0' encoding='UTF-8'?>
        <rss version='2.0'><channel><title>Archive race feed</title><item>
          <title>Archive race RSS item</title>
          <link>https://archive-race.example.org/rss-item</link>
          <description>This RSS description is deliberately long enough to reach candidate generation before a concurrent archive wins.</description>
        </item></channel></rss>"""
        cases = [
            (
                "web",
                lambda: self.client.post(
                    "/pldr-api/v1/import/url",
                    json={
                        "url": "https://archive-race.example.org/web-item",
                        "source_name": "Archive race web source",
                        "html": (
                            "<html><head><title>Archive race web item</title></head>"
                            "<body><article>This web material is deliberately long enough "
                            "to reach candidate generation before a concurrent archive wins."
                            "</article></body></html>"
                        ),
                        "language": "en",
                    },
                ),
            ),
            (
                "text",
                lambda: self.client.post(
                    "/pldr-api/v1/intake/text",
                    json={
                        "text": (
                            "This pasted material is deliberately long enough to reach candidate "
                            "generation before a concurrent archive wins."
                        ),
                        "source_description": "Archive race pasted source",
                        "language": "en",
                    },
                ),
            ),
            (
                "file",
                lambda: self.client.post(
                    "/pldr-api/v1/intake/files",
                    files={
                        "file": (
                            "archive-race.txt",
                            b"This local file is deliberately long enough to reach candidate generation before a concurrent archive wins.",
                            "text/plain",
                        )
                    },
                    data={"source_description": "Archive race local file", "language": "en"},
                ),
            ),
            (
                "rss",
                lambda: self.client.post(
                    "/pldr-api/v1/import/rss",
                    json={
                        "xml": rss_xml,
                        "source_name": "Archive race RSS source",
                        "language": "en",
                    },
                ),
            ),
        ]

        with patch(
            "pldr_api.intake.run_model_task",
            new=archive_during_submission_model_call,
        ):
            for expected_type, submit in cases:
                with self.subTest(input_type=expected_type):
                    with SessionLocal() as session:
                        before_ids = set(session.scalars(select(IntakeItem.id)).all())
                    response = submit()
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn("Restore", response.json()["detail"])
                    with SessionLocal() as session:
                        after_ids = set(session.scalars(select(IntakeItem.id)).all())
                        new_ids = after_ids - before_ids
                        self.assertEqual(len(new_ids), 1, new_ids)
                        item = session.get(IntakeItem, next(iter(new_ids)))
                        assert item is not None
                        self.assertEqual(item.input_type, expected_type)
                        self.assertEqual(item.status, "parsed")
                        self.assertIsNone(item.error)
                        self.assertIsNone(item.candidate_mode)
                        self.assertIsNone(item.candidate_error)
                        self.assertIsNotNone(item.archived_at)
                        self.assertEqual(item.archived_by, "concurrent-submission-archiver")
                        self.assertEqual(
                            session.scalar(
                                select(func.count())
                                .select_from(IntakeCandidate)
                                .where(IntakeCandidate.item_id == item.id)
                            ),
                            0,
                        )

    def test_archive_wins_between_confirmation_validation_and_formal_write_fence(self):
        from pldr_api.intake import archive_intake, lock_intake_for_mutation

        with patch(
            "pldr_api.intake.run_model_task",
            return_value={"mode": "fallback"},
        ):
            created = self.client.post(
                "/pldr-api/v1/intake/text",
                json={
                    "text": (
                        "This confirmation race fixture has exact evidence and must never "
                        "create formal objects after a concurrent archive commits."
                    ),
                    "source_description": "Confirmation archive race fixture",
                    "language": "en",
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["intake_item"]
        before_formal = self.formal_counts()
        archive_inserted = False

        def archive_before_confirmation_fence(session, item_id, *, action):
            nonlocal archive_inserted
            if action == "confirming it" and not archive_inserted:
                archive_inserted = True
                with SessionLocal() as concurrent_session:
                    concurrent_item = concurrent_session.get(IntakeItem, item_id)
                    assert concurrent_item is not None
                    archive_intake(
                        concurrent_session,
                        concurrent_item,
                        analyst="confirmation-race-archiver",
                        reason="Archive committed after validation and before formal writes",
                    )
            return lock_intake_for_mutation(session, item_id, action=action)

        with patch(
            "pldr_api.intake.lock_intake_for_mutation",
            new=archive_before_confirmation_fence,
        ):
            confirmed = self.client.post(
                f"/pldr-api/v1/intake/{item['id']}/confirm",
                json=self.confirmation_request(item),
            )
        self.assertTrue(archive_inserted)
        self.assertEqual(confirmed.status_code, 409, confirmed.text)
        self.assertIn("Restore", confirmed.json()["detail"])
        self.assertEqual(self.formal_counts(), before_formal)
        with SessionLocal() as session:
            persisted = session.get(IntakeItem, item["id"])
            assert persisted is not None
            self.assertTrue(persisted.archived_at)
            self.assertEqual(persisted.status, "candidate_ready")
            self.assertIsNone(persisted.final_event_id)
            self.assertIsNone(persisted.final_document_id)
            self.assertIsNone(persisted.confirmation_fingerprint)

    def test_archive_schema_migration_adds_columns_and_repairs_missing_indexes(self):
        from pldr_api import main as main_module

        migration_root = Path(tempfile.mkdtemp(prefix="pldr-archive-migration-"))
        migration_engine = create_engine(f"sqlite:///{migration_root / 'legacy.db'}")
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text("CREATE TABLE intake_items (id VARCHAR(80) PRIMARY KEY)")
                )
                connection.execute(
                    text(
                        "CREATE TABLE external_search_query_runs ("
                        "id VARCHAR(96) PRIMARY KEY, result_count INTEGER DEFAULT 0, "
                        "current_page INTEGER DEFAULT 1, page_size INTEGER DEFAULT 10, "
                        "returned_count INTEGER DEFAULT 0, has_more BOOLEAN DEFAULT 0, "
                        "total_known BOOLEAN DEFAULT 0, updated_at DATETIME, created_at DATETIME)"
                    )
                )
            with patch.object(main_module, "engine", migration_engine):
                main_module.ensure_compatible_schema()
            legacy = inspect(migration_engine)
            for table_name in ("intake_items", "external_search_query_runs"):
                columns = {column["name"] for column in legacy.get_columns(table_name)}
                self.assertTrue(
                    {"archived_at", "archived_by", "archive_reason"}.issubset(columns)
                )
            self.assertIn(
                "ix_intake_items_archived_at",
                {index["name"] for index in legacy.get_indexes("intake_items")},
            )
            self.assertIn(
                "ix_external_search_query_runs_archived_at",
                {
                    index["name"]
                    for index in legacy.get_indexes("external_search_query_runs")
                },
            )

            with migration_engine.begin() as connection:
                connection.execute(text("DROP INDEX ix_intake_items_archived_at"))
                connection.execute(
                    text("DROP INDEX ix_external_search_query_runs_archived_at")
                )
            with patch.object(main_module, "engine", migration_engine):
                main_module.ensure_compatible_schema()
            repaired = inspect(migration_engine)
            self.assertIn(
                "ix_intake_items_archived_at",
                {index["name"] for index in repaired.get_indexes("intake_items")},
            )
            self.assertIn(
                "ix_external_search_query_runs_archived_at",
                {
                    index["name"]
                    for index in repaired.get_indexes("external_search_query_runs")
                },
            )
        finally:
            migration_engine.dispose()
            shutil.rmtree(migration_root, ignore_errors=True)

    def test_investigation_onboarding_schema_migration_preserves_existing_topics(self):
        from pldr_api import main as main_module

        migration_root = Path(tempfile.mkdtemp(prefix="pldr-topic-migration-"))
        migration_engine = create_engine(f"sqlite:///{migration_root / 'legacy.db'}")
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE investigations ("
                        "id VARCHAR(80) PRIMARY KEY, title VARCHAR(160) NOT NULL, "
                        "question TEXT, description TEXT, status VARCHAR(20), "
                        "created_at DATETIME, updated_at DATETIME)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO investigations "
                        "(id, title, question, description, status, created_at, updated_at) "
                        "VALUES ('legacy-topic', '既有专题', '既有问题', '', 'active', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
            with patch.object(main_module, "engine", migration_engine):
                main_module.ensure_compatible_schema()
                main_module.ensure_compatible_schema()
            columns = {
                column["name"]
                for column in inspect(migration_engine).get_columns("investigations")
            }
            self.assertTrue(
                {"tracking_mode", "event_start_at", "event_end_at", "settings_json"}.issubset(columns)
            )
            with migration_engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT title, tracking_mode, settings_json "
                        "FROM investigations WHERE id='legacy-topic'"
                    )
                ).one()
            self.assertEqual(row.title, "既有专题")
            self.assertEqual(row.tracking_mode, "one_time")
            self.assertEqual(str(row.settings_json), "{}")
        finally:
            migration_engine.dispose()
            shutil.rmtree(migration_root, ignore_errors=True)

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
            self.assertNotIn("正文 SHA-256", snapshot.text)
            self.assertNotIn("Snapshot：", snapshot.text)
            self.assertNotIn("独立来源组：", snapshot.text)
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
        self.assertNotRegex(report_text, r"\d{4}-\d{2}-\d{2}T\d{2}:")
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
                *[
                    self.search_hit(
                        f"https://news.example.org/story-{index}",
                        title=f"Additional result {index}",
                    )
                    for index in range(1, 8)
                ],
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
        self.assertEqual(news_payload["result_count"], 5)
        self.assertEqual(len(news_payload["results"]), 5)
        self.assertEqual(web_payload["result_count"], 5)
        self.assertEqual(len(web_payload["results"]), 5)
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
                10,
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

            async def post(self, endpoint, json=None, headers=None):
                calls.append(("POST", endpoint, json, headers))
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

        self.assertEqual(calls[0][1], "https://api.search.brave.com/res/v1/news/search")
        self.assertEqual(calls[0][2]["q"], "adapter contract")
        self.assertEqual(calls[0][2]["count"], 5)
        self.assertEqual(calls[0][2]["search_lang"], "en")
        self.assertEqual(calls[0][2]["country"], "ALL")
        self.assertEqual(calls[0][2]["safesearch"], "strict")
        self.assertIs(calls[0][2]["spellcheck"], False)
        self.assertNotIn("result_filter", calls[0][2])
        self.assertNotIn("text_decorations", calls[0][2])
        self.assertEqual(calls[0][3]["X-Subscription-Token"], "test-key")
        self.assertEqual(calls[0][3]["Accept"], "application/json")
        self.assertEqual(calls[0][3]["Content-Type"], "application/json")
        self.assertEqual(calls[1][1], "https://api.search.brave.com/res/v1/web/search")
        self.assertEqual(calls[1][2]["result_filter"], ["web"])
        self.assertIs(calls[1][2]["text_decorations"], False)
        self.assertNotIn("decorators", calls[1][2])
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

        with patch("pldr_api.search.request_search", controlled_backend):
            second_search = self.client.post(
                "/pldr-api/v1/search",
                json={"keyword": "selective intake second query", "scope": "web"},
            )
            self.assertEqual(second_search.status_code, 200, second_search.text)
            second_result = second_search.json()["results"][0]
            self.assertNotEqual(second_result["id"], results[0]["id"])
            second_selected = self.client.post(
                "/pldr-api/v1/search/select", json={"result_ids": [second_result["id"]]}
            )
            self.assertEqual(second_selected.status_code, 200, second_selected.text)

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
        second_alpha = second_selected.json()["results"][0]
        self.assertEqual(second_alpha["outcome"], "already_added")
        self.assertEqual(second_alpha["intake_item_id"], alpha_response["intake_item_id"])
        self.assertEqual(second_alpha["result"]["selection"]["latest_query_run_id"], second_search.json()["id"])
        self.assertEqual(second_alpha["result"]["selection"]["latest_result_id"], second_result["id"])

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
            self.assertEqual(trace["keyword"], "selective intake second query")
            self.assertEqual(trace["scope"], "web")
            self.assertEqual(trace["channel"], "brave-search-api:web")
            self.assertEqual(trace["result_id"], second_result["id"])
            self.assertEqual(trace["query_run_id"], second_search.json()["id"])
            self.assertEqual(trace["original_url"], "https://alpha.example.org/selected")
            self.assertEqual(trace["search_title"], "Alpha search headline")
            history = alpha.review["external_search_history"]
            self.assertEqual(
                [entry["query_run_id"] for entry in history],
                [search.json()["id"], search.json()["id"], second_search.json()["id"]],
            )
            self.assertEqual(
                [entry["result_id"] for entry in history],
                [results[0]["id"], results[0]["id"], second_result["id"]],
            )
            selection = session.scalar(
                select(SearchSelection).where(SearchSelection.intake_item_id == alpha.id)
            )
            assert selection is not None
            events = list(
                session.scalars(
                    select(SearchSelectionEvent)
                    .where(SearchSelectionEvent.selection_id == selection.id)
                    .order_by(SearchSelectionEvent.created_at)
                )
            )
            self.assertEqual([event.outcome for event in events], ["added", "already_added", "already_added"])
            self.assertEqual(
                [event.query_run_id for event in events],
                [search.json()["id"], search.json()["id"], second_search.json()["id"]],
            )

        serialized_second_trace = self.client.get(
            f"/pldr-api/v1/intake/{alpha_response['intake_item_id']}"
        ).json()
        self.assertEqual(serialized_second_trace["search"]["query_run_id"], second_search.json()["id"])
        self.assertEqual(
            [entry["result_id"] for entry in serialized_second_trace["search_history"]],
            [results[0]["id"], results[0]["id"], second_result["id"]],
        )

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
        with SessionLocal() as session:
            from pldr_api.models import ReviewTask

            task = session.scalar(
                select(ReviewTask).where(
                    ReviewTask.intake_item_id == charlie_response["intake_item_id"]
                )
            )
            assert task is not None
            self.assertEqual(task.status, "ready")
            self.assertIsNone(task.error_class)
            self.assertIsNone(task.error_message)
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

            async def get(self, *_, **__):
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
        self.assertIn("const snapshotId = final.snapshot || item.final_snapshot_id", script.text)
        self.assertIn('href="/snapshots/${escapeHtml(snapshotId)}"', script.text)
        self.assertIn("没有匹配结果。PLDR 不会用演示数据填充空结果。", script.text)
        self.assertIn("未生成演示结果。", script.text)
        self.assertIn("data-search-retry=", script.text)
        self.assertIn('data-intake-action="retry-search"', script.text)
        self.assertIn('event.target.closest("button[data-intake-step]")', script.text)
        self.assertIn("本次查询最多保留", script.text)
        self.assertIn("item.search_history", script.text)
        self.assertIn("处理追踪", script.text)
        self.assertIn('escapeHtml(result.title || "无标题")', script.text)
        report_source = script.text.split("async function generateReport", 1)[1].split(
            "\n}\n\nfunction openImportModal", 1
        )[0]
        self.assertIn("const scopeInvestigation = eventOverviewInvestigation();", report_source)
        self.assertIn("isServerInvestigation(scopeInvestigation)", report_source)
        self.assertIn(
            "...(scopeInvestigationId ? { investigation_id: scopeInvestigationId } : {})",
            report_source,
        )

        styles = self.client.get("/assets/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertIn("@media (max-width: 580px)", styles.text)
        self.assertIn(".intake-layout { grid-template-columns: 1fr;", styles.text)
        self.assertIn(".search-modal", styles.text)
        self.assertIn(".search-result", styles.text)

    def test_initial_route_only_opens_drawer_for_a_valid_event_deep_link(self):
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
        self.assertIn("const eventOverviewRoute =", init_source)
        self.assertIn(
            "requestedEvent && eventOverviewEvents().some((event) => event.id === requestedEvent)",
            init_source,
        )
        self.assertIn(
            "await selectEvent(requestedEvent, { open: true, syncUrl: false });",
            init_source,
        )
        self.assertIn("showInvestigationHome({ syncUrl: false });", init_source)
        self.assertNotIn("openDrawer();", init_source)


if __name__ == "__main__":
    unittest.main()
