# SPDX-License-Identifier: MIT
"""Loopback-only, authenticated origin for the Cloudflare demo Worker.

Run using the project venv. The tunnel targets this proxy, never the unprotected app.
No headers from the visitor are trusted to authorize a request or choose a destination.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        # Never log credentials, query strings, prompts, or file paths.
        pass

    def relay(self):
        actual = self.headers.get("X-AutoOntology-Gateway", "")
        expected = self.server.secret
        if not hmac.compare_digest(
            hashlib.sha256(actual.encode()).digest(), hashlib.sha256(expected.encode()).digest()
        ):
            self.send_error(403, "Gateway required")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 0 or length > 65536 or self.headers.get("Transfer-Encoding"):
            self.send_error(413)
            return
        if not self.path.startswith("/") or self.path.startswith("//"):
            self.send_error(400)
            return
        body = self.rfile.read(length) if length else None
        forwarded = {
            name: self.headers[name]
            for name in ("Accept", "Content-Type", "Range", "If-None-Match")
            if self.headers.get(name)
        }
        forwarded["Host"] = f"127.0.0.1:{self.server.app_port}"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.app_port, timeout=240)
        try:
            connection.request(self.command, self.path, body=body, headers=forwarded)
            response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in {
                    "connection",
                    "transfer-encoding",
                    "content-length",
                    "set-cookie",
                    "server",
                    "date",
                }:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            if not self.close_connection:
                self.send_error(502, "Application unavailable")
        finally:
            connection.close()

    do_GET = relay
    do_HEAD = relay
    do_POST = relay
    do_PUT = relay
    do_DELETE = relay
    do_PATCH = relay
    do_OPTIONS = relay


def serve():
    secrets_file = Path(
        os.environ.get(
            "AUTOONTOLOGY_DEMO_SECRETS",
            Path(__file__).parents[2] / "data/cloudflare-demo/secrets.json",
        )
    )
    secrets = json.loads(secrets_file.read_text())
    secret = secrets["origin_secret"]
    if len(secret) < 32:
        raise ValueError("origin secret must be at least 32 characters")
    server = ThreadingHTTPServer(
        ("127.0.0.1", int(os.environ.get("AUTOONTOLOGY_DEMO_PORT", "7810"))), Proxy
    )
    server.daemon_threads = True
    server.secret = secret
    server.app_port = int(os.environ.get("AUTOONTOLOGY_PORT", "7800"))
    server.serve_forever()


if __name__ == "__main__":
    serve()
