from __future__ import annotations

import unittest


class IntakeMultiVersionTest(unittest.TestCase):
    """Focused regression coverage for one canonical Document with many reviewed versions."""

    def setUp(self) -> None:
        # Import lazily so unittest discovery does not initialize PLDR's process-global
        # database before test_p0.py has selected its own isolated database.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from pldr_api.database import Base
        from pldr_api import models as _models  # noqa: F401 - register every table on Base

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def _add_item(
        self,
        session,
        *,
        body: str,
        title: str,
        fetched_at: str,
        collection: dict | None = None,
        url: str = "https://versioned.example.org/report",
    ) -> str:
        from pldr_api.extraction import content_hash
        from pldr_api.intake import _base_item, _store_candidates, sha256_text

        review = {"material": {"fetched_at": fetched_at}}
        if collection is not None:
            review["collection"] = collection
        item = _base_item(
            "collection" if collection is not None else "web",
            source_description="Versioned Example",
            source_url=url,
            canonical_url=url,
            title=title,
            language="en",
            raw_snapshot=body,
            raw_hash=sha256_text(body),
            extracted_snapshot=body,
            extracted_hash=content_hash(body),
            review=review,
        )
        session.add(item)
        session.flush()
        _store_candidates(
            session,
            item,
            {
                "event": {"title": title, "summary": body, "event_time": None, "location_name": None},
                "entities": [],
                "claims": [
                    {
                        "text": f"资料显示：{title}",
                        "uncertainty": None,
                        "temporal_scope": None,
                        "evidence": [{"snippet": body, "stance": "supports", "strength": 0.9}],
                    }
                ],
            },
            "fallback",
            None,
        )
        item.status = "candidate_ready"
        session.commit()
        return item.id

    @staticmethod
    def _request(*, title: str, body: str, merge_event_id: str | None = None):
        from pldr_api.schemas import IntakeConfirmationRequest

        return IntakeConfirmationRequest(
            disposition="merge" if merge_event_id else "create",
            analyst="version-reviewer",
            merge_event_id=merge_event_id,
            event={
                "title": title,
                "summary": body,
                "event_type": "incident",
                "start_at": None,
                "location_name": "",
                "importance": "medium",
            },
            entities=[],
            claims=[
                {
                    "candidate_key": "claim:1",
                    "action": "create",
                    "text": f"资料显示：{title}",
                    "status": "verified",
                    "confidence": 0.9,
                    "temporal_scope": "",
                    "merge_claim_id": None,
                }
            ],
            evidence=[
                {
                    "candidate_key": "evidence:1",
                    "action": "include",
                    "snippet": body,
                    "stance": "supports",
                    "strength": 0.9,
                    "note": "Reviewed against this exact version.",
                }
            ],
        )

    def _confirm(self, session, item_id: str, request, failure_hook=None):
        from pldr_api.intake import confirm_intake, get_intake_item

        item = get_intake_item(session, item_id)
        self.assertIsNotNone(item)
        return confirm_intake(session, item, request, failure_hook=failure_hook)

    def _confirmed_first_version(self, session):
        first_body = (
            "Version one states that the monitored terminal remained open after the morning inspection."
        )
        first_item_id = self._add_item(
            session,
            body=first_body,
            title="Terminal status: version one",
            fetched_at="2026-08-29T01:00:00Z",
        )
        _, first_result, created = self._confirm(
            session,
            first_item_id,
            self._request(title="Terminal status: version one", body=first_body),
        )
        self.assertTrue(created)
        return first_body, first_item_id, first_result

    def test_new_reviewed_body_appends_snapshot_and_pins_old_and_new_evidence(self):
        from sqlalchemy import func, select

        from pldr_api.intake import get_intake_item
        from pldr_api.models import Claim, Document, Evidence, Snapshot
        from pldr_api.repository import get_event, serialize_event_detail

        collection_trace = {
            "target_id": "watch-versioned-example",
            "run_id": "run-0002",
            "version_number": 2,
        }
        second_body = (
            "Version two states that the monitored terminal closed after the afternoon safety inspection."
        )
        with self.Session() as session:
            first_body, _, first_result = self._confirmed_first_version(session)
            event_id = first_result["formal_object_ids"]["event"]
            document_id = first_result["formal_object_ids"]["document"]
            old_snapshot_id = first_result["formal_object_ids"]["snapshot"]
            old_evidence_id = first_result["formal_object_ids"]["evidence"][0]

            second_item_id = self._add_item(
                session,
                body=second_body,
                title="Terminal status: version two",
                fetched_at="2026-08-29T02:00:00Z",
                collection=collection_trace,
            )
            second_request = self._request(
                title="Terminal status: version two",
                body=second_body,
                merge_event_id=event_id,
            )
            second_item, second_result, created = self._confirm(session, second_item_id, second_request)
            self.assertTrue(created)
            self.assertEqual(second_result["formal_object_ids"]["document"], document_id)
            new_snapshot_id = second_result["formal_object_ids"]["snapshot"]
            new_evidence_id = second_result["formal_object_ids"]["evidence"][0]
            self.assertNotEqual(new_snapshot_id, old_snapshot_id)
            self.assertEqual(second_result["trace"]["collection"], collection_trace)

            document = session.get(Document, document_id)
            old_snapshot = session.get(Snapshot, old_snapshot_id)
            new_snapshot = session.get(Snapshot, new_snapshot_id)
            old_evidence = session.get(Evidence, old_evidence_id)
            new_evidence = session.get(Evidence, new_evidence_id)
            self.assertIsNotNone(document)
            self.assertIsNotNone(old_snapshot)
            self.assertIsNotNone(new_snapshot)
            self.assertIsNotNone(old_evidence)
            self.assertIsNotNone(new_evidence)
            self.assertEqual(document.title, "Terminal status: version two")
            self.assertEqual(document.body, second_body)
            self.assertEqual(document.content_hash, new_snapshot.content_hash)
            self.assertTrue(document.fetched_at.isoformat().startswith("2026-08-29T02:00:00"))
            self.assertEqual(document.metadata_json["latest_snapshot_id"], new_snapshot_id)
            self.assertEqual(document.metadata_json["latest_intake_item_id"], second_item_id)
            self.assertEqual(document.metadata_json["intake_item_ids"], [
                first_result["trace"]["intake_item_id"],
                second_item_id,
            ])
            self.assertEqual(old_snapshot.excerpt, first_body)
            self.assertEqual(new_snapshot.excerpt, second_body)
            self.assertEqual(old_evidence.snapshot_id, old_snapshot_id)
            self.assertEqual(new_evidence.snapshot_id, new_snapshot_id)
            self.assertEqual(
                old_snapshot.excerpt[old_evidence.start_offset : old_evidence.end_offset],
                old_evidence.snippet,
            )
            self.assertEqual(
                new_snapshot.excerpt[new_evidence.start_offset : new_evidence.end_offset],
                new_evidence.snippet,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.document_id == document_id)),
                2,
            )

            session.expire_all()
            detail = serialize_event_detail(get_event(session, event_id))
            serialized_evidence = {
                evidence["id"]: evidence
                for claim in detail["claims"]
                for evidence in claim["evidence"]
            }
            serialized_old = serialized_evidence[old_evidence_id]
            serialized_new = serialized_evidence[new_evidence_id]
            self.assertEqual(serialized_old["snapshot_id"], old_snapshot_id)
            self.assertIn(old_snapshot_id, serialized_old["snapshot_url"])
            self.assertEqual(serialized_old["document"]["snapshot_id"], old_snapshot_id)
            self.assertIn(old_snapshot_id, serialized_old["document"]["snapshot_url"])
            self.assertEqual(serialized_old["document"]["snapshot_role"], "evidence-fixed")
            self.assertEqual(serialized_old["document"]["title"], "Terminal status: version one")
            self.assertEqual(serialized_old["document"]["content_hash"], old_snapshot.content_hash)
            self.assertTrue(serialized_old["document"]["fetched_at"].startswith("2026-08-29T01:00:00"))
            self.assertEqual(
                serialized_old["document"]["provenance"]["intake_item_id"],
                first_result["trace"]["intake_item_id"],
            )
            self.assertEqual(serialized_old["document"]["latest_snapshot_id"], new_snapshot_id)
            self.assertIn(new_snapshot_id, serialized_old["document"]["latest_snapshot_url"])
            self.assertEqual(
                serialized_old["document"]["document_head"]["content_hash"],
                new_snapshot.content_hash,
            )
            self.assertEqual(serialized_new["snapshot_id"], new_snapshot_id)
            self.assertEqual(detail["documents"][0]["snapshot_id"], new_snapshot_id)
            self.assertEqual(detail["documents"][0]["snapshot_role"], "document-latest")

            before_repeat = {
                "snapshots": session.scalar(select(func.count()).select_from(Snapshot)),
                "claims": session.scalar(select(func.count()).select_from(Claim)),
                "evidence": session.scalar(select(func.count()).select_from(Evidence)),
            }
            repeated_item = get_intake_item(session, second_item.id)
            _, repeated_result, repeated_created = self._confirm(
                session,
                repeated_item.id,
                second_request,
            )
            self.assertFalse(repeated_created)
            self.assertEqual(repeated_result, second_result)
            self.assertEqual(
                {
                    "snapshots": session.scalar(select(func.count()).select_from(Snapshot)),
                    "claims": session.scalar(select(func.count()).select_from(Claim)),
                    "evidence": session.scalar(select(func.count()).select_from(Evidence)),
                },
                before_repeat,
            )

    def test_later_failure_rolls_back_document_head_and_appended_snapshot(self):
        from sqlalchemy import func, select

        from pldr_api.intake import get_intake_item
        from pldr_api.models import Claim, Document, Evidence, Snapshot

        failed_body = (
            "A later version states that the monitored terminal status changed, but promotion must fail."
        )
        with self.Session() as session:
            first_body, _, first_result = self._confirmed_first_version(session)
            event_id = first_result["formal_object_ids"]["event"]
            document_id = first_result["formal_object_ids"]["document"]
            old_snapshot_id = first_result["formal_object_ids"]["snapshot"]
            failed_item_id = self._add_item(
                session,
                body=failed_body,
                title="Terminal status: failed version",
                fetched_at="2026-08-29T03:00:00Z",
            )
            failed_request = self._request(
                title="Terminal status: failed version",
                body=failed_body,
                merge_event_id=event_id,
            )
            baseline = {
                "snapshots": session.scalar(select(func.count()).select_from(Snapshot)),
                "claims": session.scalar(select(func.count()).select_from(Claim)),
                "evidence": session.scalar(select(func.count()).select_from(Evidence)),
            }

            with self.assertRaisesRegex(RuntimeError, "injected after document advance"):
                self._confirm(
                    session,
                    failed_item_id,
                    failed_request,
                    failure_hook=lambda: (_ for _ in ()).throw(
                        RuntimeError("injected after document advance")
                    ),
                )

            session.expire_all()
            document = session.get(Document, document_id)
            failed_item = get_intake_item(session, failed_item_id)
            self.assertEqual(document.title, "Terminal status: version one")
            self.assertEqual(document.body, first_body)
            self.assertEqual(document.metadata_json["latest_snapshot_id"], old_snapshot_id)
            self.assertEqual(failed_item.status, "candidate_ready")
            self.assertIsNone(failed_item.final_document_id)
            self.assertIsNone(failed_item.final_snapshot_id)
            self.assertTrue(all(candidate.final_object_id is None for candidate in failed_item.candidates))
            self.assertEqual(
                {
                    "snapshots": session.scalar(select(func.count()).select_from(Snapshot)),
                    "claims": session.scalar(select(func.count()).select_from(Claim)),
                    "evidence": session.scalar(select(func.count()).select_from(Evidence)),
                },
                baseline,
            )

    def test_out_of_order_review_keeps_newest_collection_version_as_document_head(self):
        from sqlalchemy import func, select

        from pldr_api.models import Document, Evidence, Snapshot
        from pldr_api.main import snapshot as render_snapshot
        from pldr_api.repository import get_event, serialize_event_detail

        older_body = (
            "Collection version one reports that the monitored terminal remained open "
            "before the later safety decision was published."
        )
        newer_body = (
            "Collection version two reports that the monitored terminal closed after "
            "the later safety decision was published."
        )
        with self.Session() as session:
            older_id = self._add_item(
                session,
                body=older_body,
                title="Terminal status: collection V1",
                fetched_at="2026-08-29T01:00:00Z",
                collection={
                    "target_id": "watch-versioned-example",
                    "run_id": "run-v1",
                    "version_number": 1,
                },
            )
            newer_id = self._add_item(
                session,
                body=newer_body,
                title="Terminal status: collection V2",
                fetched_at="2026-08-29T02:00:00Z",
                collection={
                    "target_id": "watch-versioned-example",
                    "run_id": "run-v2",
                    "version_number": 2,
                },
            )

            _, newer_result, _ = self._confirm(
                session,
                newer_id,
                self._request(title="Terminal status: collection V2", body=newer_body),
            )
            event_id = newer_result["formal_object_ids"]["event"]
            document_id = newer_result["formal_object_ids"]["document"]
            newer_snapshot_id = newer_result["formal_object_ids"]["snapshot"]
            _, older_result, _ = self._confirm(
                session,
                older_id,
                self._request(
                    title="Terminal status: collection V1",
                    body=older_body,
                    merge_event_id=event_id,
                ),
            )
            older_snapshot_id = older_result["formal_object_ids"]["snapshot"]

            session.expire_all()
            document = session.get(Document, document_id)
            older_snapshot = session.get(Snapshot, older_snapshot_id)
            newer_snapshot = session.get(Snapshot, newer_snapshot_id)
            self.assertEqual(document.title, "Terminal status: collection V2")
            self.assertEqual(document.body, newer_body)
            self.assertEqual(document.content_hash, newer_snapshot.content_hash)
            self.assertEqual(document.metadata_json["latest_snapshot_id"], newer_snapshot_id)
            self.assertEqual(document.metadata_json["latest_collection"]["version_number"], 2)
            self.assertEqual(
                document.metadata_json["intake_item_ids"],
                [newer_id, older_id],
            )
            self.assertEqual(older_snapshot.metadata_json["title"], "Terminal status: collection V1")
            self.assertEqual(newer_snapshot.metadata_json["title"], "Terminal status: collection V2")
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(Snapshot).where(Snapshot.document_id == document_id)
                ),
                2,
            )

            detail = serialize_event_detail(get_event(session, event_id))
            older_evidence_id = older_result["formal_object_ids"]["evidence"][0]
            serialized_old = next(
                evidence
                for claim in detail["claims"]
                for evidence in claim["evidence"]
                if evidence["id"] == older_evidence_id
            )
            stored_old_evidence = session.get(Evidence, older_evidence_id)
            self.assertEqual(stored_old_evidence.snapshot_id, older_snapshot_id)
            self.assertEqual(serialized_old["document"]["snapshot_id"], older_snapshot_id)
            self.assertEqual(serialized_old["document"]["title"], "Terminal status: collection V1")
            self.assertEqual(serialized_old["document"]["content_hash"], older_snapshot.content_hash)
            self.assertEqual(
                serialized_old["document"]["document_head"]["snapshot_id"],
                newer_snapshot_id,
            )
            rendered_old = render_snapshot(older_snapshot_id, event_id, session)
            self.assertIn("Terminal status: collection V1", rendered_old)
            self.assertNotIn("正文 SHA-256", rendered_old)
            self.assertNotIn("Terminal status: collection V2", rendered_old)
            self.assertIn("name='viewport'", rendered_old)
            self.assertIn("overflow-wrap:anywhere", rendered_old)

    def test_updating_duplicate_family_root_does_not_create_reference_cycle(self):
        from pldr_api.models import Document, Snapshot

        body = "A sufficiently long identical dispatch remains stable across two public locations."
        with self.Session() as session:
            first_id = self._add_item(
                session, body=body, title="Root report", fetched_at="2026-08-29T01:00:00Z"
            )
            _, first_result, _ = self._confirm(
                session, first_id, self._request(title="Root report", body=body)
            )
            root_id = first_result["formal_object_ids"]["document"]
            event_id = first_result["formal_object_ids"]["event"]

            repost_id = self._add_item(
                session, body=body, title="Repost", fetched_at="2026-08-29T02:00:00Z",
                url="https://repost.example.org/report",
            )
            _, repost_result, _ = self._confirm(
                session, repost_id, self._request(title="Repost", body=body)
            )
            repost = session.get(Document, repost_result["formal_object_ids"]["document"])
            self.assertEqual(repost.metadata_json["duplicate_of_document_id"], root_id)

            update_id = self._add_item(
                session, body=body, title="Root report updated", fetched_at="2026-08-29T03:00:00Z"
            )
            _, update_result, _ = self._confirm(
                session, update_id,
                self._request(title="Root report updated", body=body, merge_event_id=event_id),
            )
            root = session.get(Document, root_id)
            snapshot = session.get(Snapshot, update_result["formal_object_ids"]["snapshot"])
            self.assertNotIn("duplicate_of_document_id", root.metadata_json)
            self.assertNotIn("duplicate_of_document_id", snapshot.metadata_json)


if __name__ == "__main__":
    unittest.main()
