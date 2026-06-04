import os
import sys
import subprocess
import time
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PORT = int(os.environ.get("PORT", 8080))

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok", "message": "DLMM Bot is running 24/7!"}')
        
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        
    def log_message(self, format, *args):
        return

def run_http_server():
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    logging.info(f"Health check HTTP server started on port {PORT}")
    httpd.serve_forever()

def get_python_executable():
    # If a virtual environment Python exists (e.g. Render installs to .venv), use it!
    # Linux (Render): .venv/bin/python
    # Windows (Local): .venv/Scripts/python.exe
    venv_linux = os.path.join(".venv", "bin", "python")
    venv_windows = os.path.join(".venv", "Scripts", "python.exe")
    
    if os.path.exists(venv_linux):
        logging.info(f"Using virtualenv python: {venv_linux}")
        return venv_linux
    elif os.path.exists(venv_windows):
        logging.info(f"Using virtualenv python: {venv_windows}")
        return venv_windows
        
    logging.info(f"Using system python: {sys.executable}")
    return sys.executable

def main():
    logging.info("Starting DLMM Standalone 24/7 Server...")
    
    # Start HTTP server for Render's port bind checks
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    python_path = get_python_executable()
    
    # Start bot.py as subprocess
    logging.info("Launching dlmm_bot.py...")
    bot_process = subprocess.Popen([python_path, "dlmm_bot.py"])
    
    try:
        while True:
            bot_poll = bot_process.poll()
            if bot_poll is not None:
                logging.error(f"dlmm_bot.py stopped unexpectedly! Restarting...")
                bot_process = subprocess.Popen([python_path, "dlmm_bot.py"])
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down processes...")
        bot_process.terminate()
        sys.exit(0)

if __name__ == "__main__":
    main()
