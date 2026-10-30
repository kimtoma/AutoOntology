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
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY_BYTES = 100_000_000
CHUNK_BYTES = 65536


class PayloadTooLarge(ValueError):
    pass


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
        if not self.path.startswith("/") or self.path.startswith("//"):
            self.send_error(400)
            return
        try:
            body, length = self.read_payload()
        except PayloadTooLarge:
            self.send_error(413)
            return
        except (ValueError, OSError):
            self.send_error(400)
            return
        forwarded = {
            name: self.headers[name]
            for name in ("Accept", "Content-Type", "Range", "If-None-Match", "Last-Event-ID")
            if self.headers.get(name)
        }
        forwarded["Content-Length"] = str(length)
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
            body.close()

    def read_payload(self):
        """Spool uploads to disk and normalize HTTP framing before forwarding."""
        self.connection.settimeout(60)
        lengths = self.headers.get_all("Content-Length", [])
        transfer = self.headers.get("Transfer-Encoding", "").lower().strip()
        if len(lengths) > 1 or (lengths and transfer) or transfer not in ("", "chunked"):
            raise ValueError("ambiguous request framing")
        body = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        total = 0

        def copy(size):
            nonlocal total
            if size < 0:
                raise ValueError("negative length")
            if total + size > MAX_BODY_BYTES:
                raise PayloadTooLarge()
            while size:
                chunk = self.rfile.read(min(size, CHUNK_BYTES))
                if not chunk:
                    raise ValueError("incomplete body")
                body.write(chunk)
                total += len(chunk)
                size -= len(chunk)

        try:
            if transfer:
                while True:
                    line = self.rfile.readline(128)
                    size_text = line.removesuffix(b"\r\n").split(b";", 1)[0]
                    if (
                        not line.endswith(b"\r\n")
                        or not size_text
                        or any(ch not in b"0123456789abcdefABCDEF" for ch in size_text)
                    ):
                        raise ValueError("invalid chunk")
                    size = int(size_text, 16)
                    if not size:
                        trailers = 0
                        while True:
                            line = self.rfile.readline(8193)
                            trailers += len(line)
                            if not line.endswith(b"\r\n") or trailers > 8192:
                                raise ValueError("invalid trailers")
                            if line == b"\r\n":
                                break
                        break
                    copy(size)
                    if self.rfile.read(2) != b"\r\n":
                        raise ValueError("invalid chunk terminator")
            else:
                raw = lengths[0] if lengths else "0"
                if not raw.isascii() or not raw.isdigit():
                    raise ValueError("invalid content length")
                copy(int(raw))
            body.seek(0)
            return body, total
        except BaseException:
            body.close()
            raise

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
