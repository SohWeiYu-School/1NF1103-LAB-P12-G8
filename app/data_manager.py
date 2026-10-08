"""
data_manager.py - the data layer: the system's memory across runs (flat JSON files).

Layout under DATA_DIR (default ./data):
    data/sample/<case>.json                       sample client cases (input)
    data/raw/<client_ref>/<piece>.json            the AI's raw reply for each request (the cache)
    data/forecasts/<client_ref>.json              the combined AI forecast for a client
    data/assessments/<client_ref>/<kind>.json     the logic manager's decision (onboarding or a review)

Every function handles missing or corrupt files without crashing.
"""

import json
import logging
import os
<<<<<<< HEAD
import re

logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", "data")
=======
import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from gridfs import GridFS

DB_NAME = "sow_risk_db"
COLLECTION_NAME = "client_cases"
ASSESSMENT_COLLECTION_NAME = "ai_assessments"
>>>>>>> cc91561


def raw_dir() -> str:
    return os.path.join(DATA_DIR, "raw")


def forecast_dir() -> str:
    return os.path.join(DATA_DIR, "forecasts")


def assessment_dir() -> str:
    return os.path.join(DATA_DIR, "assessments")


def sample_dir() -> str:
    return os.path.join(DATA_DIR, "sample")


# ----------------------------------------------------------------------------
# JSON helpers
# ----------------------------------------------------------------------------

def safe_name(value: str) -> str:
    """Turn a client ref or piece name into a safe file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "unknown"))


def load_json(path: str, default: object = None) -> object:
    """Load a JSON file. Returns `default` if it's missing or corrupt."""
    if not os.path.exists(path):
        return default
    try:
<<<<<<< HEAD
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        logger.error("Could not read %s (%s); ignoring it", path, error)
        return default


def save_json(path: str, data: object) -> bool:
    """Write JSON safely (temp file, then rename). Returns True if it worked."""
=======
        client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=2000, tlsCAFile=certifi.where())
        db = client[DB_NAME]
        return db[COLLECTION_NAME]
    except Exception:
        return None


def get_assessment_collection():
    """Returns the ai_assessments collection object or None if connection fails."""
    try:
        client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=2000, tlsCAFile=certifi.where())
        db = client[DB_NAME]
        return db[ASSESSMENT_COLLECTION_NAME]
    except Exception:
        return None


def save_case_record(record: dict) -> bool:
    """Saves or updates a client risk assessment record in MongoDB."""
    if not isinstance(record, dict) or "client_ref" not in record:
        return False

    collection = get_collection()
    if collection is None:
        return False

>>>>>>> cc91561
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(temp_path, path)
        return True
    except (OSError, TypeError, ValueError) as error:
        logger.error("Could not write %s: %s", path, error)
        return False


# ----------------------------------------------------------------------------
# Client cases (input)
# ----------------------------------------------------------------------------

def list_sample_cases() -> list[str]:
    """Paths of every case file in data/sample, sorted."""
    folder = sample_dir()
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, name) for name in sorted(os.listdir(folder)) if name.endswith(".json")]


def load_case(path: str) -> dict | None:
    """Load one case file. Returns None if it's missing, corrupt or not a JSON object."""
    case = load_json(path, default=None)
    return case if isinstance(case, dict) else None


def save_case(case: dict) -> str | None:
    """Save a case typed in by the officer to data/cases/<client_ref>.json."""
    path = os.path.join(DATA_DIR, "cases", f"{safe_name(case.get('client_ref'))}.json")
    return path if save_json(path, case) else None


# ----------------------------------------------------------------------------
# Raw AI replies (the cache that makes reruns free and identical)
# ----------------------------------------------------------------------------

def raw_reply_path(client_ref: str, piece_name: str) -> str:
    return os.path.join(raw_dir(), safe_name(client_ref), f"{safe_name(piece_name)}.json")


def load_raw_replies(client_ref: str) -> dict:
    """{piece_name: raw_text} for every saved reply of this client."""
    folder = os.path.join(raw_dir(), safe_name(client_ref))
    if not os.path.isdir(folder):
        return {}
    replies = {}
    for file_name in sorted(os.listdir(folder)):
        if not file_name.endswith(".json"):
            continue
        entry = load_json(os.path.join(folder, file_name), default={})
        if isinstance(entry, dict) and entry.get("call") and isinstance(entry.get("raw"), str):
            replies[entry["call"]] = entry["raw"]
    return replies


def save_raw_replies(client_ref: str, results: dict) -> int:
    """Save each successful reply that came fresh from the API. Returns how many were saved."""
    saved = 0
    for piece_name, result in results.items():
        if result.get("ok") and not result.get("from_cache") and result.get("raw"):
            entry = {"call": piece_name, "raw": result["raw"], "attempts": result.get("attempts"),
                     "prompt_version": result.get("prompt_version")}
            if save_json(raw_reply_path(client_ref, piece_name), entry):
                saved += 1
    return saved


# ----------------------------------------------------------------------------
# Forecasts
# ----------------------------------------------------------------------------

