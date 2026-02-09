#!/usr/bin/env python3
"""
Multi-threaded Tor web scraper that saves complete HTML pages to MongoDB
Each domain gets its own thread for parallel scraping
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
import threading
from queue import Queue
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(threadName)-10s] %(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

class ThreadedTorScraperMongo:
    def __init__(self, mongo_uri='mongodb://localhost:27017/', 
                 db_name='tor_scraper', 
                 collection_name='pages',
                 max_depth=2, 
                 delay=3,
                 num_threads=5):
        """
        Initialize the threaded scraper with MongoDB connection
        
        Args:
            mongo_uri: MongoDB connection string
            db_name: Database name
            collection_name: Collection name for storing pages
            max_depth: Maximum crawl depth
            delay: Delay between requests in seconds
            num_threads: Maximum number of concurrent threads (domains)
        """
        # Set up MongoDB connection
        try:
            self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            # Test connection
            self.client.admin.command('ping')
            logging.info(f"Connected to MongoDB at {mongo_uri}")
        except ConnectionFailure as e:
            logging.error(f"Failed to connect to MongoDB: {e}")
            raise
        
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]
        
        # Create index on URL to prevent duplicates
        self.collection.create_index('url', unique=True)
        logging.info(f"Using database: {db_name}, collection: {collection_name}")
        
        self.max_depth = max_depth
        self.delay = delay
        self.num_threads = num_threads
        
        # Thread-safe data structures
        self.visited_lock = threading.Lock()
        self.visited = set()
        self.pages_saved = 0
        self.pages_saved_lock = threading.Lock()
        
        # Queue for domains to scrape
        self.domain_queue = Queue()
        
    def get_session(self):
        """Create a new requests session for each thread (thread-safe)"""
        session = requests.Session()
        session.proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0'
        })
        return session
    
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
    
    def mark_visited(self, url):
        """Thread-safe method to mark URL as visited"""
        with self.visited_lock:
            if url in self.visited:
                return False
            self.visited.add(url)
            return True
    
    def increment_pages_saved(self):
        """Thread-safe counter increment"""
        with self.pages_saved_lock:
            self.pages_saved += 1
    
    def scrape_page(self, url, depth=0, parent_url=None, session=None):
        """Scrape a single page and save to MongoDB"""
        
        # Skip if too deep
        if depth > self.max_depth:
            return
        
        # Thread-safe visited check
        if not self.mark_visited(url):
            logging.debug(f"Already visited: {url}")
            return
        
        # Use provided session or create new one
        if session is None:
            session = self.get_session()
        
        logging.info(f"[Depth {depth}] Scraping: {url}")
        
        try:
            # Make request through Tor
            response = session.get(url, timeout=60)
            response.raise_for_status()
            
            # Get the raw HTML
            html_content = response.text
            
            # Parse HTML for metadata
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract metadata
            page_data = {
                'url': url,
                'parent_url': parent_url,
                'html': html_content,
                'html_length': len(html_content),
                'title': soup.title.string if soup.title else None,
                'headings': [h.get_text(strip=True) for h in soup.find_all(['h1', 'h2', 'h3'])],
                'paragraphs': [p.get_text(strip=True) for p in soup.find_all('p')][:10],
                'meta_description': None,
                'depth': depth,
                'status_code': response.status_code,
                'scraped_at': datetime.utcnow(),
                'content_type': response.headers.get('Content-Type', 'unknown'),
                'thread_name': threading.current_thread().name
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
                self.increment_pages_saved()
                logging.info(f"✓ Saved to MongoDB (ID: {result.inserted_id})")
                logging.info(f"  Title: {page_data['title']}")
                logging.info(f"  HTML size: {len(html_content):,} bytes")
                logging.info(f"  Found {len(links)} links")
            except DuplicateKeyError:
                logging.warning(f"Already in database: {url}")
                return
            
            # Follow links if not at max depth
            if depth < self.max_depth:
                for link_url in links[:5]:  # Limit to 5 links per page
                    # Random delay to be polite
                    time.sleep(self.delay + random.uniform(0, 2))
                    self.scrape_page(link_url, depth + 1, parent_url=url, session=session)
            
        except requests.exceptions.Timeout:
            logging.error(f"Timeout on {url}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error scraping {url}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error on {url}: {e}")
    
    def domain_worker(self):
        """Worker thread that processes domains from the queue"""
        # Each thread gets its own session
        session = self.get_session()
        
        while True:
            try:
                # Get domain from queue (with timeout to allow checking if done)
                url = self.domain_queue.get(timeout=1)
                
                if url is None:  # Poison pill to stop thread
                    self.domain_queue.task_done()
                    break
                
                logging.info(f"Starting domain: {url}")
                
                # Scrape this domain
                self.scrape_page(url, depth=0, session=session)
                
                logging.info(f"Completed domain: {url}")
                
                # Mark task as done
                self.domain_queue.task_done()
                
            except Exception as e:
                # Queue.get() timeout - check if there's more work
                continue
    
    def scrape(self, start_urls):
        """Start scraping from a list of URLs using multiple threads"""
        logging.info("\nStarting multi-threaded Tor scraper with MongoDB storage...")
        logging.info(f"Max depth: {self.max_depth}")
        logging.info(f"Delay: {self.delay}s")
        logging.info(f"Number of threads: {self.num_threads}")
        logging.info(f"Domains to scrape: {len(start_urls)}")
        logging.info("-" * 60)
        
        # Add all domains to queue
        for url in start_urls:
            self.domain_queue.put(url)
        
        # Create and start worker threads
        threads = []
        for i in range(min(self.num_threads, len(start_urls))):
            t = threading.Thread(
                target=self.domain_worker,
                name=f'Worker-{i+1}'
            )
            t.start()
            threads.append(t)
            logging.info(f"Started thread: {t.name}")
        
        # Wait for all tasks to complete
        self.domain_queue.join()
        
        # Stop workers by sending poison pills
        for _ in threads:
            self.domain_queue.put(None)
        
        # Wait for all threads to finish
        for t in threads:
            t.join()
        
        logging.info("=" * 60)
        logging.info("Scraping complete!")
        logging.info(f"Pages saved to MongoDB: {self.pages_saved}")
        logging.info(f"Total pages visited: {len(self.visited)}")
        logging.info("=" * 60)
    
    def get_stats(self):
        """Get statistics from the database"""
        total_docs = self.collection.count_documents({})
        logging.info(f"\nDatabase Statistics:")
        logging.info(f"  Total documents: {total_docs}")
        
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
            logging.info(f"  Average HTML size: {stats[0]['avg_size']:,.0f} bytes")
            logging.info(f"  Largest page: {stats[0]['max_size']:,} bytes")
            logging.info(f"  Smallest page: {stats[0]['min_size']:,} bytes")
        
        # Pages by thread
        pipeline = [
            {'$group': {'_id': '$thread_name', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]
        
        logging.info("\n  Pages scraped by thread:")
        for result in self.collection.aggregate(pipeline):
            logging.info(f"    {result['_id']}: {result['count']} pages")
    
    def close(self):
        """Close MongoDB connection"""
        self.client.close()
        logging.info("\n✓ MongoDB connection closed")


def test_tor_connection():
    """Test if Tor is working"""
    logging.info("Testing Tor connection...")
    try:
        session = requests.Session()
        session.proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        response = session.get('https://check.torproject.org', timeout=30)
        if 'Congratulations' in response.text:
            logging.info("✓ Tor is working!\n")
            return True
        else:
            logging.warning("✗ Connected but not using Tor\n")
            return False
    except Exception as e:
        logging.error(f"✗ Tor connection failed: {e}\n")
        return False


def test_mongo_connection(mongo_uri='mongodb://localhost:27017/'):
    """Test if MongoDB is accessible"""
    logging.info("Testing MongoDB connection...")
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        logging.info(f"✓ MongoDB is accessible at {mongo_uri}\n")
        client.close()
        return True
    except Exception as e:
        logging.error(f"✗ MongoDB connection failed: {e}\n")
        return False


if __name__ == '__main__':
    # Configuration
    MONGO_URI = 'mongodb://localhost:27017/'
    DB_NAME = 'tor_scraper'
    COLLECTION_NAME = 'pages'
    NUM_THREADS = 3  # Number of concurrent threads (one per domain)
    
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
    scraper = ThreadedTorScraperMongo(
        mongo_uri=MONGO_URI,
        db_name=DB_NAME,
        collection_name=COLLECTION_NAME,
        max_depth=1,      # Keep it shallow for testing
        delay=3,          # 3 second delay between requests
        num_threads=NUM_THREADS  # Number of concurrent domain threads
    )
    
    # Add your .onion URLs here - each will be scraped in its own thread
    start_urls = [
        'http://yvudsnnux372gj2nvg3bnkficwf4niel6drfqyhbtglgdsf2l75xfqqd.onion/',
        # Add more .onion URLs here - each gets its own thread
        # 'http://another-onion-site.onion/',
        # 'http://yet-another-site.onion/',
    ]
    
    try:
        # Scrape
        scraper.scrape(start_urls)
        
        # Show stats
        scraper.get_stats()
        
    finally:
        # Close connection
        scraper.close()
    
    print("\nDone!")
