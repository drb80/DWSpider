# Tor Web Scraper with Scrapy

A Python web scraper that routes through Tor to access .onion sites and the dark web while avoiding image downloads.


## Start Tor

    docker run --rm -p 127.0.0.1:9050:9050 -e SOCKS_HOSTNAME=0.0.0.0 leplusorg/tor

Verify Tor is running

    curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org

## Start Mongo

    mkdir data
    docker run -p 27017:27017 -v ${PWD}/data/db:/data/db mongo

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

The scraper is configured to use Tor's SOCKS5 proxy on port 9050 (default). If your Tor instance uses a different port, modify the proxy setting in `tor_scraper.py`:

```python
meta={'proxy': 'socks5h://127.0.0.1:9050'}  # Change port if needed
```

## Usage

### Update Target URLs

Edit `tor_scraper.py` and add your .onion URLs:

```python
process.crawl(
    TorSpider,
    start_urls=[
        'http://your-onion-address.onion',
        'http://another-onion-site.onion',
    ],
    max_depth=2
)
```

### 4. Run the Scraper

```bash
python3 tor_scraper.py
```

Output will be written to Mongo.

## How It Works

The scraper uses Scrapy's built-in `HttpProxyMiddleware` with PySocks to route all requests through Tor:

- Each request includes `meta={'proxy': 'socks5h://127.0.0.1:9050'}`
- The `socks5h` protocol ensures DNS resolution happens through Tor (prevents DNS leaks)
- All traffic is routed through Tor's SOCKS proxy on port 9050

## Important Settings for Tor

The scraper includes Tor-specific optimizations:

- **Increased timeouts**: Tor connections are slower (60s timeout)
- **Lower concurrency**: Only 1 request per domain at a time
- **Higher delays**: 3 seconds between requests with randomization
- **Retry logic**: Automatically retries failed requests (3 attempts)
- **socks5h protocol**: DNS resolution happens through Tor (prevents DNS leaks)
- **Tor Browser user agent**: Better compatibility with .onion sites

## Security Considerations

### DNS Leaks
- Always use `socks5h://` (not `socks5://`) to ensure DNS requests go through Tor
- This prevents DNS leaks that could reveal your real IP address

### Anonymity
- The scraper uses Tor Browser's user agent string
- Cookies are disabled by default
- Each request routes through Tor's network

### Best Practices
- Don't log in to personal accounts while using Tor
- Be aware that Tor is slower than regular internet
- Respect .onion site resources - they often have limited capacity
- Be mindful of legal implications in your jurisdiction

## Troubleshooting

### "Connection refused" error
- Make sure Tor is running: `systemctl status tor`
- Check Tor is listening on port 9050: `netstat -an | grep 9050`
- Start Tor if needed: `sudo systemctl start tor`

### "DNS resolution failed"
- Verify you're using `socks5h://` (not `socks5://`)
- Check Tor service is working: `curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org`

### "Module not found" errors
- Reinstall dependencies: `pip install -r requirements.txt`
- Try using a virtual environment:
  ```bash
  python -m venv venv
  source venv/bin/activate  # or `venv\Scripts\activate` on Windows
  pip install -r requirements.txt
  ```

### Slow performance
- This is normal for Tor! Connections are routed through multiple nodes
- Consider reducing `max_depth` to 1 for faster results
- Increase `DOWNLOAD_DELAY` if you're getting timeout errors

### .onion sites not loading
- Verify the .onion address is still active (many change frequently)
- Some .onion sites may be temporarily down or have limited uptime
- Try accessing the site manually through Tor Browser first to verify it works

## Output Format

## Legal and Ethical Considerations

- **Know your jurisdiction**: Laws regarding Tor usage vary by country
- **Respect terms of service**: Even on .onion sites
- **Ethical scraping**: Don't overload hidden services (they have limited resources)
- **Privacy**: Be aware of what data you're collecting and how you're storing it
- **Legal content only**: Accessing illegal content is illegal, regardless of whether you're using Tor

## Rate Limiting

.onion sites often have very limited resources. The scraper is configured conservatively:
- 1 concurrent request per domain
- 3-second delay between requests (with randomization)
- Automatic retries for failed requests
- Maximum 4 concurrent requests total

Adjust these if needed, but be respectful of site resources.

## Advanced Configuration

### Change Tor Port

If you're using Tor Browser instead of the Tor daemon, change the port:

```python
meta={'proxy': 'socks5h://127.0.0.1:9150'}  # Tor Browser port
```

### Adjust Timeouts and Delays

In `tor_scraper.py`, modify these settings:

```python
custom_settings = {
    'DOWNLOAD_DELAY': 3,  # Seconds between requests
    'DOWNLOAD_TIMEOUT': 60,  # Request timeout in seconds
    'CONCURRENT_REQUESTS_PER_DOMAIN': 1,  # Requests per domain
    'RETRY_TIMES': 3,  # Number of retries
}
```

### Request New Tor Circuit (Renew IP)

You can request a new Tor circuit to get a new IP address:

```python
# Requires stem library: pip install stem
from stem import Signal
from stem.control import Controller

with Controller.from_port(port=9051) as controller:
    controller.authenticate()
    controller.signal(Signal.NEWNYM)
```

Note: You'll need to enable Tor's control port first. Edit `/etc/tor/torrc`:
```
ControlPort 9051
CookieAuthentication 1
```

Then restart Tor: `sudo systemctl restart tor`