def forecast_path(client_ref: str) -> str:
    return os.path.join(forecast_dir(), f"{safe_name(client_ref)}.json")


def save_forecast(client_ref: str, forecast: dict) -> str | None:
    path = forecast_path(client_ref)
    return path if save_json(path, {"client_ref": client_ref, "forecast": forecast}) else None


def load_forecast(client_ref: str) -> dict | None:
    record = load_json(forecast_path(client_ref), default=None)
    if isinstance(record, dict) and isinstance(record.get("forecast"), dict):
        return record["forecast"]
    return None


# ----------------------------------------------------------------------------
# Assessments (the logic manager's decisions)
# ----------------------------------------------------------------------------

def assessment_path(client_ref: str, kind: str) -> str:
    return os.path.join(assessment_dir(), safe_name(client_ref), f"{safe_name(kind)}.json")


def save_assessment(client_ref: str, kind: str, assessment: dict) -> str | None:
    """kind is 'onboarding' or 'review_<date>'."""
    path = assessment_path(client_ref, kind)
    return path if save_json(path, {"client_ref": client_ref, "kind": kind, "assessment": assessment}) else None


def load_assessment(client_ref: str, kind: str) -> dict | None:
    record = load_json(assessment_path(client_ref, kind), default=None)
    if isinstance(record, dict) and isinstance(record.get("assessment"), dict):
        return record["assessment"]
    return None


def load_all_assessments() -> list[dict]:
    """Every saved decision, loaded at startup. Corrupt files are skipped."""
    folder = assessment_dir()
    if not os.path.isdir(folder):
        return []
    rows = []
    for client_folder in sorted(os.listdir(folder)):
        path = os.path.join(folder, client_folder)
        if not os.path.isdir(path):
            continue
        for file_name in sorted(os.listdir(path)):
            if not file_name.endswith(".json"):
                continue
            record = load_json(os.path.join(path, file_name), default=None)
            if isinstance(record, dict) and isinstance(record.get("assessment"), dict):
                rows.append(record)
    return rows


<<<<<<< HEAD
def query_assessments(records: list[dict], outcome: str | None = None,
                      client_ref: str | None = None, kind: str | None = None) -> list[dict]:
    """Filter saved decisions by outcome, client and/or kind ('onboarding' or 'review')."""
    matches = []
    for record in records:
        assessment = record["assessment"]
        if outcome and assessment.get("outcome") != outcome:
            continue
        if client_ref and record.get("client_ref") != client_ref:
            continue
        if kind and not str(record.get("kind", "")).startswith(kind):
            continue
        matches.append(record)
    return matches
=======
def filter_cases_by_outcome(outcome: str) -> list:
    """Queries MongoDB for cases matching a specific risk outcome."""
    collection = get_collection()
    if collection is None:
        return []

    try:
        return list(collection.find({"outcome": outcome}, {"_id": 0}))
    except PyMongoError:
        return []

def save_assessment(record: dict) -> bool:
    """Saves or updates an AI assessment record in the ai_assessments collection.

    Uses client_ref as the foreign key linking to client_cases.
    """
    if not isinstance(record, dict) or "client_ref" not in record:
        return False

    collection = get_assessment_collection()
    if collection is None:
        return False

    try:
        collection.update_one(
            {"client_ref": record["client_ref"]},
            {"$set": record},
            upsert=True
        )
        return True
    except PyMongoError:
        return False


#supporting documents

def upload_supporting_document(file_path: str):
    """
    Uploads a supporting document into MongoDB GridFS.

    Returns the GridFS file ID if successful.
    Returns None if upload fails.
    """

    try:
        collection = get_collection()

        if collection is None:
            return None

        db = collection.database
        fs = GridFS(db)

        with open(file_path, "rb") as file:
            file_id = fs.put(
                file,
                filename=os.path.basename(file_path)
            )

        return str(file_id)

    except Exception as e:
        print(f"Error uploading document: {e}")
        return None

def download_supporting_document(file_id: str, output_path: str) -> bool:
    """
    Downloads a supporting document from MongoDB GridFS.

    file_id:
        GridFS file ID stored in the client record.

    output_path:
        Where the downloaded file should be saved locally.
    """

    try:
        collection = get_collection()

        if collection is None:
            return False

        db = collection.database
        fs = GridFS(db)

        from bson import ObjectId

        gridfs_file = fs.get(ObjectId(file_id))

        with open(output_path, "wb") as file:
            file.write(gridfs_file.read())

        return True

    except Exception as e:
        print(f"Error downloading document: {e}")
        return False



# Test block runs when executing this file directly
if __name__ == "__main__":
    print("Testing MongoDB connection...")
    test_case = {"client_ref": "TEST-002", "outcome": "failed"}
    
    if save_case_record(test_case):
        print("Save successful!")
        print("Records in DB:", load_all_records())
    else:
        print("Could not connect to MongoDB. Make sure MongoDB Server is running!")
>>>>>>> cc91561
