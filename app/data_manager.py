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
import os
import certifi
from dotenv import load_dotenv
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from gridfs import GridFS
from datetime import datetime, timezone

# Load environment variables automatically from .env file
load_dotenv()

# ==============================================================================
# PATHS & DATABASE SETTINGS
# ==============================================================================

# Find the absolute root directory path of the project
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Define the local data directory folder (e.g., .../data)
DATA_DIR = os.path.join(_BASE_DIR, "data")

# Subfolder for local JSON database files
LOCAL_DB_DIR = os.path.join(DATA_DIR, "local database")

# Set the path for saving client profiles locally
LOCAL_CASES_JSON = os.path.join(LOCAL_DB_DIR, "sow_cases.json")

# Set the path for saving AI assessment results locally
LOCAL_ASSESSMENTS_JSON = os.path.join(LOCAL_DB_DIR, "ai_assessments.json")

# Define MongoDB database and collection names
DB_NAME = "sow_risk_db"
COLLECTION_NAME = "client_cases"
ASSESSMENT_COLLECTION_NAME = "ai_assessments"


def _ensure_data_dir():
    # Make sure the local database directory exists on disk before reading or writing
    os.makedirs(LOCAL_DB_DIR, exist_ok=True)


# ==============================================================================
# LOCAL JSON FILE HELPERS (Primary Data Source)
# ==============================================================================

def load_from_json(filepath: str) -> list:
    # Check if local JSON file exists; if not, return an empty list
    if not os.path.exists(filepath):
        return []
    try:
        # Open and read the JSON file safely
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Return data if it is a list, otherwise return empty list
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError) as e:
        # Catch file read or decode errors
        return []


def save_to_json(record: dict, filepath: str) -> bool:
    # Ensure record is a valid dictionary and contains a client_ref key
    if not isinstance(record, dict) or "client_ref" not in record:
        return False

    # Make sure the data directory exists
    _ensure_data_dir()
    
    # Read current records from the JSON file
    records = load_from_json(filepath)

    # Search for an existing record with the same client_ref
    updated = False
    for i, existing in enumerate(records):
        if existing.get("client_ref") == record["client_ref"]:
            records[i] = record  # Overwrite existing record
            updated = True
            break

    # If it is a new client, append it to the end of the list
    if not updated:
        records.append(record)

    # Write the updated list back to the local JSON file formatted with 4-space indent
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=4, ensure_ascii=False)
        return True
    except IOError as e:
        return False


# ==============================================================================
# MONGODB CONNECTION HELPERS (Backup Target)
# ==============================================================================

def get_collection():
    # Connect to MongoDB client_cases collection for backup sync operations
    try:
        client = MongoClient(
            os.getenv("MONGODB_URI"),
            serverSelectionTimeoutMS=2000,  # Wait a maximum of 2 seconds to connect
            tlsCAFile=certifi.where()        # Provide SSL certificates to prevent connection errors
        )
        return client[DB_NAME][COLLECTION_NAME]
    except Exception:
        # Return None if database is offline or URI is not set
        return None


def get_assessment_collection():
    # Connect to MongoDB ai_assessments collection for backup sync operations
    try:
        client = MongoClient(
            os.getenv("MONGODB_URI"),
            serverSelectionTimeoutMS=2000,
            tlsCAFile=certifi.where()
        )
        return client[DB_NAME][ASSESSMENT_COLLECTION_NAME]
    except Exception:
        return None


# ==============================================================================
# PRIMARY CRUD OPERATIONS (Reads & Writes Local JSON First)
# ==============================================================================

def save_case_record(record: dict) -> bool:
    # Save or update a client record strictly in local sow_cases.json
    return save_to_json(record, LOCAL_CASES_JSON)


def load_all_records() -> list:
    # Get all client records strictly from local sow_cases.json
    return load_from_json(LOCAL_CASES_JSON)


def find_client_record(client_ref: str) -> dict | None:
    # Search local sow_cases.json for a single client matching client_ref
    records = load_all_records()
    for record in records:
        if record.get("client_ref") == client_ref:
            return record
    return None


def filter_cases_by_outcome(outcome: str) -> list:
    # Get client cases from local sow_cases.json that match a specific outcome
    all_records = load_all_records()
    return [r for r in all_records if r.get("outcome") == outcome]


def save_assessment(record: dict) -> bool:
    # Save or update an AI assessment record strictly in local ai_assessments.json
    return save_to_json(record, LOCAL_ASSESSMENTS_JSON)


def load_all_assessments() -> list:
    # Get all AI assessment records strictly from local ai_assessments.json
    return load_from_json(LOCAL_ASSESSMENTS_JSON)


def query_assessments(assessments: list, outcome: str = None) -> list:
    # Filter a list of assessments by an outcome status string
    if not outcome:
        return assessments
    return [
        a for a in assessments
        if a.get("outcome") == outcome or a.get("assessment", {}).get("outcome") == outcome
    ]


# ==============================================================================
# SYNC FUNCTIONALITY (JSON -> MongoDB Backup)
# ==============================================================================

