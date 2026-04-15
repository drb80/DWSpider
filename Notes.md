https://ahmia.fi/add/onionsadded/?1234
https://ahmia.fi/address/?1234
http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/add/onionsadded/

---

# Docker Setup (Recommended)

Docker runs Tor, MongoDB, and the scraper all together in the background. No need to install or start services manually.

## Prerequisites
- Install Docker Desktop: https://www.docker.com/products/docker-desktop/
- Make sure Docker Desktop is **running** (whale icon in system tray) before using any commands below

## First Time / After Code Changes
Open a terminal in the DWSpider folder and run:
```
docker compose up -d --build
```
- `up` starts all three services (Tor, MongoDB, scraper)
- `-d` runs them in the background (detached) so you can close the terminal
- `--build` rebuilds the scraper image to pick up any code changes

## Day-to-Day Commands

| Command | What it does |
|---|---|
| `docker compose up -d` | Start everything (no rebuild needed if code hasn't changed) |
| `docker compose down` | Stop and remove all containers |
| `docker compose ps` | Check which containers are running |
| `docker compose restart scraper` | Restart just the scraper |

## Watching the Scraper

| Command | What it does |
|---|---|
| `docker compose logs -f scraper` | Live stream scraper output (Ctrl+C to stop watching) |
| `docker compose logs --tail 50 scraper` | Show the last 50 lines |
| `docker compose logs --tail 100 tor` | Check Tor container logs |
| `docker compose logs --tail 100 mongo` | Check MongoDB container logs |

## Checking the Data in MongoDB
Connect to the MongoDB container and open a shell:
```
docker compose exec mongo mongosh
```
Then inside mongosh:
```js
use tor_scraper              // switch to the scraper database
db.pages.countDocuments()    // how many pages have been saved
db.pages.find().limit(5)     // look at the first 5 saved pages
db.pages.distinct("url").length  // count unique URLs scraped
```
Type `exit` to leave mongosh.

## Troubleshooting

- **"Cannot connect to the Docker daemon"** -- Docker Desktop is not running. Open it from the Start menu and wait for the whale icon to appear in the system tray.
- **Lots of "Host unreachable" errors** -- Normal. Many .onion sites go offline. The scraper skips them and moves on.
- **Scraper container exits immediately** -- Check logs with `docker compose logs scraper` to see the error. Common causes: empty `urls.txt`, Tor not ready yet (restart with `docker compose restart scraper`).
- **Want to start fresh** -- Stop everything and delete the data volume:
  ```
  docker compose down
  rm -rf ./data/db
  docker compose up -d --build
  ```

---

# Local Setup (Alternative -- without Docker)

If you prefer to run things directly on Windows without Docker:

## Start Tor
- If you installed the Tor Expert Bundle:
  - Open a terminal and run:
    `start tor`
  - Or run `tor.exe` from its install directory.
- If you use the Tor Browser, just launch the Tor Browser -- it will start the Tor process.

## Start MongoDB
- If installed as a service (default for MongoDB installer):
  - Open a terminal as Administrator and run:
    `net start MongoDB`
  - Or use:
    `sc start MongoDB`
- If not installed as a service:
  - Find the path to `mongod.exe` (e.g., `C:\Program Files\MongoDB\Server\6.0\bin\mongod.exe`)
  - Run:
    `"C:\Program Files\MongoDB\Server\6.0\bin\mongod.exe"`

## Run the Scraper
```
python tor_scraper.py
```
