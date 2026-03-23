import unittest

from seed_harvester import (
    build_paginated_urls,
    canonicalize_onion_url,
    expand_web_sources,
    extract_onion_urls_from_html,
    extract_onion_urls_from_text,
    is_ahmia_source,
    merge_into_catalog,
)


class TestSeedHarvesterHelpers(unittest.TestCase):
    def test_ahmia_source_detection(self):
        self.assertTrue(is_ahmia_source("https://ahmia.fi/address/"))
        self.assertTrue(is_ahmia_source("https://ahmia.fyi/address/"))
        self.assertFalse(is_ahmia_source("https://example.org/list"))

    def test_build_paginated_urls(self):
        pages = build_paginated_urls("https://ahmia.fi/address/", start_page=2, max_pages=3)
        self.assertEqual(
            pages,
            [
                "https://ahmia.fi/address/?page=2",
                "https://ahmia.fi/address/?page=3",
                "https://ahmia.fi/address/?page=4",
            ],
        )

        with_existing_query = build_paginated_urls(
            "https://ahmia.fi/address/?sort=updated",
            start_page=1,
            max_pages=2,
        )
        self.assertEqual(
            with_existing_query,
            [
                "https://ahmia.fi/address/?sort=updated&page=1",
                "https://ahmia.fi/address/?sort=updated&page=2",
            ],
        )

    def test_expand_web_sources_only_expands_ahmia(self):
        sources = ["https://ahmia.fi/address/", "https://example.org/list"]
        expanded = expand_web_sources(sources, ahmia_start_page=1, ahmia_max_pages=2)
        self.assertEqual(
            expanded,
            [
                "https://ahmia.fi/address/?page=1",
                "https://ahmia.fi/address/?page=2",
                "https://example.org/list",
            ],
        )

    def test_canonicalize_onion_url(self):
        host = "a" * 56 + ".onion"
        self.assertEqual(
            canonicalize_onion_url(f"https://{host}/path?q=1"),
            f"https://{host}/",
        )
        self.assertEqual(canonicalize_onion_url(host), f"http://{host}/")
        self.assertIsNone(canonicalize_onion_url("https://example.com"))

    def test_extract_from_text_and_html(self):
        host1 = "b" * 56 + ".onion"
        host2 = "c" * 56 + ".onion"

        text = f"find http://{host1}/x and {host2} in this blob"
        from_text = extract_onion_urls_from_text(text)
        self.assertIn(f"http://{host1}/", from_text)
        self.assertIn(f"http://{host2}/", from_text)

        html = f'<html><body><a href="http://{host1}/a">a</a>{host2}</body></html>'
        from_html = extract_onion_urls_from_html("https://source.test", html)
        self.assertIn(f"http://{host1}/", from_html)
        self.assertIn(f"http://{host2}/", from_html)

    def test_merge_into_catalog_tracks_new_and_existing(self):
        host = "d" * 56 + ".onion"
        url = f"http://{host}/"
        catalog = {"hosts": {}, "generated_at": None, "total_hosts": 0}
        gathered = {url: {"file:urls.txt", "web:https://ahmia.fi/address/"}}

        stats1 = merge_into_catalog(catalog, gathered, "2026-03-22T00:00:00+00:00")
        self.assertEqual(stats1.new_hosts, 1)
        self.assertEqual(stats1.existing_hosts, 0)
        self.assertEqual(catalog["total_hosts"], 1)

        stats2 = merge_into_catalog(catalog, gathered, "2026-03-23T00:00:00+00:00")
        self.assertEqual(stats2.new_hosts, 0)
        self.assertEqual(stats2.existing_hosts, 1)
        self.assertEqual(catalog["hosts"][host]["seen_count"], 2)


if __name__ == "__main__":
    unittest.main()
