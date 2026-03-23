#!/usr/bin/env python3
"""Harvest .onion seeds from multiple fresh sources.

This module is designed for continuous seed expansion. It can ingest onion
addresses from:
- public web pages (for example Ahmia pages)
- local seed files
- previously scraped MongoDB documents (url and links fields)

It outputs:
- a deduplicated seed URL file suitable for tor_scraper.py
- a JSON catalog with first/last seen timestamps and source attribution
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient


DEFAULT_WEB_SOURCES = [
    "https://ahmia.fi/address/",
    "https://ahmia.fyi/address/",
]

ONION_TOKEN_RE = re.compile(
    r"(?i)(?:https?://)?([a-z2-7]{16}|[a-z2-7]{56})\.onion(?:/[^\s\"'<>)]*)?"
)
ONION_HOST_RE = re.compile(r"^[a-z2-7]{16}\.onion$|^[a-z2-7]{56}\.onion$", re.IGNORECASE)


@dataclass(frozen=True)
class HarvestStats:
    total_candidates: int
    unique_hosts: int
    new_hosts: int
    existing_hosts: int
    sources_used: int


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def canonicalize_onion_url(value: str) -> str | None:
    """Canonicalize to a root onion URL (scheme://host/).

    Returns None when the input does not contain a valid v2/v3 onion hostname.
    """
    if not value:
        return None

    candidate = value.strip()
    if not candidate:
        return None

    if not candidate.lower().startswith(("http://", "https://")):
        candidate = f"http://{candidate}"

    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if not host.endswith(".onion"):
        return None

    if not ONION_HOST_RE.match(host):
        return None

    scheme = "https" if parsed.scheme.lower() == "https" else "http"
    return f"{scheme}://{host}/"


def extract_onion_urls_from_text(text: str) -> set[str]:
    """Extract canonical onion root URLs from arbitrary text."""
    extracted: set[str] = set()
    for match in ONION_TOKEN_RE.finditer(text or ""):
        raw = match.group(0)
        normalized = canonicalize_onion_url(raw)
        if normalized:
            extracted.add(normalized)
    return extracted


def extract_onion_urls_from_html(page_url: str, html_text: str) -> set[str]:
    """Extract onion URLs from both links and raw text in HTML."""
    found = set(extract_onion_urls_from_text(html_text))
    soup = BeautifulSoup(html_text, "html.parser")

    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(page_url, anchor["href"])
        normalized = canonicalize_onion_url(absolute)
        if normalized:
            found.add(normalized)

    return found


def is_ahmia_source(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"ahmia.fi", "ahmia.fyi"}


def build_paginated_urls(base_url: str, start_page: int, max_pages: int) -> list[str]:
    """Build paginated URLs by applying/overwriting the `page` query param."""
    if max_pages < 1:
        return [base_url]

    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    urls = []

    for page_number in range(start_page, start_page + max_pages):
        page_query = dict(query)
        page_query["page"] = str(page_number)
        new_parsed = parsed._replace(query=urlencode(page_query, doseq=True))
        urls.append(urlunparse(new_parsed))

    return urls


def expand_web_sources(source_urls: Iterable[str], ahmia_start_page: int, ahmia_max_pages: int) -> list[str]:
    """Expand Ahmia sources with page=N URLs while leaving non-Ahmia unchanged."""
    expanded: list[str] = []
    seen: set[str] = set()

    for source in source_urls:
        source = source.strip()
        if not source:
            continue

        candidates = (
            build_paginated_urls(source, ahmia_start_page, ahmia_max_pages)
            if is_ahmia_source(source)
            else [source]
        )

        for candidate in candidates:
            if candidate not in seen:
                expanded.append(candidate)
                seen.add(candidate)

    return expanded


class SeedHarvester:
    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        timeout: int = 30,
        retries: int = 2,
    ):
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.timeout = timeout
        self.retries = retries

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:121.0) "
                    "Gecko/20100101 Firefox/121.0"
                )
            }
        )

    def fetch_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retries:
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("fetch_text reached unexpected state")

    def harvest_from_web(self, source_urls: Iterable[str]) -> dict[str, set[str]]:
        """Return mapping of canonical URL -> source tags from web sources."""
        gathered: dict[str, set[str]] = {}

        for source in source_urls:
            source = source.strip()
            if not source:
                continue

            try:
                html = self.fetch_text(source)
                urls = extract_onion_urls_from_html(source, html)
                logging.info("Web source %s yielded %d onion hosts", source, len(urls))
                for url in urls:
                    gathered.setdefault(url, set()).add(f"web:{source}")
            except Exception as exc:  # pragma: no cover - network varies in runtime
                logging.warning("Failed fetching %s: %s", source, exc)

        return gathered

    def harvest_from_files(self, file_paths: Iterable[str]) -> dict[str, set[str]]:
        """Return mapping of canonical URL -> source tags from local files."""
        gathered: dict[str, set[str]] = {}

        for file_path in file_paths:
            p = Path(file_path)
            if not p.exists():
                logging.warning("Seed input file not found: %s", p)
                continue

            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            raw_text = "\n".join(line for line in lines if not line.strip().startswith("#"))
            urls = extract_onion_urls_from_text(raw_text)
            logging.info("File source %s yielded %d onion hosts", p, len(urls))

            for url in urls:
                gathered.setdefault(url, set()).add(f"file:{p}")

        return gathered

    def harvest_from_mongo(self, doc_limit: int | None = None) -> dict[str, set[str]]:
        """Return mapping of canonical URL -> source tags from Mongo pages collection."""
        gathered: dict[str, set[str]] = {}

        client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
        try:
            collection = client[self.db_name][self.collection_name]
            projection = {"url": 1, "links": 1}
            cursor = collection.find({}, projection=projection, batch_size=500)

            processed = 0
            for doc in cursor:
                if doc_limit and processed >= doc_limit:
                    break
                processed += 1

                url = canonicalize_onion_url(doc.get("url", ""))
                if url:
                    gathered.setdefault(url, set()).add("mongo:url")

                for link in doc.get("links", []) or []:
                    norm = canonicalize_onion_url(link)
                    if norm:
                        gathered.setdefault(norm, set()).add("mongo:links")

            logging.info("Mongo source yielded %d onion hosts from %d documents", len(gathered), processed)
        finally:
            client.close()

        return gathered


def load_catalog(catalog_file: Path) -> dict:
    if not catalog_file.exists():
        return {"hosts": {}, "generated_at": None, "total_hosts": 0}

    try:
        data = json.loads(catalog_file.read_text(encoding="utf-8"))
        if "hosts" not in data or not isinstance(data["hosts"], dict):
            return {"hosts": {}, "generated_at": None, "total_hosts": 0}
        return data
    except json.JSONDecodeError:
        logging.warning("Catalog file is not valid JSON; starting a new one: %s", catalog_file)
        return {"hosts": {}, "generated_at": None, "total_hosts": 0}


def merge_into_catalog(
    catalog: dict,
    gathered: dict[str, set[str]],
    now_iso: str,
) -> HarvestStats:
    hosts = catalog.setdefault("hosts", {})
    new_hosts = 0
    existing_hosts = 0

    for canonical_url, source_tags in gathered.items():
        host = urlparse(canonical_url).hostname
        if not host:
            continue

        if host in hosts:
            existing_hosts += 1
            entry = hosts[host]
            entry["last_seen"] = now_iso
            entry["seen_count"] = int(entry.get("seen_count", 0)) + 1
            source_set = set(entry.get("sources", []))
            source_set.update(source_tags)
            entry["sources"] = sorted(source_set)
            if entry.get("seed_url") != canonical_url:
                entry["seed_url"] = canonical_url
        else:
            new_hosts += 1
            hosts[host] = {
                "seed_url": canonical_url,
                "first_seen": now_iso,
                "last_seen": now_iso,
                "seen_count": 1,
                "sources": sorted(source_tags),
            }

    catalog["generated_at"] = now_iso
    catalog["total_hosts"] = len(hosts)

    return HarvestStats(
        total_candidates=len(gathered),
        unique_hosts=len(hosts),
        new_hosts=new_hosts,
        existing_hosts=existing_hosts,
        sources_used=len({tag for tags in gathered.values() for tag in tags}),
    )


def write_seed_file(catalog: dict, output_file: Path) -> None:
    hosts = catalog.get("hosts", {})
    urls = sorted(entry["seed_url"] for entry in hosts.values() if entry.get("seed_url"))

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")


def write_catalog(catalog: dict, catalog_file: Path) -> None:
    catalog_file.parent.mkdir(parents=True, exist_ok=True)
    catalog_file.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harvest fresh .onion seeds from multiple sources")
    parser.add_argument("--output-file", default="urls.txt", help="Path for deduplicated seed output")
    parser.add_argument(
        "--catalog-file",
        default="data/seeds/seed_catalog.json",
        help="Path to seed metadata catalog JSON",
    )

    parser.add_argument(
        "--source-url",
        action="append",
        dest="source_urls",
        default=[],
        help="Web source URL (repeatable). Defaults include Ahmia address pages.",
    )
    parser.add_argument(
        "--source-file",
        action="append",
        dest="source_files",
        default=[],
        help="Local file path containing potential onion URLs (repeatable)",
    )

    parser.add_argument("--from-mongo", action="store_true", help="Harvest seeds from MongoDB pages collection")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017/")
    parser.add_argument("--db-name", default="tor_scraper")
    parser.add_argument("--collection-name", default="pages")
    parser.add_argument(
        "--mongo-doc-limit",
        type=int,
        default=None,
        help="Limit number of Mongo documents scanned (optional)",
    )

    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout for source fetches")
    parser.add_argument("--retries", type=int, default=2, help="HTTP retries per source")
    parser.add_argument(
        "--ahmia-max-pages",
        type=int,
        default=1,
        help="Number of paginated Ahmia pages to fetch per Ahmia source",
    )
    parser.add_argument(
        "--ahmia-start-page",
        type=int,
        default=1,
        help="Start page index for Ahmia pagination",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")

    source_urls = list(dict.fromkeys((args.source_urls or []) + DEFAULT_WEB_SOURCES))
    source_urls = expand_web_sources(
        source_urls,
        ahmia_start_page=max(1, args.ahmia_start_page),
        ahmia_max_pages=max(1, args.ahmia_max_pages),
    )
    logging.info("Total web source URLs after Ahmia expansion: %d", len(source_urls))
    source_files = list(dict.fromkeys((args.source_files or []) + ["urls.txt", "urls.txt.bak"]))

    harvester = SeedHarvester(
        mongo_uri=args.mongo_uri,
        db_name=args.db_name,
        collection_name=args.collection_name,
        timeout=args.timeout,
        retries=args.retries,
    )

    gathered: dict[str, set[str]] = {}

    for mapping in (
        harvester.harvest_from_files(source_files),
        harvester.harvest_from_web(source_urls),
        harvester.harvest_from_mongo(args.mongo_doc_limit) if args.from_mongo else {},
    ):
        for url, tags in mapping.items():
            gathered.setdefault(url, set()).update(tags)

    catalog_path = Path(args.catalog_file)
    output_path = Path(args.output_file)

    catalog = load_catalog(catalog_path)
    stats = merge_into_catalog(catalog, gathered, utc_now_iso())

    write_catalog(catalog, catalog_path)
    write_seed_file(catalog, output_path)

    logging.info("Harvest complete")
    logging.info("Candidates this run: %d", stats.total_candidates)
    logging.info("New hosts added: %d", stats.new_hosts)
    logging.info("Previously known hosts seen again: %d", stats.existing_hosts)
    logging.info("Total catalog hosts: %d", stats.unique_hosts)
    logging.info("Source tags captured this run: %d", stats.sources_used)
    logging.info("Catalog written to: %s", catalog_path)
    logging.info("Seed file written to: %s", output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
