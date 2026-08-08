import http.server
import json
import logging

# --- CONFIGURATION ---
PORT = 5679
WEBHOOK_PATH = "/voice-command"
LOG_FILE = "aios_audit.log"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MockN8nHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == WEBHOOK_PATH:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                user_text = data.get("text", "")
                
                logger.info(f"📥 [MOCK N8N] Received voice command: '{user_text}'")
                
                # Simulate the AI Agent parsing the text into a Work Order
                # In a real scenario, this is where n8n + LLM would work.
                mock_response = {
                    "type": "work_order",
                    "id": "mock-uuid-12345",
                    "parent_charter_id": "root",
                    "version": "1.0.0",
                    "description": f"MOCK PARSED: {user_text}",
                    "status": "backlog",
                    "priority": "medium"
                }
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(mock_response).encode('utf-8'))
                logger.info("📤 [MOCK N8N] Sent mock response back to client.")
                
            except Exception as e:
                logger.error(f"❌ [MOCK N8N] Error processing request: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default logging to avoid cluttering the audit log
        return

if __name__ == "__main__":
    server_address = ('', PORT)
    httpd = http.server.HTTPServer(server_address, MockN8nHandler)
    logger.info(f"🚀 Mock n8n Webhook Server running on port {PORT}...")
    logger.info(f"Endpoint: http://localhost:{PORT}{WEBHOOK_PATH}")
    logger.info("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("🛑 Mock server stopping...")
        httpd.server_close()