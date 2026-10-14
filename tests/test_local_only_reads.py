"""The app reads clients, assessments and documents from the local files only.
MongoDB is a backup that Manual Sync writes to; nothing is ever read back from it."""

import json
import os

import pytest

from app import data_manager, utilities


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    """Empty local database in a temporary folder, and MongoDB that fails the test if it is used."""
    db = tmp_path / "db"
    db.mkdir()
    monkeypatch.setattr(data_manager, "LOCAL_CASES_JSON", str(db / "sow_cases.json"))
    monkeypatch.setattr(data_manager, "LOCAL_ASSESSMENTS_JSON", str(db / "ai_assessments.json"))

    def no_mongo(*args, **kwargs):
        raise AssertionError("MongoDB was read")
    monkeypatch.setattr(data_manager, "get_collection", no_mongo)
    monkeypatch.setattr(data_manager, "get_assessment_collection", no_mongo)
    return db


def test_deleted_local_database_means_no_clients(local_db):
    assert data_manager.load_all_records() == []
    assert data_manager.find_client_record("SOW-2026-0001") is None
    assert data_manager.load_all_assessments() == []


def test_clients_and_assessments_come_from_the_local_files(local_db):
    (local_db / "sow_cases.json").write_text(json.dumps([{"client_ref": "SOW-2026-0001", "full_name": "A"}]))
    (local_db / "ai_assessments.json").write_text(json.dumps([{"client_ref": "SOW-2026-0001", "outcome": "PASSED"}]))
    assert [r["client_ref"] for r in data_manager.load_all_records()] == ["SOW-2026-0001"]
    assert data_manager.find_client_record("sow-2026-0001")["full_name"] == "A"
    assert len(data_manager.load_all_assessments()) == 1


def test_documents_are_copied_from_the_local_copy_only(local_db, tmp_path, monkeypatch):
    monkeypatch.setattr(utilities, "ROOT_DIR", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "payslip.txt").write_text("Basic salary 5,000")
    out = tmp_path / "out" / "payslip.txt"
    assert data_manager.download_supporting_document("data/payslip.txt", str(out))
    assert out.read_text() == "Basic salary 5,000"
    # an id that only exists in MongoDB (after a sync) is not fetched from the cloud
    assert not data_manager.download_supporting_document("652f1c2e9b1e8a3f4c5d6e7f", str(tmp_path / "x.txt"))


def test_synced_document_is_read_from_its_local_path(local_db, tmp_path, monkeypatch):
    monkeypatch.setattr(utilities, "ROOT_DIR", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "letter.txt").write_text("Employment letter")
    entry = {"file_name": "letter.txt", "file_path": "data/letter.txt", "file_id": "652f1c2e9b1e8a3f4c5d6e7f"}
    read = data_manager.read_supporting_documents([entry])
    assert read[0]["kind"] == "text" and "Employment letter" in read[0]["text"]
