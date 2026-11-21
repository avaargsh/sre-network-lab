#!/usr/bin/env python3
"""Synthetic HTTP endpoint for an isolated netns PMTU/MSS data-plane drill."""
from __future__ import annotations

import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sys

IP = "10.207.20.2"
PORT = 18080
PAYLOAD = (b"pmtu-lab-unique-0123456789abcdef" * 2048)[:65536]
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.reply(200, b"healthy")
        else:
            self.reply(404, b"not-found")

    def do_POST(self) -> None:
        if self.path != "/upload":
            self.reply(404, b"not-found")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length != len(PAYLOAD):
                self.reply(400, b"invalid-length")
                return
            content = self.rfile.read(length)
            if len(content) != length:
                return
            self.reply(200, hashlib.sha256(content).hexdigest().encode())
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            pass

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def client(action: str) -> int:
    conn = HTTPConnection(IP, PORT, timeout=6 if action == "upload" else 3)
    try:
        if action == "health":
            conn.request("GET", "/health")
        elif action == "upload":
            conn.request("POST", "/upload", body=PAYLOAD,
                         headers={"Content-Type": "application/octet-stream"})
        else:
            raise ValueError("invalid client mode")
        response = conn.getresponse()
        body = response.read().decode(errors="replace")
        expected = "healthy" if action == "health" else SHA
        ok = response.status == 200 and body == expected
        result = {"ok": ok, "status": response.status,
                  "body_matches": body == expected, "action": action}
    except (OSError, socket.timeout, TimeoutError) as exc:
        result = {"ok": False, "action": action,
                  "error": "timeout" if isinstance(exc, TimeoutError) or
                  isinstance(exc, socket.timeout) else type(exc).__name__}
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["ok"] else 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("serve", "upload", "health"):
        print("usage: pmtu_http_endpoint.py {serve|upload|health}", file=sys.stderr)
        return 2
    if sys.argv[1] == "serve":
        server = ThreadingHTTPServer((IP, PORT), Handler)
        server.serve_forever(poll_interval=0.1)
        return 0
    return client(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
