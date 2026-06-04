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

def main():
    logging.info("Starting DLMM Standalone 24/7 Server...")
    
    # Start HTTP server for Render's port bind checks
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Start bot.py as subprocess
    logging.info("Launching dlmm_bot.py...")
    bot_process = subprocess.Popen([sys.executable, "dlmm_bot.py"])
    
    try:
        while True:
            bot_poll = bot_process.poll()
            if bot_poll is not None:
                logging.error(f"dlmm_bot.py stopped unexpectedly! Restarting...")
                bot_process = subprocess.Popen([sys.executable, "dlmm_bot.py"])
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down processes...")
        bot_process.terminate()
        sys.exit(0)

if __name__ == "__main__":
    main()
