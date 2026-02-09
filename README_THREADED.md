# Multi-Threaded Tor Scraper

Scrapes multiple .onion domains simultaneously, with each domain getting its own thread.

## Key Features

- ✅ **One thread per domain** - Scrape multiple sites in parallel
- ✅ **Thread-safe** - Proper locking for shared resources
- ✅ **Each thread has its own session** - Prevents connection conflicts
- ✅ **Queue-based work distribution** - Efficient thread management
- ✅ **Detailed logging** - See which thread is doing what
- ✅ **All MongoDB features** - Same storage as the single-threaded version

## How It Works

```
Main Thread
    │
    ├─> Worker-1 Thread → Scrapes domain1.onion
    │
    ├─> Worker-2 Thread → Scrapes domain2.onion
    │
    └─> Worker-3 Thread → Scrapes domain3.onion
```

Each worker thread:
1. Gets a domain from the queue
2. Creates its own Tor session
3. Scrapes that domain (following links up to max_depth)
4. Saves all pages to MongoDB
5. Gets the next domain from the queue

## Usage

```bash
python tor_scraper_threaded.py
```

### Configuration

Edit the script to customize:

```python
# Number of concurrent threads (domains scraped simultaneously)
NUM_THREADS = 3

# Your target domains
start_urls = [
    'http://site1.onion/',
    'http://site2.onion/',
    'http://site3.onion/',
    # Add as many as you want
]

# Scraping settings
max_depth=1,   # How deep to follow links on each domain
delay=3,       # Seconds between requests (per thread)
```

## Thread Safety

The scraper uses proper synchronization:

- **`visited` set**: Protected by a lock to prevent duplicate scraping
- **`pages_saved` counter**: Atomic increment with lock
- **Sessions**: Each thread has its own requests session
- **MongoDB**: PyMongo is thread-safe by default

## Performance Considerations

### Threading vs Domains

```python
# 3 threads, 3 domains = all scraped in parallel
NUM_THREADS = 3
start_urls = ['site1.onion', 'site2.onion', 'site3.onion']

# 3 threads, 10 domains = 3 at a time, then next 3, etc.
NUM_THREADS = 3
start_urls = ['site1.onion', ..., 'site10.onion']  # 10 domains
```

### Tor Bandwidth Limits

- Tor is slower than regular internet
- Too many threads can overload Tor
- **Recommended**: 3-5 threads max
- Each thread makes requests with delays (respectful crawling)

### MongoDB Performance

- MongoDB handles concurrent writes well
- Duplicate URLs are caught by unique index
- Consider adding more indexes if doing complex queries

## Logging Output

The scraper shows detailed logs:

```
[Worker-1  ] 14:23:45 - INFO - Starting domain: http://site1.onion/
[Worker-2  ] 14:23:45 - INFO - Starting domain: http://site2.onion/
[Worker-1  ] 14:23:48 - INFO - [Depth 0] Scraping: http://site1.onion/
[Worker-2  ] 14:23:49 - INFO - [Depth 0] Scraping: http://site2.onion/
[Worker-1  ] 14:23:50 - INFO - ✓ Saved to MongoDB (ID: ...)
[Worker-2  ] 14:23:51 - INFO - ✓ Saved to MongoDB (ID: ...)
```

You can see which thread is working on which domain.

## Advantages Over Single-Threaded

**Single-threaded (original):**
```
Domain 1 → wait → Domain 2 → wait → Domain 3
Total time: ~10 minutes
```

**Multi-threaded (this version):**
```
Domain 1 ─┐
Domain 2 ─┤ all at once
Domain 3 ─┘
Total time: ~3-4 minutes
```

## Example: Scraping 10 Domains

```python
NUM_THREADS = 5  # 5 domains at a time

start_urls = [
    'http://domain1.onion/',
    'http://domain2.onion/',
    'http://domain3.onion/',
    'http://domain4.onion/',
    'http://domain5.onion/',
    'http://domain6.onion/',
    'http://domain7.onion/',
    'http://domain8.onion/',
    'http://domain9.onion/',
    'http://domain10.onion/',
]
```

Execution:
1. Threads 1-5 start on domains 1-5 (parallel)
2. As each finishes, it picks up domains 6-10
3. All done when queue is empty

## Statistics

After scraping, you'll see stats including which thread scraped what:

```
Database Statistics:
  Total documents: 125
  
  Pages scraped by thread:
    Worker-1: 45 pages
    Worker-2: 42 pages
    Worker-3: 38 pages
```

## Troubleshooting

### Too many threads causing errors

Reduce `NUM_THREADS`:
```python
NUM_THREADS = 2  # Start conservative
```

### Threads finishing too quickly

Increase `max_depth` to scrape deeper:
```python
max_depth=2,  # Follow links 2 levels deep
```

### Seeing "Already visited" warnings

This is normal - threads might discover the same URL. The thread-safe locking prevents duplicate work.

### MongoDB connection errors

Make sure MongoDB can handle concurrent connections. Default MongoDB config supports 65,536 connections, so this shouldn't be an issue with 3-5 threads.

## Comparison: Single vs Multi-Threaded

| Feature | Single-Threaded | Multi-Threaded |
|---------|----------------|----------------|
| Domains at once | 1 | N (configurable) |
| Speed for 5 domains | ~20 min | ~5 min |
| Complexity | Simple | More complex |
| Memory usage | Low | Medium |
| Best for | 1-2 domains | 3+ domains |

## When to Use Which

**Use single-threaded** (`tor_scraper_mongo.py`) when:
- Scraping only 1-2 domains
- Want simpler code
- Limited system resources

**Use multi-threaded** (`tor_scraper_threaded.py`) when:
- Scraping 3+ domains
- Want faster completion
- Have decent system resources

## Advanced: Adjusting Thread Count Dynamically

You can adjust based on number of domains:

```python
# Auto-set thread count based on domains
NUM_THREADS = min(len(start_urls), 5)  # Max 5, but fewer if less domains
```

## Memory Usage

Each thread uses:
- ~10-20 MB for session/buffers
- Temporary HTML storage during processing
- MongoDB client connection

With 5 threads: ~100-150 MB total (very reasonable)

## Legal & Ethical Notes

- Threading makes scraping faster, so be **extra careful** about rate limits
- Each thread still respects delays (default 3s between requests)
- Don't increase thread count to DOS sites - this is unethical and illegal
- Be respectful of .onion site resources

## Dependencies

Same as single-threaded version:
```bash
pip install -r requirements_simple.txt
```

No additional dependencies needed - uses Python's built-in `threading` module.
