"""Loopback ComfyUI protocol fixture for the browser suite. It performs no model inference."""

from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse


@contextmanager
def sample_generator(clip: Path):
    issued = []
    uploads = {}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, value, mime="application/json"):
            data = json.dumps(value).encode() if mime == "application/json" else value
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path == "/upload/image":
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                msg = BytesParser(policy=policy.default).parsebytes(
                    (
                        "Content-Type: "
                        + self.headers["Content-Type"]
                        + "\r\nMIME-Version: 1.0\r\n\r\n"
                    ).encode()
                    + raw
                )
                parts = {
                    p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()
                }
                item = parts.get("image")
                if item is None:
                    self.send_error(400)
                    return
                payload = item.get_payload(decode=True)
                filename = item.get_filename()
                if not filename or not payload.startswith(bytes.fromhex("89504e470d0a1a0a")):
                    self.send_error(400)
                    return
                uploads[filename] = payload
                self.reply({"name": filename, "type": "input", "subfolder": ""})
                return
            if self.path != "/prompt":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "2" in body["prompt"]:
                ref_name = body["prompt"]["2"]["inputs"]["image"]
                if ref_name not in uploads:
                    self.send_error(400)
                    return
                body["fixture_observed_image_sha256"] = hashlib.sha256(
                    uploads[ref_name]
                ).hexdigest()
            issued.append(body)
            self.reply({"prompt_id": body["prompt_id"]})

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/view":
                query = parse_qs(urlparse(self.path).query)
                if query.get("type") == ["input"]:
                    payload = uploads.get(query.get("filename", [""])[0])
                    if payload is None:
                        self.send_error(404)
                        return
                    self.reply(payload, "image/png")
                    return
                is_image = parse_qs(urlparse(self.path).query).get("filename") == [
                    "sample-output.png"
                ]
                self.reply(
                    (clip.with_suffix(".png") if is_image else clip).read_bytes(),
                    "image/png" if is_image else "video/mp4",
                )
                return
            if path.startswith("/history/"):
                entry = next((p for p in issued if path.endswith("/" + p["prompt_id"])), None)
                is_image = entry and entry["prompt"]["90"]["inputs"].get("image") is True
                self.reply(
                    {
                        entry["prompt_id"]: {
                            "status": {"completed": True, "status_str": "success"},
                            "outputs": {
                                "90": {
                                    ("images" if is_image else "videos"): [
                                        {
                                            "filename": (
                                                "sample-output.png"
                                                if is_image
                                                else "sample-output.mp4"
                                            ),
                                            "type": "output",
                                            "subfolder": "",
                                        }
                                    ]
                                }
                            },
                        }
                    }
                    if entry
                    else {}
                )
                return
            self.send_error(404)

        def log_message(self, *args):
            return

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    cfg = {
        "browser-protocol": {
            "label": "接口测试生成器",
            "model": "fixture-no-inference",
            "base_url": f"http://127.0.0.1:{http.server_port}",
            "workflow": {"1": {"inputs": {"text": "", "seed": 0}}, "90": {"inputs": {}}},
            "text_input": ["1", "text"],
            "seed_input": ["1", "seed"],
            "output_node": "90",
        }
    }
    cfg["still-protocol"] = {
        **cfg["browser-protocol"],
        "label": "接口测试关键帧",
        "output_kind": "image",
        "workflow": {"1": {"inputs": {"text": "", "seed": 0}}, "90": {"inputs": {"image": True}}},
    }
    cfg["reference-protocol"] = {
        **cfg["browser-protocol"],
        "label": "参考图视频接口",
        "image_input": ["2", "image"],
        "workflow": {
            "1": {"inputs": {"text": "", "seed": 0}},
            "2": {"class_type": "LoadImage", "inputs": {"image": ""}},
            "90": {"inputs": {"frames": ["2", 0]}},
        },
    }
    try:
        yield {"AI_SHIP_COMFY_PROFILES": json.dumps(cfg)}, issued
    finally:
        http.shutdown()
        http.server_close()
        worker.join()
