import os
import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from gridfs import GridFS

DB_NAME = "sow_risk_db"
COLLECTION_NAME = "client_cases"
ASSESSMENT_COLLECTION_NAME = "ai_assessments"


def get_collection():
    """Returns the MongoDB collection object or None if connection fails."""
    try:
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