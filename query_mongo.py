#!/usr/bin/env python3
"""
Helper script to query and view scraped data from MongoDB
"""

from pymongo import MongoClient
from datetime import datetime
import json

def connect_db(mongo_uri='mongodb://localhost:27017/', db_name='tor_scraper', collection_name='pages'):
    """Connect to MongoDB"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    collection = db[collection_name]
    return client, collection

def list_all_urls(collection):
    """List all scraped URLs"""
    print("\n" + "=" * 60)
    print("All Scraped URLs")
    print("=" * 60)
    
    docs = collection.find({}, {'url': 1, 'title': 1, 'scraped_at': 1, 'html_length': 1})
    
    for i, doc in enumerate(docs, 1):
        print(f"\n{i}. {doc['url']}")
        print(f"   Title: {doc.get('title', 'No title')}")
        print(f"   Size: {doc.get('html_length', 0):,} bytes")
        print(f"   Scraped: {doc.get('scraped_at', 'Unknown')}")

def search_by_keyword(collection, keyword):
    """Search pages by keyword in title or content"""
    print(f"\n" + "=" * 60)
    print(f"Searching for: '{keyword}'")
    print("=" * 60)
    
    # Search in title, headings, and paragraphs
    query = {
        '$or': [
            {'title': {'$regex': keyword, '$options': 'i'}},
            {'headings': {'$regex': keyword, '$options': 'i'}},
            {'paragraphs': {'$regex': keyword, '$options': 'i'}}
        ]
    }
    
    docs = collection.find(query)
    count = 0
    
    for doc in docs:
        count += 1
        print(f"\n{count}. {doc['url']}")
        print(f"   Title: {doc.get('title', 'No title')}")

    if count == 0:
        print("No results found")
    else:
        print(f"\nFound {count} pages")

def get_page_html(collection, url):
    """Get the full HTML of a specific page"""
    doc = collection.find_one({'url': url})
    
    if doc:
        print(f"\n" + "=" * 60)
        print(f"HTML for: {url}")
        print("=" * 60)
        print(doc.get('html', 'No HTML stored'))
        return doc.get('html')
    else:
        print(f"No page found with URL: {url}")
        return None

def get_stats(collection):
    """Get database statistics"""
    print("\n" + "=" * 60)
    print("Database Statistics")
    print("=" * 60)
    
    total = collection.count_documents({})
    print(f"\nTotal pages: {total}")
    
    # Pages by depth
    pipeline = [
        {'$group': {'_id': '$depth', 'count': {'$sum': 1}}},
        {'$sort': {'_id': 1}}
    ]
    
    print("\nPages by depth:")
    for result in collection.aggregate(pipeline):
        print(f"  Depth {result['_id']}: {result['count']} pages")
    
    # Average page size
    pipeline = [
        {
            '$group': {
                '_id': None,
                'avg_size': {'$avg': '$html_length'},
                'total_size': {'$sum': '$html_length'}
            }
        }
    ]
    
    stats = list(collection.aggregate(pipeline))
    if stats:
        print(f"\nAverage page size: {stats[0]['avg_size']:,.0f} bytes")
        print(f"Total storage: {stats[0]['total_size']:,.0f} bytes ({stats[0]['total_size'] / (1024*1024):.2f} MB)")
    
    # Most recent scrapes
    print("\nMost recent scrapes:")
    docs = collection.find({}, {'url': 1, 'scraped_at': 1}).sort('scraped_at', -1).limit(5)
    for doc in docs:
        print(f"  {doc['scraped_at']}: {doc['url']}")

def export_page_to_file(collection, url, filename):
    """Export a page's HTML to a file"""
    doc = collection.find_one({'url': url})
    
    if doc:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(doc.get('html', ''))
        print(f"✓ Exported HTML to {filename}")
    else:
        print(f"✗ No page found with URL: {url}")

def delete_all(collection):
    """Delete all documents (use with caution!)"""
    response = input("Are you sure you want to delete ALL documents? (yes/no): ")
    if response.lower() == 'yes':
        result = collection.delete_many({})
        print(f"✓ Deleted {result.deleted_count} documents")
    else:
        print("Cancelled")

def main():
    """Interactive menu"""
    client, collection = connect_db()
    
    while True:
        print("\n" + "=" * 60)
        print("MongoDB Tor Scraper - Query Tool")
        print("=" * 60)
        print("1. List all URLs")
        print("2. Search by keyword")
        print("3. Get page HTML")
        print("4. Show statistics")
        print("5. Export page to file")
        print("6. Delete all documents")
        print("0. Exit")
        print("=" * 60)
        
        choice = input("\nEnter choice: ").strip()
        
        if choice == '1':
            list_all_urls(collection)
        elif choice == '2':
            keyword = input("Enter keyword to search: ").strip()
            search_by_keyword(collection, keyword)
        elif choice == '3':
            url = input("Enter URL: ").strip()
            get_page_html(collection, url)
        elif choice == '4':
            get_stats(collection)
        elif choice == '5':
            url = input("Enter URL: ").strip()
            filename = input("Enter filename (e.g., page.html): ").strip()
            export_page_to_file(collection, url, filename)
        elif choice == '6':
            delete_all(collection)
        elif choice == '0':
            break
        else:
            print("Invalid choice")
    
    client.close()
    print("\n✓ Goodbye!")

if __name__ == '__main__':
    main()
