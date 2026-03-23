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
from datetime import datetime, timezone
import re
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, DuplicateKeyError
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

# Set up logging – no threading any more so we don't print thread names.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s',
    datefmt='%H:%M:%S'
)


class TorScraperMongo:
    ONION_CANDIDATE_RE = re.compile(
        r'(?i)(?:https?://)?([a-z2-7]{16}|[a-z2-7]{56})\.onion(?:/[^\s"\'<>)]*)?'
    )

    def __init__(self, mongo_uri='mongodb://localhost:27017/',
                 db_name='tor_scraper',
                 collection_name='pages',
                 max_depth=25,
                 delay=3,
                 max_workers=10,
                 verify_ssl=True):
        """
        Initialise the scraper with a MongoDB connection.

        Args:
            mongo_uri: MongoDB connection string
            db_name: database name
            collection_name: collection for pages
            max_depth: crawl depth limit
            delay: delay between requests
            max_workers: number of threads to use for scraping
            verify_ssl: whether to verify HTTPS certificates (set to False to
                permit self-signed certificates)
            request_timeout: request timeout in seconds
            max_retries: retries for timeouts / transient request failures
        """
        try:
            self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            logging.info(f"Connected to MongoDB at {mongo_uri}")
        except ConnectionFailure as e:
            logging.error(f"Failed to connect to MongoDB: {e}")
            raise
        
        self.verify_ssl = verify_ssl

        self.db = self.client[db_name]
        self.collection = self.db[collection_name]
        self.collection.create_index('url', unique=True)
        logging.info(f"Using database: {db_name}, collection: {collection_name}")

        self.max_depth = max_depth
        self.delay = delay
        self.max_workers = max_workers
        self.request_timeout = request_timeout
        self.max_retries = max_retries

        # simple, threaded state
        self.visited = set()
        self.pages_saved = 0
        self.pending_tasks = 0
        self.lock = threading.Lock()

    def _increment_pending(self, amount: int = 1):
        with self.lock:
            self.pending_tasks += amount

    def _decrement_pending(self, amount: int = 1):
        with self.lock:
            self.pending_tasks = max(0, self.pending_tasks - amount)

    def _on_task_done(self, future):
        """Handle task completion and keep pending counter balanced."""
        try:
            # Surface unexpected task failures in logs.
            future.result()
        except Exception as e:
            logging.error(f"Task failed: {e}")
        finally:
            self._decrement_pending()

    def _submit_task(self, executor, url, depth, parent_url):
        """Submit a scrape task in a race-safe way for pending tracking."""
        self._increment_pending()
        try:
            future = executor.submit(self.scrape_page,
                                     url,
                                     depth,
                                     parent_url,
                                     None,
                                     executor)
        except Exception:
            # Keep counter correct if submission itself fails.
            self._decrement_pending()
            raise

        future.add_done_callback(self._on_task_done)
        return future

    def get_pending_count(self):
        with self.lock:
            return self.pending_tasks

    def get_session(self):
        """Return a requests session configured to use the local Tor proxy.

        The returned session honours :attr:`verify_ssl`; if that flag is False
        the session will not validate HTTPS certificates (useful for testing
        against servers with self-signed certs).
        """
        session = requests.Session()
        session.proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:109.0) '
                          'Gecko/20100101 Firefox/115.0'
        })
        # control certificate validation
        session.verify = self.verify_ssl
        return session

    def is_onion_url(self, url):
        """Check if URL is an onion site (.onion domain)."""
        parsed = urlparse(url)
        return parsed.netloc.endswith('.onion')

    def is_valid_url(self, url):
        """Return True if the URL is http/https, not an image or other binary file,
        not fragment-only, and is an onion site.

        We also exclude a handful of common download extensions such as DMG and MSI
        so that the scraper stays focussed on HTML pages. The response content-type
        is checked later in ``scrape_page`` to avoid saving non-HTML responses.
        """
        if not url:
            return False

        # Filter out fragment-only URLs (e.g., #section, #top)
        if url.startswith('#'):
            return False

        # extensions that are clearly not HTML pages
        non_html_exts = [
            '.jpg', '.jpeg', '.png', '.gif', '.bmp',
            '.svg', '.webp', '.ico',
            '.dmg', '.msi', '.exe', '.zip', '.tar', '.gz',
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'
        ]
        if any(url.lower().endswith(ext) for ext in non_html_exts):
            return False

        parsed = urlparse(url)
        if parsed.scheme not in ['http', 'https']:
            return False
        
        # Only allow .onion sites
        return self.is_onion_url(url)
    
    def normalize_url(self, url):
        """Remove fragments from URLs to avoid duplicates like url/ and url/#main."""
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        # Remove default ports to reduce duplicates.
        if (scheme == 'http' and netloc.endswith(':80')) or (
            scheme == 'https' and netloc.endswith(':443')
        ):
            netloc = netloc.rsplit(':', 1)[0]
        # Reconstruct URL without fragment
        return f"{scheme}://{netloc}{parsed.path}{'?' + parsed.query if parsed.query else ''}"

    def extract_onion_links(self, page_url, html_content, soup):
        """Extract onion links from anchors and raw HTML text.

        Some onion pages hide links in script/text blobs rather than anchor tags,
        so we also scan raw HTML for onion URL patterns.
        """
        links = []
        seen_normalized = set()

        for link in soup.find_all('a', href=True):
            absolute_url = urljoin(page_url, link['href'])
            if self.is_valid_url(absolute_url):
                normalized = self.normalize_url(absolute_url)
                if normalized not in seen_normalized:
                    links.append(normalized)
                    seen_normalized.add(normalized)

        for match in self.ONION_CANDIDATE_RE.finditer(html_content):
            candidate = match.group(0)
            if not candidate.lower().startswith(('http://', 'https://')):
                candidate = f"http://{candidate}"
            absolute_url = urljoin(page_url, candidate)
            if self.is_valid_url(absolute_url):
                normalized = self.normalize_url(absolute_url)
                if normalized not in seen_normalized:
                    links.append(normalized)
                    seen_normalized.add(normalized)

        return links

    def fetch_with_retries(self, session, url):
        """Fetch a URL with retry/backoff for transient Tor failures."""
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return response
            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < self.max_retries:
                    backoff = 1.5 ** attempt + random.uniform(0, 1)
                    logging.debug(f"Timeout on {url}; retrying in {backoff:.1f}s")
                    time.sleep(backoff)
                    continue
                raise
            except requests.exceptions.RequestException as e:
                last_error = e
                # Retry select transient network conditions.
                retriable = any(marker in str(e).lower() for marker in [
                    'connection reset', 'temporarily unavailable',
                    'connection aborted', 'remote end closed connection'
                ])
                if retriable and attempt < self.max_retries:
                    backoff = 1.5 ** attempt + random.uniform(0, 1)
                    logging.debug(f"Transient error on {url}; retrying in {backoff:.1f}s")
                    time.sleep(backoff)
                    continue
                raise
        if last_error:
            raise last_error

    def mark_visited(self, url):
        """Record that we have visited a URL.

        Returns False if it was already seen.
        """
        with self.lock:
            if url in self.visited:
                return False
            self.visited.add(url)
            return True

    def increment_pages_saved(self):
        with self.lock:
            self.pages_saved += 1

    def scrape_page(self, url, depth=0, parent_url=None, session=None, executor=None):
        """Scrape a single page and save to MongoDB."""
        if depth > self.max_depth:
            return

        if not self.mark_visited(url):
            logging.debug(f"Already visited: {url}")
            return

        # Respect a per-request delay to avoid hammering Tor endpoints
        if self.delay and depth > 0:
            time.sleep(self.delay + random.uniform(0, 2))

        if session is None:
            session = self.get_session()

        thread_name = threading.current_thread().name

        logging.info(f"[Depth {depth}] Scraping: {url}")
        try:
            response = self.fetch_with_retries(session, url)

            # Process HTML and also content with missing/incorrect content-type.
            content_type = response.headers.get('Content-Type', '')
            html_content = response.text
            looks_like_html = '<html' in html_content[:4096].lower()
            if 'text/html' not in content_type.lower() and not looks_like_html:
                logging.info(f"Skipping non-HTML content ({content_type}) at {url}")
                return

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
                'scraped_at': datetime.now(timezone.utc),
                'content_type': response.headers.get('Content-Type',
                                                    'unknown'),
                'thread_name': thread_name,
            }

            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                page_data['meta_description'] = meta_desc['content']

            links = self.extract_onion_links(url, html_content, soup)

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

            if depth < self.max_depth and links:
                # Follow all discovered links
                for link_url in links:
                    if executor:
                        # Each task creates its own session to keep requests usage thread-safe.
                        try:
                            self._submit_task(executor, link_url, depth + 1, url)
                            logging.debug(f"Queued: {link_url} (pending: {self.get_pending_count()})")
                        except RuntimeError as e:
                            # If executor is shutting down, fall back to local recursion.
                            if 'cannot schedule new futures after shutdown' in str(e):
                                logging.warning(f"Executor shutting down; scraping inline: {link_url}")
                                self.scrape_page(link_url, depth + 1,
                                                 parent_url=url, session=session,
                                                 executor=None)
                            else:
                                raise
                    else:
                        self.scrape_page(link_url, depth + 1,
                                         parent_url=url, session=session,
                                         executor=None)

        except requests.exceptions.Timeout:
            logging.error(f"Timeout on {url}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error scraping {url}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error on {url}: {e}")

    def scrape(self, start_urls):
        """Scrape each start URL using a thread pool."""
        logging.info("\nStarting Tor scraper with MongoDB storage...")
        logging.info(f"Max depth: {self.max_depth}")
        logging.info(f"Delay: {self.delay}s")
        logging.info(f"Thread pool size: {self.max_workers}")
        logging.info(f"Domains to scrape: {len(start_urls)}")
        logging.info("-" * 60)

        with self.lock:
            self.pending_tasks = 0

        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            for url in start_urls:
                self._submit_task(executor, url, 0, None)

            # Keep the executor alive until all recursively queued tasks finish.
            while self.get_pending_count() > 0:
                time.sleep(0.1)
        finally:
            executor.shutdown(wait=True)

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
    Load URLs from a text file (one URL per line).
    Only loads .onion (Tor) URLs.

    Args:
        filename: Path to file containing URLs

    Returns:
        List of onion URLs
    """
    urls = []
    skipped = 0
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Only include .onion URLs
                    if '.onion' in line:
                        if not line.lower().startswith(('http://', 'https://')):
                            line = f"http://{line}"
                        parsed = urlparse(line)
                        if parsed.netloc.endswith('.onion'):
                            urls.append(line)
                        else:
                            skipped += 1
                    else:
                        skipped += 1
        logging.info(f"✓ Loaded {len(urls)} onion URLs from {filename}")
        if skipped > 0:
            logging.info(f"  (Skipped {skipped} non-onion URLs)")
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
        max_depth=100,      # keep it shallow for testing
        delay=0,            # 0–3 second delay between requests
    )

    try:
        scraper.scrape(start_urls)
        scraper.get_stats()
    finally:
        scraper.close()

    print("\nDone!")
