"""
data_manager.py - the data layer. Everything the system saves goes into one MongoDB
database (MONGODB_URI in .env, database "sow_risk_db"):

    client_cases      one document per client (client_ref)
                        - the client record typed in by the officer (io_manager forms)
                        - forecasting_case: the client in the forecasting format
                        - outcome, routing, next_review_date, last_assessed_on:
                          the latest forecasting decision, so filter_cases_by_outcome works
    ai_assessments    one document per client (client_ref)
                        - crime_scan, benchmark          (crime scan and benchmark results)
                        - forecast                       (the combined AI forecast, Calls 1-4)
                        - forecast_decisions.onboarding  (the onboarding decision)
                        - forecast_decisions.review_<date> (each review decision)
    ai_raw_replies    one document per AI request (client_ref + call): the AI's exact reply,
                      reused on reruns so they are free and give identical answers
    GridFS            uploaded supporting documents

Only the sample client files that ship with the project (data/sample/*.json) are read
from disk; they are input, nothing is written there.

Every function returns False / None / [] / {} if the database can't be reached,
instead of crashing.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import certifi
from gridfs import GridFS
from pymongo import MongoClient, UpdateOne
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

DB_NAME = "sow_risk_db"
COLLECTION_NAME = "client_cases"
ASSESSMENT_COLLECTION_NAME = "ai_assessments"
RAW_REPLY_COLLECTION_NAME = "ai_raw_replies"

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_client: MongoClient | None = None


# ============================================================================
# Connection (shared by every function below)
# ============================================================================

def _database():
    """The sow_risk_db database. One connection is opened and reused. None if it can't be created."""
    global _client
    try:
        if _client is None:
            _client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=2000,
                                  tlsCAFile=certifi.where())
        return _client[DB_NAME]
    except Exception as error:
        logger.error("Could not connect to MongoDB: %s", error)
        return None


def _collection(name: str):
    db = _database()
    return None if db is None else db[name]


def get_collection():
    """Returns the MongoDB collection object or None if connection fails."""
    return _collection(COLLECTION_NAME)


def get_assessment_collection():
    """Returns the ai_assessments collection object or None if connection fails."""
    return _collection(ASSESSMENT_COLLECTION_NAME)


