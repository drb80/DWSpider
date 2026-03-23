import threading
import unittest
from types import SimpleNamespace

from tor_scraper import TorScraperMongo


class FakeResponse:
    def __init__(self, html):
        self.text = html
        self.status_code = 200
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, html_by_url):
        self._html_by_url = html_by_url

    def get(self, url, timeout=60):
        html = self._html_by_url.get(url)
        if html is None:
            raise RuntimeError(f"Unexpected URL in test: {url}")
        return FakeResponse(html)


class TestExecutorShutdownSmoke(unittest.TestCase):
    def _build_scraper(self):
        # Build a minimal instance without opening a Mongo connection.
        scraper = TorScraperMongo.__new__(TorScraperMongo)
        scraper.max_depth = 1
        scraper.delay = 0
        scraper.max_workers = 2
        scraper.verify_ssl = True
        scraper.request_timeout = 60
        scraper.max_retries = 0
        scraper.visited = set()
        scraper.pages_saved = 0
        scraper.pending_tasks = 0
        scraper.lock = threading.Lock()
        scraper.collection = SimpleNamespace(
            insert_one=lambda _: SimpleNamespace(inserted_id="fake-id")
        )
        return scraper

    def test_scrape_survives_submit_after_shutdown_error(self):
        root_url = "http://root-example.onion/"
        child_url = "http://child-example.onion/"

        html_by_url = {
            root_url: f"<html><body><a href=\"{child_url}\">child</a></body></html>",
            child_url: "<html><body><p>ok</p></body></html>",
        }

        scraper = self._build_scraper()
        scraper.get_session = lambda: FakeSession(html_by_url)

        original_submit = scraper._submit_task

        def flaky_submit(executor, url, depth, parent_url):
            if depth > 0:
                raise RuntimeError("cannot schedule new futures after shutdown")
            return original_submit(executor, url, depth, parent_url)

        scraper._submit_task = flaky_submit

        # Smoke-test expectation: no exception and both pages handled.
        scraper.scrape([root_url])

        self.assertEqual(scraper.get_pending_count(), 0)
        self.assertIn(root_url, scraper.visited)
        self.assertIn(child_url, scraper.visited)
        self.assertEqual(scraper.pages_saved, 2)


if __name__ == "__main__":
    unittest.main()