# SPDX-License-Identifier: MIT
"""Exercise the actual loopback HTTP boundary without model or database dependencies."""

import hashlib
import http.client
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "origin_proxy", Path(__file__).with_name("origin_proxy.py")
)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        result = json.dumps(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": body.decode(errors="replace"),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)


class OriginTests(unittest.TestCase):
    def test_tunnel_cannot_bypass_gateway_and_browser_headers_are_not_trusted(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        gateway = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Proxy)
        gateway.secret = "test-origin-secret-that-is-not-a-real-secret"
        gateway.app_port = upstream.server_port
        for server in (upstream, gateway):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
            connection.request("POST", "/api/ontology", body=b"{}")
            self.assertEqual(connection.getresponse().status, 403)
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
            connection.request(
                "POST",
                "/api/playground/chat",
                body=b'{"question":"sample"}',
                headers={
                    "X-AutoOntology-Gateway": gateway.secret,
                    "Host": "attacker.example",
                    "Origin": "https://attacker.example",
                    "Cookie": "do-not-forward",
                    "Authorization": "do-not-forward",
                    "Content-Type": "application/json",
                    "Last-Event-ID": "1234",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            data = json.loads(response.read())
            self.assertEqual(data["path"], "/api/playground/chat")
            self.assertEqual(data["headers"]["Host"], f"127.0.0.1:{upstream.server_port}")
            for name in ("Origin", "Cookie", "Authorization", "X-AutoOntology-Gateway"):
                self.assertNotIn(name, data["headers"])
            self.assertEqual(json.loads(data["body"]), {"question": "sample"})
            self.assertEqual(data["headers"]["Last-Event-ID"], "1234")
            connection.close()
        finally:
            for server in (gateway, upstream):
                server.shutdown()
                server.server_close()

    def test_large_fixed_and_chunked_uploads_keep_their_binary_contents(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        gateway = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Proxy)
        gateway.secret = "test-origin-secret-that-is-not-a-real-secret"
        gateway.app_port = upstream.server_port
        for server in (upstream, gateway):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        payload = bytes(range(256)) * 1024
        try:
            for chunked in (False, True):
                with self.subTest(chunked=chunked):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", gateway.server_port, timeout=3
                    )
                    connection.request(
                        "POST",
                        "/api/sources",
                        body=iter([payload[:70000], payload[70000:]]) if chunked else payload,
                        headers={
                            "X-AutoOntology-Gateway": gateway.secret,
                            "Content-Type": "multipart/form-data; boundary=test",
                        },
                        encode_chunked=chunked,
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(
                        json.loads(response.read())["sha256"], hashlib.sha256(payload).hexdigest()
                    )
                    connection.close()
        finally:
            for server in (gateway, upstream):
                server.shutdown()
                server.server_close()

    def test_invalid_or_oversized_upload_framing_is_rejected(self):
        gateway = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Proxy)
        gateway.secret = "test-origin-secret-that-is-not-a-real-secret"
        gateway.app_port = 9
        threading.Thread(target=gateway.serve_forever, daemon=True).start()
        try:
            cases = [
                ({"Content-Length": str(proxy.MAX_BODY_BYTES + 1)}, None, 413),
                ({"Content-Length": "0", "Transfer-Encoding": "chunked"}, b"0\r\n\r\n", 400),
                ({"Transfer-Encoding": "chunked"}, b"gg\r\n", 400),
                ({"Transfer-Encoding": "chunked"}, b"100000000\r\n", 413),
            ]
            for headers, body, status in cases:
                with self.subTest(headers=headers, body=body):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", gateway.server_port, timeout=3
                    )
                    connection.request(
                        "POST",
                        "/api/sources",
                        body=body,
                        headers={**headers, "X-AutoOntology-Gateway": gateway.secret},
                    )
                    self.assertEqual(connection.getresponse().status, status)
                    connection.close()
        finally:
            gateway.shutdown()
            gateway.server_close()


if __name__ == "__main__":
    unittest.main()