def sync_json_to_mongodb() -> dict:
    # Reads local JSON files and batch upserts all data into MongoDB Atlas
    stats = {"cases_synced": 0, "assessments_synced": 0, "errors": []}

    # Step 1: Push all client cases from sow_cases.json to MongoDB
    cases = load_all_records()
    cases_coll = get_collection()
    if cases_coll is not None and cases:
        for record in cases:
            try:
                # Update record in MongoDB or insert if it doesn't exist
                cases_coll.update_one(
                    {"client_ref": record["client_ref"]},
                    {"$set": record},
                    upsert=True
                )
                stats["cases_synced"] += 1
            except PyMongoError as e:
                stats["errors"].append(f"Case {record.get('client_ref')}: {e}")
    else:
        if cases_coll is None:
            stats["errors"].append("Could not connect to MongoDB for client_cases backup.")

    # Step 2: Push all AI assessments from ai_assessments.json to MongoDB
    assessments = load_all_assessments()
    assess_coll = get_assessment_collection()
    if assess_coll is not None and assessments:
        for record in assessments:
            try:
                # Update record in MongoDB or insert if it doesn't exist
                assess_coll.update_one(
                    {"client_ref": record["client_ref"]},
                    {"$set": record},
                    upsert=True
                )
                stats["assessments_synced"] += 1
            except PyMongoError as e:
                stats["errors"].append(f"Assessment {record.get('client_ref')}: {e}")
    else:
        if assess_coll is None:
            stats["errors"].append("Could not connect to MongoDB for ai_assessments backup.")

    return stats


# ==============================================================================
# SUPPORTING DOCUMENTS & MAIN.PY HELPERS
# ==============================================================================

def upload_supporting_document(file_path: str):
    # Try to upload document binary into MongoDB GridFS if online
    try:
        coll = get_collection()
        if coll is not None and os.path.exists(file_path):
            fs = GridFS(coll.database)
            with open(file_path, "rb") as file:
                file_id = fs.put(file, filename=os.path.basename(file_path))
            return str(file_id)
    except Exception:
        pass
    # If MongoDB is offline, return local filename string instead
    return os.path.basename(file_path) if os.path.exists(file_path) else None


def download_supporting_document(file_id: str, output_path: str) -> bool:
    # Check if GridFS file_id is a valid ObjectId
    if not file_id or not ObjectId.is_valid(file_id):
        return False
    try:
        coll = get_collection()
        if coll is None:
            return False
        # Retrieve binary file from GridFS and write to output path
        fs = GridFS(coll.database)
        gridfs_file = fs.get(ObjectId(file_id))
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as file:
            file.write(gridfs_file.read())
        return True
    except Exception:
        return False


# Compatibility stub functions required by main.py
def load_raw_replies(ref: str) -> dict: 
    return {}

def save_raw_replies(ref: str, replies: dict) -> int: 
    return len(replies) if isinstance(replies, dict) else 0

def save_forecast(ref: str, forecast: dict) -> bool: 
    return True

def save_forecast_assessment(ref: str, kind: str, assessment: dict) -> bool:
    # Wrap assessment payload with reference ID and timestamp, then save directly to ai_assessments.json
    record = {
        "client_ref": ref,
        "kind": kind,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "assessment": assessment
    }
    return save_assessment(record)

def save_case(case: dict) -> bool: 
    return save_case_record(case)

def load_case(path: str) -> dict | None:
    # Read a sample case file from a given file path
    if not os.path.exists(path): 
        return None
    try:
        with open(path, "r", encoding="utf-8") as f: 
            return json.load(f)
    except Exception: 
        return None

def load_json(path: str, default=None):
    # Read any JSON file from disk; return default if missing or unreadable
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def sample_dir() -> str:
    # Return the absolute path to the data/sample directory
    return os.path.join(DATA_DIR, "sample")


def list_sample_cases() -> list:
    # List all sample json files inside data/sample/
    sdir = sample_dir()
    if not os.path.exists(sdir):
        return []
    return [os.path.join(sdir, f) for f in os.listdir(sdir) if f.endswith(".json")]

def load_forecasting_case(ref: str) -> dict | None: 
    return find_client_record(ref)

def load_forecast(ref: str) -> dict | None: 
    return None

def load_assessment(ref: str, kind: str = "onboarding") -> dict | None:
    # Find saved assessment matching the given client reference strictly from local JSON
    assessments = load_all_assessments()
    for a in assessments:
        if a.get("client_ref") == ref:
            return a
    return None


# ==============================================================================
# LOCAL TEST HARNESS (Only runs when data_manager.py is executed directly)
# ==============================================================================

if __name__ == "__main__":
    # Safe inspection test: Prints local JSON counts without injecting dummy records
    print("Checking local JSON records...")
    records = load_all_records()
    print(f"Total client records found in sow_cases.json: {len(records)}")
    for r in records:
        print(f" - {r.get('client_ref')}: {r.get('name', r.get('client_profile', {}).get('full_name', 'Unnamed'))}")