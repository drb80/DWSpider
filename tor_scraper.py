#!/usr/bin/env python3
"""
Tor web scraper that saves complete HTML pages to MongoDB
"""

import requests
import json
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import random
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, DuplicateKeyError

class TorScraperMongo:
    def __init__(self, mongo_uri='mongodb://localhost:27017/', 
                 db_name='tor_scraper', 
                 collection_name='pages',
                 max_depth=2, 
                 delay=3):
        """
        Initialize the scraper with MongoDB connection
        
        Args:
            mongo_uri: MongoDB connection string
            db_name: Database name
            collection_name: Collection name for storing pages
            max_depth: Maximum crawl depth
            delay: Delay between requests in seconds
        """
        # Set up MongoDB connection
        try:
            self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            # Test connection
            self.client.admin.command('ping')
            print(f"✓ Connected to MongoDB at {mongo_uri}")
        except ConnectionFailure as e:
            print(f"✗ Failed to connect to MongoDB: {e}")
            raise
        
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]
        
        # Create index on URL to prevent duplicates
        self.collection.create_index('url', unique=True)
        print(f"✓ Using database: {db_name}, collection: {collection_name}")
        
        # Set up requests session with Tor
        self.session = requests.Session()
        self.session.proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0'
        })
        
        self.max_depth = max_depth
        self.delay = delay
        self.visited = set()
        self.pages_saved = 0
        
    def is_valid_url(self, url):
        """Check if URL is valid and not an image"""
        if not url:
            return False
        
        # Skip images
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico']
        if any(url.lower().endswith(ext) for ext in image_extensions):
            return False
            
        # Must be http or https
        parsed = urlparse(url)
        return parsed.scheme in ['http', 'https']
    
    def scrape_page(self, url, depth=0, parent_url=None):
        """Scrape a single page and save to MongoDB"""
        
        # Skip if already visited or too deep
        if url in self.visited or depth > self.max_depth:
            return
        
        self.visited.add(url)
        
        print(f"[Depth {depth}] Scraping: {url}")
        
        try:
            # Make request through Tor
            response = self.session.get(url, timeout=60)
            response.raise_for_status()
            
            # Get the raw HTML
            html_content = response.text
            
            # Parse HTML for metadata
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract metadata
            page_data = {
                'url': url,
                'parent_url': parent_url,
                'html': html_content,  # Store complete HTML
                'html_length': len(html_content),
                'title': soup.title.string if soup.title else None,
                'meta_description': None,
                'depth': depth,
                'status_code': response.status_code,
                'scraped_at': datetime.utcnow(),
                'content_type': response.headers.get('Content-Type', 'unknown')
            }
            
            # Extract meta description if available
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                page_data['meta_description'] = meta_desc['content']
            
            # Extract all links
            links = []
            for link in soup.find_all('a', href=True):
                absolute_url = urljoin(url, link['href'])
                if self.is_valid_url(absolute_url):
                    links.append(absolute_url)
            
            page_data['links'] = links
            page_data['links_count'] = len(links)
            
            # Save to MongoDB
            try:
                result = self.collection.insert_one(page_data)
                self.pages_saved += 1
                print(f"  ✓ Saved to MongoDB (ID: {result.inserted_id})")
                print(f"    - Title: {page_data['title']}")
                print(f"    - HTML size: {len(html_content):,} bytes")
                print(f"    - Found {len(links)} links")
            except DuplicateKeyError:
                print(f"  ⚠ Already in database, skipping")
                return  # Don't follow links if we've already scraped this page
            
            # Follow links if not at max depth
            if depth < self.max_depth:
                for link_url in links:
                    if link_url not in self.visited:
                        # Random delay to be polite
                        time.sleep(self.delay + random.uniform(0, 2))
                        self.scrape_page(link_url, depth + 1, parent_url=url)
            
        except requests.exceptions.Timeout:
            print(f"  ✗ Timeout on {url}")
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Error scraping {url}: {e}")
        except Exception as e:
            print(f"  ✗ Unexpected error on {url}: {e}")
    
    def scrape(self, start_urls):
        """Start scraping from a list of URLs"""
        print("\nStarting Tor scraper with MongoDB storage...")
        print(f"Max depth: {self.max_depth}")
        print(f"Delay: {self.delay}s")
        print("-" * 60)
        
        for url in start_urls:
            self.scrape_page(url, depth=0)
        
        print(f"\n{'=' * 60}")
        print(f"Scraping complete!")
        print(f"Pages saved to MongoDB: {self.pages_saved}")
        print(f"Total pages visited: {len(self.visited)}")
        print(f"{'=' * 60}")
    
    def get_stats(self):
        """Get statistics from the database"""
        total_docs = self.collection.count_documents({})
        print(f"\nDatabase Statistics:")
        print(f"  Total documents: {total_docs}")
        
        # Get size distribution
        pipeline = [
            {
                '$group': {
                    '_id': None,
                    'avg_size': {'$avg': '$html_length'},
                    'max_size': {'$max': '$html_length'},
                    'min_size': {'$min': '$html_length'}
                }
            }
        ]
        
        stats = list(self.collection.aggregate(pipeline))
        if stats:
            print(f"  Average HTML size: {stats[0]['avg_size']:,.0f} bytes")
            print(f"  Largest page: {stats[0]['max_size']:,} bytes")
            print(f"  Smallest page: {stats[0]['min_size']:,} bytes")
    
    def export_to_json(self, filename='mongo_export.json', limit=None):
        """Export MongoDB data to JSON file"""
        query = {}
        cursor = self.collection.find(query).limit(limit) if limit else self.collection.find(query)
        
        documents = []
        for doc in cursor:
            # Convert ObjectId to string for JSON serialization
            doc['_id'] = str(doc['_id'])
            doc['scraped_at'] = doc['scraped_at'].isoformat()
            documents.append(doc)
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(documents, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Exported {len(documents)} documents to {filename}")
    
    def close(self):
        """Close MongoDB connection"""
        self.client.close()
        print("\n✓ MongoDB connection closed")


def test_tor_connection():
    """Test if Tor is working"""
    print("Testing Tor connection...")
    try:
        session = requests.Session()
        session.proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        response = session.get('https://check.torproject.org', timeout=30)
        if 'Congratulations' in response.text:
            print("✓ Tor is working!\n")
            return True
        else:
            print("✗ Connected but not using Tor\n")
            return False
    except Exception as e:
        print(f"✗ Tor connection failed: {e}\n")
        return False


def test_mongo_connection(mongo_uri='mongodb://localhost:27017/'):
    """Test if MongoDB is accessible"""
    print("Testing MongoDB connection...")
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        print(f"✓ MongoDB is accessible at {mongo_uri}\n")
        client.close()
        return True
    except Exception as e:
        print(f"✗ MongoDB connection failed: {e}\n")
        return False


if __name__ == '__main__':
    # Configuration
    MONGO_URI = 'mongodb://localhost:27017/'
    DB_NAME = 'tor_scraper'
    COLLECTION_NAME = 'pages'
    
    # Test connections first
    if not test_tor_connection():
        print("Please make sure Tor is running on port 9050")
        print("Start it with: sudo systemctl start tor")
        exit(1)
    
    if not test_mongo_connection(MONGO_URI):
        print("Please make sure MongoDB is running")
        print("Start it with: sudo systemctl start mongod")
        exit(1)
    
    # Create scraper
    scraper = TorScraperMongo(
        mongo_uri=MONGO_URI,
        db_name=DB_NAME,
        collection_name=COLLECTION_NAME,
        max_depth=1,  # Keep it shallow for testing
        delay=3       # 3 second delay between requests
    )
    
    # Add your .onion URLs here
    start_urls = [
        'https://ahmia.fyi/address/'
    ]
    
    try:
        # Scrape
        scraper.scrape(start_urls)
        
        # Show stats
        scraper.get_stats()
        
        # Optional: Export to JSON as backup
        # scraper.export_to_json('backup.json', limit=10)
        
    finally:
        # Close connection
        scraper.close()
    
    print("\nDone!")