def get_raw_reply_collection():
    """Returns the ai_raw_replies collection object or None if connection fails."""
    return _collection(RAW_REPLY_COLLECTION_NAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storable(value: object) -> object:
    """Make a result safe to store: MongoDB only accepts text keys, so number keys
    become text (exactly what saving to JSON did before)."""
    return json.loads(json.dumps(value, ensure_ascii=False))


def _field_name(value: str) -> str:
    """A safe MongoDB field name (no '.' or '$')."""
    return re.sub(r"[.$]", "_", str(value))


# ============================================================================
# Client records (client_cases)
# ============================================================================

def save_case_record(record: dict) -> bool:
    """Saves or updates a client risk assessment record in MongoDB."""
    if not isinstance(record, dict) or "client_ref" not in record:
        return False

    collection = get_collection()
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


def load_all_records() -> list:
    """Retrieves all case records from MongoDB."""
    collection = get_collection()
    if collection is None:
        return []

    try:
        return list(collection.find({}, {"_id": 0}))
    except PyMongoError:
        return []


def find_client_record(client_ref: str) -> dict | None:
    """One client record by its reference, or None if not found or the database is down."""
    collection = get_collection()
    if collection is None:
        return None
    try:
        return collection.find_one({"client_ref": client_ref}, {"_id": 0})
    except PyMongoError:
        return None


def filter_cases_by_outcome(outcome: str) -> list:
    """Queries MongoDB for cases matching a specific risk outcome."""
    collection = get_collection()
    if collection is None:
        return []

    try:
        return list(collection.find({"outcome": outcome}, {"_id": 0}))
    except PyMongoError:
        return []


def save_case(case: dict) -> bool:
    """Save a client in the forecasting format onto the client's record (client_cases.forecasting_case)."""
    if not isinstance(case, dict) or not case.get("client_ref"):
        return False
    return save_case_record({"client_ref": case["client_ref"], "forecasting_case": _storable(case)})


def load_forecasting_case(client_ref: str) -> dict | None:
    """The client in the forecasting format, if one was saved."""
    record = find_client_record(client_ref)
    case = (record or {}).get("forecasting_case")
    return case if isinstance(case, dict) else None


# ============================================================================
# AI results (ai_assessments): crime scan, benchmark and forecasting together
# ============================================================================

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


def load_ai_assessment(client_ref: str) -> dict | None:
    """Everything the AI produced for one client: crime scan, benchmark, forecast and decisions."""
    collection = get_assessment_collection()
    if collection is None:
        return None
    try:
        return collection.find_one({"client_ref": client_ref}, {"_id": 0})
    except PyMongoError:
        return None


def save_forecast(client_ref: str, forecast: dict) -> bool:
    """Save the combined AI forecast (Calls 1-4) next to the crime scan and benchmark."""
    return save_assessment({"client_ref": client_ref, "forecast": _storable(forecast),
                            "forecast_saved_at": _now()})


def load_forecast(client_ref: str) -> dict | None:
    forecast = (load_ai_assessment(client_ref) or {}).get("forecast")
    return forecast if isinstance(forecast, dict) else None


def save_forecast_assessment(client_ref: str, kind: str, assessment: dict) -> bool:
    """Save a forecasting decision. kind is 'onboarding' or 'review_<date>'.

    The full decision goes into ai_assessments.forecast_decisions.<kind>. The headline
    (outcome, routing, next review date) is also put on the client's record in
    client_cases, so the officer's searches (e.g. filter_cases_by_outcome) see it.
    """
    stored = _storable(assessment)
    saved = save_assessment({"client_ref": client_ref,
                             f"forecast_decisions.{_field_name(kind)}": stored})
    if saved:
        save_case_record({"client_ref": client_ref,
                          "outcome": stored.get("outcome"),
                          "routing": stored.get("routing"),
                          "next_review_date": stored.get("next_review_date"),
                          "last_assessed_on": stored.get("assessed_on") or stored.get("review_date"),
                          "last_assessment_kind": kind})
    return saved


def load_assessment(client_ref: str, kind: str) -> dict | None:
    """One forecasting decision ('onboarding' or 'review_<date>')."""
    decisions = (load_ai_assessment(client_ref) or {}).get("forecast_decisions") or {}
    decision = decisions.get(_field_name(kind))
    return decision if isinstance(decision, dict) else None


def load_all_assessments() -> list[dict]:
    """Every forecasting decision of every client, as {client_ref, kind, assessment}."""
    collection = get_assessment_collection()
    if collection is None:
        return []
    try:
        documents = list(collection.find({"forecast_decisions": {"$exists": True}},
                                         {"_id": 0, "client_ref": 1, "forecast_decisions": 1}))
    except PyMongoError:
        return []
    rows = []
    for document in sorted(documents, key=lambda d: str(d.get("client_ref"))):
        for kind, assessment in sorted((document.get("forecast_decisions") or {}).items()):
            if isinstance(assessment, dict):
                rows.append({"client_ref": document.get("client_ref"), "kind": kind, "assessment": assessment})
    return rows


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


# ============================================================================
# Raw AI replies (ai_raw_replies): the cache that makes reruns free and identical
# ============================================================================

def load_raw_replies(client_ref: str) -> dict:
    """{piece_name: raw_text} for every saved AI reply of this client."""
    collection = get_raw_reply_collection()
    if collection is None:
        return {}
    try:
        entries = collection.find({"client_ref": client_ref}, {"_id": 0, "call": 1, "raw": 1})
        return {entry["call"]: entry["raw"] for entry in entries
                if entry.get("call") and isinstance(entry.get("raw"), str)}
    except Exception as error:  # a broken cache just means asking the AI again
        logger.error("Could not load saved AI replies for %s: %s", client_ref, error)
        return {}


def save_raw_replies(client_ref: str, results: dict) -> int:
    """Save each successful reply that came fresh from the API (in one database call).
    Returns how many were saved."""
    operations = []
    for piece_name, result in results.items():
        if result.get("ok") and not result.get("from_cache") and result.get("raw"):
            entry = {"client_ref": client_ref, "call": piece_name, "raw": result["raw"],
                     "attempts": result.get("attempts"), "prompt_version": result.get("prompt_version"),
                     "saved_at": _now()}
            operations.append(UpdateOne({"client_ref": client_ref, "call": piece_name},
                                        {"$set": entry}, upsert=True))
    if not operations:
        return 0
    collection = get_raw_reply_collection()
    if collection is None:
        return 0
    try:
        collection.bulk_write(operations, ordered=False)
        return len(operations)
    except Exception as error:  # never let a failed save stop the assessment
        logger.error("Could not save AI replies for %s: %s", client_ref, error)
        return 0


# ============================================================================
# Supporting documents (GridFS)
# ============================================================================

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


# ============================================================================
# Sample client files that ship with the project (read only)
# ============================================================================

def sample_dir() -> str:
    return os.path.join(_BASE, "data", "sample")


def safe_name(value: str) -> str:
    """Turn a client ref or piece name into a safe file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "unknown"))


def load_json(path: str, default: object = None) -> object:
    """Load a JSON file. Returns `default` if it's missing or corrupt."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        logger.error("Could not read %s (%s); ignoring it", path, error)
        return default


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


# Test block runs when executing this file directly
if __name__ == "__main__":
    print("Testing MongoDB connection...")
    test_case = {"client_ref": "TEST-002", "outcome": "failed"}

    if save_case_record(test_case):
        print("Save successful!")
        print("Records in DB:", load_all_records())
    else:
        print("Could not connect to MongoDB. Make sure MongoDB Server is running!")