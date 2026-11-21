#!/usr/bin/env python3
"""Fixed 64KiB synthetic HTTP target for the isolated destination guest."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys

IP = "10.209.20.2"
PORT = 18080
PAYLOAD = (b"sre-lab03d-synthetic-0123456789abcdef" * 2048)[:65536]
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, b"healthy")
        else:
            self.reply(404, b"missing")

    def do_POST(self):
        if self.path != "/upload" or self.headers.get("Content-Length") != str(len(PAYLOAD)):
            self.reply(400, b"unexpected-request")
            return
        data = self.rfile.read(len(PAYLOAD))
        if data != PAYLOAD:
            self.reply(400, b"unexpected-body")
        else:
            self.reply(200, SHA256.encode())

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    if sys.argv[1:] != ["serve"]:
        sys.exit("usage: http_vm.py serve")
    ThreadingHTTPServer((IP, PORT), Handler).serve_forever()
