@echo off
REM Start Tor and MongoDB, then run the scraper

REM Start Tor (adjust path if needed)
start "Tor" tor.exe

REM Start MongoDB as a service
net start MongoDB

REM Wait a few seconds for services to start
ping 127.0.0.1 -n 6 > nul

REM Activate Python virtual environment
call .\venv\Scripts\activate.bat

REM Run the scraper
c:/python314/python.exe tor_scraper.py

pause
