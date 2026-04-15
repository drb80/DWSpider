#!/usr/bin/env python3
"""Automated harvest-scrape loop.

Runs the seed harvester and tor scraper in alternating rounds to snowball
onion URL coverage. Each round:
  1. Harvest seeds from Ahmia + MongoDB (discovered links from prior scrapes)
  2. Scrape the updated seed list through Tor into MongoDB

Usage:
    python run_loop.py                # run 2 rounds (default)
    python run_loop.py --rounds 5     # run 5 rounds
    python run_loop.py --rounds 0     # run forever until stopped
"""

import argparse
import logging
import os
import subprocess
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)


def run_harvest(mongo_uri, db_name, collection_name):
    """Run seed_harvester.py to pull new onion URLs into urls.txt."""
    cmd = [
        sys.executable, "seed_harvester.py",
        "--from-mongo",
        "--mongo-uri", mongo_uri,
        "--db-name", db_name,
        "--collection-name", collection_name,
    ]
    logging.info("Running seed harvester...")
    result = subprocess.run(cmd)
    return result.returncode == 0


def run_scraper():
    """Run tor_scraper.py to scrape the current seed list."""
    cmd = [sys.executable, "tor_scraper.py"]
    logging.info("Running scraper...")
    result = subprocess.run(cmd)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Automated harvest-scrape loop")
    parser.add_argument("--rounds", type=int, default=2,
                        help="Number of harvest-scrape rounds (0 = run forever)")
    args = parser.parse_args()

    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
    db_name = os.environ.get('DB_NAME', 'tor_scraper')
    collection_name = os.environ.get('COLLECTION_NAME', 'pages')

    round_num = 0
    while args.rounds == 0 or round_num < args.rounds:
        round_num += 1
        logging.info("=" * 60)
        logging.info("ROUND %d", round_num)
        logging.info("=" * 60)

        if round_num > 1:
            # First round uses existing urls.txt; subsequent rounds harvest new seeds
            if not run_harvest(mongo_uri, db_name, collection_name):
                logging.error("Harvest failed on round %d, continuing with existing seeds", round_num)

        if not run_scraper():
            logging.error("Scraper failed on round %d", round_num)
            break

    logging.info("Loop complete after %d rounds", round_num)


if __name__ == "__main__":
    main()
