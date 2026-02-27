#!/usr/bin/env python3
"""
Single‑threaded Tor web scraper that saves complete HTML pages to MongoDB.
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
import logging

# Set up logging – no threading any more so we don't print thread names.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)


class TorScraperMongo:
    def __init__(self, mongo_uri='mongodb://localhost:27017/',
                 db_name='tor_scraper',
                 collection_name='pages',
                 max_depth=5,
                 delay=3):
        """
        Initialise the scraper with a MongoDB connection.

        Args:
            mongo_uri: MongoDB connection string
            db_name: database name
            collection_name: collection for pages
            max_depth: crawl depth limit
            delay: delay between requests
        """
        try:
            self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            logging.info(f"Connected to MongoDB at {mongo_uri}")
        except ConnectionFailure as e:
            logging.error(f"Failed to connect to MongoDB: {e}")
            raise

        self.db = self.client[db_name]
        self.collection = self.db[collection_name]
        self.collection.create_index('url', unique=True)
        logging.info(f"Using database: {db_name}, collection: {collection_name}")

        self.max_depth = max_depth
        self.delay = delay

        # simple, non‑threaded state
        self.visited = set()
        self.pages_saved = 0

        # constant value stored in each document
        self.thread_name = 'main'

    def get_session(self):
        """Return a requests session configured to use the local Tor proxy."""
        session = requests.Session()
        session.proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:109.0) '
                          'Gecko/20100101 Firefox/115.0'
        })
        return session

    def is_valid_url(self, url):
        """Return True if the URL is http/https and not an image."""
        if not url:
            return False

        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp',
                            '.svg', '.webp', '.ico']
        if any(url.lower().endswith(ext) for ext in image_extensions):
            return False

        parsed = urlparse(url)
        return parsed.scheme in ['http', 'https']

    def mark_visited(self, url):
        """Record that we have visited a URL.

        Returns False if it was already seen.
        """
        if url in self.visited:
            return False
        self.visited.add(url)
        return True

    def increment_pages_saved(self):
        self.pages_saved += 1

    def scrape_page(self, url, depth=0, parent_url=None, session=None):
        """Scrape a single page and save to MongoDB."""
        if depth > self.max_depth:
            return

        if not self.mark_visited(url):
            logging.debug(f"Already visited: {url}")
            return

        if session is None:
            session = self.get_session()

        logging.info(f"[Depth {depth}] Scraping: {url}")
        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
            html_content = response.text
            soup = BeautifulSoup(html_content, 'html.parser')

            page_data = {
                'url': url,
                'parent_url': parent_url,
                'html': html_content,
                'html_length': len(html_content),
                'title': soup.title.string if soup.title else None,
                'headings': [h.get_text(strip=True)
                             for h in soup.find_all(['h1', 'h2', 'h3'])],
                'paragraphs': [p.get_text(strip=True)
                               for p in soup.find_all('p')][:10],
                'meta_description': None,
                'depth': depth,
                'status_code': response.status_code,
                'scraped_at': datetime.utcnow(),
                'content_type': response.headers.get('Content-Type',
                                                    'unknown'),
                'thread_name': self.thread_name,
            }

            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                page_data['meta_description'] = meta_desc['content']

            links = []
            for link in soup.find_all('a', href=True):
                absolute_url = urljoin(url, link['href'])
                if self.is_valid_url(absolute_url):
                    links.append(absolute_url)

            page_data['links'] = links
            page_data['links_count'] = len(links)

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

            if depth < self.max_depth:
                for link_url in links[:5]:
                    time.sleep(self.delay + random.uniform(0, 2))
                    self.scrape_page(link_url, depth + 1,
                                     parent_url=url, session=session)

        except requests.exceptions.Timeout:
            logging.error(f"Timeout on {url}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error scraping {url}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error on {url}: {e}")

    def scrape(self, start_urls):
        """Sequentially scrape each start URL."""
        logging.info("\nStarting Tor scraper with MongoDB storage...")
        logging.info(f"Max depth: {self.max_depth}")
        logging.info(f"Delay: {self.delay}s")
        logging.info(f"Domains to scrape: {len(start_urls)}")
        logging.info("-" * 60)

        session = self.get_session()
        for url in start_urls:
            logging.info(f"Starting domain: {url}")
            self.scrape_page(url, depth=0, session=session)
            logging.info(f"Completed domain: {url}")

        logging.info("=" * 60)
        logging.info("Scraping complete!")
        logging.info(f"Pages saved to MongoDB: {self.pages_saved}")
        logging.info(f"Total pages visited: {len(self.visited)}")
        logging.info("=" * 60)

    def get_stats(self):
        """Print some statistics from the MongoDB collection."""
        total_docs = self.collection.count_documents({})
        logging.info(f"\nDatabase Statistics:")
        logging.info(f"  Total documents: {total_docs}")

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

        pipeline = [
            {'$group': {'_id': '$thread_name', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]
        logging.info("\n  Pages scraped by thread:")
        for result in self.collection.aggregate(pipeline):
            logging.info(f"    {result['_id']}: {result['count']} pages")

    def close(self):
        """Close MongoDB connection."""
        self.client.close()
        logging.info("\n✓ MongoDB connection closed")


def load_urls_from_file(filename):
    """
    Load URLs from a text file (one URL per line)

    Args:
        filename: Path to file containing URLs

    Returns:
        List of URLs
    """
    urls = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    urls.append(line)
        logging.info(f"✓ Loaded {len(urls)} URLs from {filename}")
        return urls
    except FileNotFoundError:
        logging.error(f"✗ File not found: {filename}")
        return []
    except Exception as e:
        logging.error(f"✗ Error reading file {filename}: {e}")
        return []


def test_tor_connection():
    """Test if Tor is working."""
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
    """Test if MongoDB is accessible."""
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

    # URL source – choose one method
    URLS_FILE = 'urls.txt'  # one URL per line
    start_urls = load_urls_from_file(URLS_FILE)

    # METHOD 2: manual list (uncomment and comment out METHOD 1)
    # start_urls = [
    #     'http://yvudsnnux372gj2nvg3bnkficwf4niel6drfqyhbtglgdsf2l75xfqqd.onion/',
    #     'http://another-onion-site.onion/',
    #     'http://yet-another-site.onion/',
    # ]

    if not start_urls:
        logging.error("No URLs to scrape. Please check your urls.txt file or use manual list.")
        exit(1)

    if not test_tor_connection():
        print("Please make sure Tor is running on port 9050")
        print("Start it with: sudo systemctl start tor")
        exit(1)

    if not test_mongo_connection(MONGO_URI):
        print("Please make sure MongoDB is running")
        print("Start it with: sudo systemctl start mongod")
        exit(1)

    scraper = TorScraperMongo(
        mongo_uri=MONGO_URI,
        db_name=DB_NAME,
        collection_name=COLLECTION_NAME,
        max_depth=5,      # keep it shallow for testing
        delay=0            # 0–3 second delay between requests
    )

    try:
        scraper.scrape(start_urls)
        scraper.get_stats()
    finally:
        scraper.close()

    print("\nDone!")