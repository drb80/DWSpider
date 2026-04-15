FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set environment variables for Mongo and Tor hostnames (override as needed)
ENV MONGO_URI=mongodb://mongo:27017/
ENV TOR_SOCKS=socks5h://tor:9050

CMD ["python", "tor_scraper.py"]
