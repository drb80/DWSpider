# Query MongoDB for pages containing the keyword "colorado"
from pymongo import MongoClient
import os

# Use environment variable or default
mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(mongo_uri)
db = client["tor_scraper"]  # Change if your DB name is different
collection = db["pages"]    # Change if your collection name is different

# Case-insensitive search for "colorado" in the scraped page data
results = collection.find({"$or": [
    {"html": {"$regex": "colorado", "$options": "i"}},
    {"title": {"$regex": "colorado", "$options": "i"}},
    {"paragraphs": {"$regex": "colorado", "$options": "i"}},
    {"headings": {"$regex": "colorado", "$options": "i"}}
]})

for doc in results:
    print(f"URL: {doc.get('url')}")
    print(f"Title: {doc.get('title')}")
    print(f"Excerpt: {str(doc.get('text', '')[:200])}")
    print("-"*40)
