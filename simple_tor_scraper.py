#!/usr/bin/env python3
"""
Simple Tor web scraper using requests + BeautifulSoup
This works reliably with Tor, unlike Scrapy which has issues with SOCKS proxies
"""

import requests
import json
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import random

class TorScraper:
    def __init__(self, max_depth=2, delay=3):
        self.session = requests.Session()
        # Configure SOCKS5 proxy for Tor
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
        self.results = []
        
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
    
    def scrape_page(self, url, depth=0):
        """Scrape a single page"""
        
        # Skip if already visited or too deep
        if url in self.visited or depth > self.max_depth:
            return
        
        self.visited.add(url)
        
        print(f"[Depth {depth}] Scraping: {url}")
        
        try:
            # Make request through Tor
            response = self.session.get(url, timeout=60)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract data
            page_data = {
                'url': url,
                'title': soup.title.string if soup.title else None,
                'headings': [h.get_text(strip=True) for h in soup.find_all(['h1', 'h2', 'h3'])],
                'paragraphs': [p.get_text(strip=True) for p in soup.find_all('p')][:5],  # First 5
                'links': [],
                'depth': depth
            }
            
            # Extract links
            links = []
            for link in soup.find_all('a', href=True):
                absolute_url = urljoin(url, link['href'])
                if self.is_valid_url(absolute_url):
                    links.append(absolute_url)
            
            page_data['links'] = links[:10]  # First 10 links
            
            print(f"  ✓ Found {len(page_data['headings'])} headings, {len(links)} links")
            
            self.results.append(page_data)
            
            # Follow links if not at max depth
            if depth < self.max_depth:
                for link_url in links[:5]:  # Limit to 5 links per page
                    if link_url not in self.visited:
                        # Random delay to be polite
                        time.sleep(self.delay + random.uniform(0, 2))
                        self.scrape_page(link_url, depth + 1)
            
        except requests.exceptions.Timeout:
            print(f"  ✗ Timeout on {url}")
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Error scraping {url}: {e}")
        except Exception as e:
            print(f"  ✗ Unexpected error on {url}: {e}")
    
    def scrape(self, start_urls):
        """Start scraping from a list of URLs"""
        print("Starting Tor scraper...")
        print(f"Max depth: {self.max_depth}")
        print(f"Delay: {self.delay}s")
        print("-" * 60)
        
        for url in start_urls:
            self.scrape_page(url, depth=0)
        
        return self.results
    
    def save_results(self, filename='tor_output.json'):
        """Save results to JSON file"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        print(f"\n✓ Saved {len(self.results)} pages to {filename}")


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


if __name__ == '__main__':
    # Test Tor first
    if not test_tor_connection():
        print("Please make sure Tor is running on port 9050")
        print("Start it with: sudo systemctl start tor")
        exit(1)
    
    # Create scraper
    scraper = TorScraper(
        max_depth=1,  # Keep it shallow for testing
        delay=3       # 3 second delay between requests
    )
    
    # Add your .onion URLs here
    start_urls = [
        'http://yvudsnnux372gj2nvg3bnkficwf4niel6drfqyhbtglgdsf2l75xfqqd.onion/',
    ]
    
    # Scrape
    results = scraper.scrape(start_urls)
    
    # Save
    scraper.save_results('tor_output.json')
    
    print(f"\nDone! Scraped {len(results)} pages total")
