"""Loopback HTTP fixture shared by prompt replay regression and browser checks."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread


@contextmanager
def capture_provider():
    observed = []

    class Fixture(BaseHTTPRequestHandler):
        def do_POST(self):
            wire = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(wire)
            request = wire["messages"][-1]["content"]
            data = json.dumps(
                {
                    "id": "local-" + str(len(observed)),
                    "model": "fixture",
                    "system_fingerprint": "test-system-build",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"shot": request}, ensure_ascii=False)
                            },
                            "finish_reason": "length" if request == "partial" else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    config = json.dumps(
        {
            "local": {
                "base_url": "http://127.0.0.1:" + str(server.server_port) + "/v1",
                "transport": "loopback_chat",
                "models": ["fixture"],
                "input_price_per_million": "0",
                "output_price_per_million": "0",
                "json_output": True,
            }
        }
    )

    try:
        yield config, observed
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
