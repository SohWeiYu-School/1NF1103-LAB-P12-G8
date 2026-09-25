import os
from pymongo import MongoClient
from pymongo.errors import PyMongoError

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://sitadriansoh_db_user:XBQDJpd04xLpZpc4@sowcluster0.qpms0w6.mongodb.net/?appName=sowCluster0")
DB_NAME = "sow_risk_db"
COLLECTION_NAME = "client_cases"


def get_collection():
    """Returns the MongoDB collection object or None if connection fails."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        db = client[DB_NAME]
        return db[COLLECTION_NAME]
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


# Test block runs when executing this file directly
if __name__ == "__main__":
    print("Testing MongoDB connection...")
    test_case = {"client_ref": "TEST-002", "outcome": "failed"}
    
    if save_case_record(test_case):
        print("Save successful!")
        print("Records in DB:", load_all_records())
    else:
        print("Could not connect to MongoDB. Make sure MongoDB Server is running!")