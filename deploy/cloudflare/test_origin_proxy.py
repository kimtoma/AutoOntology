# SPDX-License-Identifier: MIT
"""Exercise the actual loopback HTTP boundary without model or database dependencies."""

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
            {"path": self.path, "headers": dict(self.headers), "body": body.decode()}
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
            connection.close()
        finally:
            for server in (gateway, upstream):
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
