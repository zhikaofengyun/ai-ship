"""A local ComfyUI protocol fixture returns an existing synthetic MP4. This is not model inference."""

from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from uuid import uuid4
from tests.test_production import ProductionBase


class GenerationTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.base = "/api/v1/production/generation"
        path = Path(self.temp.name) / "fixture.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x120:rate=25:duration=1",
                "-threads",
                "1",
                "-c:v",
                "libx264",
                str(path),
            ],
            check=True,
        )
        self.video = path.read_bytes()
        self.job = None
        self.status = "pending"
        self.lost = False
        self.submits = 0
        self.paths = []
        self.input_files = {}
        self.reference_fault = None
        self.on_upload = None
        self.output = "candidate.mp4"
        t = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body, raw=False):
                if not raw:
                    body = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                t.paths.append(self.path)
                if self.path == "/upload/image":
                    parts = BytesParser(policy=policy.default).parsebytes(
                        b"Content-Type: "
                        + self.headers["Content-Type"].encode()
                        + b"\r\n\r\n"
                        + body
                    )
                    form = {
                        part.get_param("name", header="Content-Disposition"): part
                        for part in parts.iter_parts()
                    }
                    name = form["image"].get_filename()
                    image = form["image"].get_payload(decode=True)
                    assert form["type"].get_payload(decode=True) == b"input"
                    assert form["overwrite"].get_payload(decode=True) == b"false"
                    assert form["image"].get_content_type() == "image/png"
                    t.input_files[name] = image if t.reference_fault != "wrong_bytes" else b"other"
                    if t.on_upload:
                        t.on_upload()
                    if t.reference_fault == "lost_ack":
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.reply(
                        {
                            "name": name,
                            "subfolder": "elsewhere" if t.reference_fault == "descriptor" else "",
                            "type": "input",
                        }
                    )
                    return
                req = json.loads(body)
                if self.path == "/prompt":
                    t.submits += 1
                    t.job = req
                    if t.lost:
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.reply({"prompt_id": req["prompt_id"]})
                elif self.path == "/queue":
                    if req.get("delete") == [t.job["prompt_id"]] and t.status == "pending":
                        t.status = "absent"
                    self.reply({})
                else:
                    self.send_error(404)

            def do_GET(self):
                t.paths.append(self.path)
                rid = t.job["prompt_id"] if t.job else None
                row = (
                    [0, rid, t.job["prompt"], {"client_id": t.job["client_id"]}, ["3"]]
                    if t.job
                    else None
                )
                if self.path == "/queue":
                    self.reply(
                        {
                            "queue_pending": [row] if t.status == "pending" else [],
                            "queue_running": [row] if t.status == "running" else [],
                        }
                    )
                elif self.path.startswith("/history"):
                    self.reply(
                        {
                            rid: {
                                "prompt": row,
                                "status": {"completed": True, "status_str": "success"},
                                "outputs": {
                                    "3": {
                                        "videos": [
                                            {
                                                "filename": t.output,
                                                "subfolder": "",
                                                "type": "output",
                                            }
                                        ]
                                    }
                                },
                            }
                        }
                        if t.status == "done"
                        else {}
                    )
                elif self.path.startswith("/view?"):
                    params = parse_qs(urlsplit(self.path).query)
                    if params.get("type") == ["input"]:
                        self.reply(t.input_files[params["filename"][0]], True)
                    else:
                        self.reply(t.video, True)
                else:
                    self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.cfg = {
            "base_url": f"http://127.0.0.1:{self.server.server_port}",
            "label": "Test transport",
            "model": "fixture",
            "workflow": {
                "1": {"class_type": "Text", "inputs": {"text": ""}},
                "2": {"class_type": "Sampler", "inputs": {"seed": 0}},
                "3": {"class_type": "Video", "inputs": {}},
            },
            "text_input": ["1", "text"],
            "seed_input": ["2", "seed"],
            "output_node": "3",
        }
        self.env2 = patch.dict(
            os.environ, {"AI_SHIP_COMFY_PROFILES": json.dumps({"demo": self.cfg})}
        )
        self.env2.start()
        self.addCleanup(self.env2.stop)
        self.body = {
            "project_id": self.pid,
            "profile_id": "demo",
            "prompt": "Blue background",
            "seed": 99,
            "slot": "镜头 2",
            "idempotency_key": uuid4().hex,
        }

    def post(self, suffix="", data=None, status=200):
        reply = self.client.post(self.base + suffix, json=data)
        self.assertEqual(reply.status_code, status, reply.text)
        return reply.json().get("data") or reply.json()["error"]

    def test_queue_download_probe_generated_origin_and_deduplicated_adoption(self):
        profiles = self.client.get(self.base + "/profiles").json()["data"]["profiles"]
        self.assertEqual(profiles[0]["status"], "registered_not_probed")
        gen = self.post(data=self.body)
        rid = gen["job"]["id"]
        self.assertEqual(gen["job"]["data"]["stage"], "queued")
        self.assertEqual(self.job["prompt"]["1"]["inputs"]["text"], self.body["prompt"])
        self.assertEqual(self.job["prompt"]["2"]["inputs"]["seed"], 99)
        self.assertIsNone(gen["asset"])
        self.post(data=self.body)
        self.post(data={**self.body, "prompt": "Changed"}, status=409)
        self.assertEqual(
            self.post(data={**self.body, "idempotency_key": uuid4().hex}, status=409)["code"],
            "GENERATION_RECONCILE_FIRST",
        )
        self.assertEqual(self.submits, 1)
        self.status = "running"
        self.assertEqual(self.post("/" + rid + "/refresh")["job"]["data"]["stage"], "running")
        self.status = "done"
        ready = self.post("/" + rid + "/refresh")
        self.assertEqual(ready["job"]["data"]["stage"], "ready", ready["job"]["data"]["issue"])
        self.assertTrue(ready["job"]["data"]["technical_qc"]["checks"]["full_decode"])
        asset = ready["asset"]
        source = self.store.get(asset["data"]["source_id"], "local")
        self.assertEqual(source["data"]["kind"], "generated_candidate")
        self.assertEqual(source["data"]["generation_call_ref"]["id"], rid)
        self.assertEqual(self.post("/" + rid + "/refresh")["asset"]["id"], asset["id"])
        self.assertEqual(len(self.store.list("local", kind="Asset")), 1)
        self.assertEqual(len(self.store.list("local", kind="Cost")), 1)
        resp = self.client.post(
            "/api/v1/production/auto-edits",
            json={
                "project_id": self.pid,
                "asset_ids": [asset["id"]],
                "goal": "Cut the generated clip",
                "mode": "local_vision",
                "target_seconds": 1,
                "idempotency_key": uuid4().hex,
            },
        )
        self.assertEqual(resp.json()["data"]["session"]["data"]["stage"], "review")
        self.assertEqual(
            self.client.get(self.base, params={"project_id": self.pid}).json()["data"]["jobs"][0][
                "id"
            ],
            rid,
        )

    def test_lost_ack_reconciles_original_queue_and_never_interrupts_other_jobs(self):
        self.lost = True
        gen = self.post(data=self.body)
        rid = gen["job"]["id"]
        self.assertEqual(gen["job"]["data"]["stage"], "outcome_unknown")
        self.post(data=self.body)
        self.assertEqual(self.post("/" + rid + "/refresh")["job"]["data"]["stage"], "queued")
        self.assertEqual(self.post("/" + rid + "/cancel")["job"]["data"]["stage"], "cancelled")
        self.assertNotIn("/interrupt", self.paths)
        self.assertEqual(self.submits, 1)

    def test_running_cancel_preserves_actual_terminal_and_rejects_unverified_video(self):
        rid = self.post(data=self.body)["job"]["id"]
        self.status = "running"
        self.assertEqual(
            self.post("/" + rid + "/cancel")["job"]["data"]["stage"], "cancel_requested"
        )
        self.status = "done"
        cancelled = self.post("/" + rid + "/refresh")
        self.assertEqual(cancelled["job"]["data"]["provider_terminal"], "completed_after_cancel")
        self.assertIsNone(cancelled["asset"])
        b = {**self.body, "idempotency_key": uuid4().hex, "seed": 100}
        rid = self.post(data=b)["job"]["id"]
        self.video = b"fake h264"
        fail = self.post("/" + rid + "/refresh")
        self.assertEqual(fail["job"]["data"]["stage"], "failed")
        self.assertEqual(self.store.list("local", kind="Asset"), [])

    def test_unregistered_config_changed_and_output_path_bounds(self):
        self.assertEqual(
            self.post(data={**self.body, "profile_id": "other"}, status=503)["code"],
            "GENERATOR_UNAVAILABLE",
        )
        rid = self.post(data=self.body)["job"]["id"]
        with patch.dict(
            os.environ,
            {"AI_SHIP_COMFY_PROFILES": json.dumps({"demo": {**self.cfg, "model": "different"}})},
        ):
            self.assertEqual(
                self.post("/" + rid + "/refresh", status=409)["code"], "GENERATOR_PROFILE_CHANGED"
            )
        self.status = "done"
        self.output = "../another.mp4"
        r = self.post("/" + rid + "/refresh")
        self.assertNotEqual(r["job"]["data"]["stage"], "ready")
        self.assertFalse(any(path.startswith("/view") for path in self.paths))

    def test_verified_generated_image_stays_distinct_from_explicit_timed_still_and_is_reusable(
        self,
    ):
        image = Path(self.temp.name) / "exact.png"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=164x100",
                "-frames:v",
                "1",
                "-threads",
                "1",
                str(image),
            ],
            check=True,
        )
        old_video = self.video
        self.video = image.read_bytes()
        self.output = "exact.png"
        self.cfg["output_kind"] = "image"
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps({"demo": self.cfg})
        job = self.post(data=self.body)["job"]
        rid = job["id"]
        self.status = "done"
        ready = self.post("/" + rid + "/refresh")
        source = ready["asset"]
        self.assertEqual(ready["job"]["data"]["output_kind"], "image")
        self.assertTrue(source["data"]["probe"]["is_still"])
        self.assertEqual(source["data"]["media_type"], "image/png")
        self.assertTrue(ready["job"]["data"]["technical_qc"]["checks"]["single_frame"])
        held = self.post("/" + rid + "/hold", {"duration": 1.2, "fps": 25})
        self.assertNotEqual(held["asset"]["id"], source["id"])
        self.assertFalse(held["asset"]["data"]["probe"]["is_still"])
        self.assertEqual(held["job"]["data"]["asset_ref"]["id"], source["id"])
        self.assertEqual(held["job"]["data"]["hold_params"]["frames"], 30)
        self.assertAlmostEqual(held["asset"]["data"]["probe"]["duration"], 1.2, places=2)
        lineage = self.store.get(held["asset"]["data"]["source_id"], "local")
        self.assertEqual(lineage["data"]["kind"], "timed_still_derivative")
        self.assertEqual(lineage["data"]["still_source_ref"]["id"], source["id"])
        again = self.post("/" + rid + "/hold", {"duration": 1.2, "fps": 25})
        self.assertEqual(held["asset"]["id"], again["asset"]["id"])
        self.assertEqual(len(self.store.list("local", kind="TransformRun")), 1)
        # A video disguised as a PNG is not admitted under the still profile.
        self.video = old_video
        second = self.post(data={**self.body, "seed": 19, "idempotency_key": uuid4().hex})["job"]
        blocked = self.post("/" + second["id"] + "/refresh")
        self.assertEqual(blocked["job"]["data"]["stage"], "failed")
        self.assertIsNone(blocked["asset"])

    def reference_setup(self):
        self.cfg["image_input"] = ["4", "image"]
        self.cfg["workflow"]["4"] = {
            "class_type": "LoadImage",
            "inputs": {"image": "administrator-default.png"},
        }
        self.cfg["workflow"]["3"]["inputs"]["frames"] = ["4", 0]
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps({"demo": self.cfg})
        still = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=orange:size=128x80",
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-c:v",
                "mjpeg",
                "-f",
                "image2pipe",
                "pipe:1",
            ]
        )
        res = self.client.post(
            "/api/v1/media/import",
            params={"project_id": self.pid, "filename": "customer-reference.jpg"},
            content=still,
        )
        self.assertEqual(res.status_code, 200, res.text)
        asset = res.json()["data"]["asset"]
        return asset, {
            **self.body,
            "reference_asset_id": asset["id"],
            "reference_asset_version": asset["version"],
        }

    def test_reference_uploaded_read_back_bound_to_graph_and_generated_lineage(self):
        asset, body = self.reference_setup()
        self.assertTrue(
            self.client.get(self.base + "/profiles").json()["data"]["profiles"][0][
                "requires_reference"
            ]
        )
        gen = self.post(data=body)["job"]
        self.assertEqual(gen["data"]["stage"], "queued")
        info = gen["data"]["reference_input"]
        name = info["filename"]
        self.assertEqual(self.job["prompt"]["4"]["inputs"]["image"], name)
        self.assertNotIn("customer", name)
        self.assertEqual(info["png_sha256"], hashlib.sha256(self.input_files[name]).hexdigest())
        self.assertTrue(self.input_files[name].startswith(bytes.fromhex("89504e470d0a1a0a")))
        self.assertNotEqual(info["original_sha256"], info["png_sha256"])
        self.assertEqual([info["width"], info["height"]], [128, 80])
        self.assertTrue(
            any((r["id"], r["version"]) == (asset["id"], asset["version"]) for r in gen["refs"])
        )
        self.assertEqual(gen["data"]["reference_upload"]["verified_sha256"], info["png_sha256"])
        self.assertEqual(self.post(data=body)["job"]["id"], gen["id"])
        self.assertEqual(
            self.post(data={**body, "idempotency_key": uuid4().hex}, status=409)["code"],
            "GENERATION_RECONCILE_FIRST",
        )
        self.assertEqual(self.paths.count("/upload/image"), 1)
        self.assertEqual(self.submits, 1)
        self.status = "done"
        ready = self.post("/" + gen["id"] + "/refresh")
        self.assertEqual(ready["job"]["data"]["stage"], "ready")
        source = self.store.get(ready["asset"]["data"]["source_id"], "local")
        self.assertTrue(any(r["id"] == gen["id"] for r in source["refs"]))
        self.assertEqual(gen["data"]["reference_asset_ref"]["id"], asset["id"])

    def test_reference_scope_wrong_kind_stale_missing_disconnected_and_unsupported(self):
        asset, body = self.reference_setup()
        self.assertEqual(self.post(data=self.body, status=422)["code"], "REFERENCE_REQUIRED")
        self.assertEqual(
            self.post(data={**body, "reference_asset_version": 2}, status=409)["code"],
            "REFERENCE_REVISION_CHANGED",
        )
        self.assertEqual(
            self.post(data={**body, "reference_asset_id": "missing"}, status=404)["code"],
            "NOT_FOUND",
        )
        project = self.client.post("/api/v1/projects", json={"name": "other"}).json()["data"]
        self.assertEqual(
            self.post(data={**body, "project_id": project["id"]}, status=403)["code"],
            "PROJECT_SCOPE_MISMATCH",
        )
        movie = self.client.post(
            "/api/v1/media/import",
            params={"project_id": self.pid, "filename": "source.mp4"},
            content=self.video,
        ).json()["data"]["asset"]
        self.assertEqual(
            self.post(data={**body, "reference_asset_id": movie["id"]}, status=422)["code"],
            "REFERENCE_IMAGE_REQUIRED",
        )
        self.cfg["workflow"]["3"]["inputs"] = {}
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps({"demo": self.cfg})
        self.assertEqual(self.post(data=body, status=503)["code"], "GENERATOR_CONFIG_INVALID")
        self.cfg.pop("image_input")
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps({"demo": self.cfg})
        self.assertFalse(
            self.client.get(self.base + "/profiles").json()["data"]["profiles"][0][
                "requires_reference"
            ]
        )
        self.assertEqual(self.post(data=body, status=422)["code"], "REFERENCE_UNSUPPORTED")
        self.assertEqual(self.submits, 0)
        self.assertEqual(self.paths, [])

    def test_reference_upload_failures_never_submit_or_charge(self):
        _, body = self.reference_setup()
        for fault in ("descriptor", "wrong_bytes", "lost_ack"):
            with self.subTest(fault=fault):
                self.reference_fault = fault
                msg = {**body, "idempotency_key": uuid4().hex}
                job = self.post(data=msg)["job"]
                self.assertEqual(job["data"]["stage"], "failed")
                self.assertEqual(job["data"]["issue"]["code"], "REFERENCE_UPLOAD_FAILED")
                self.assertFalse(job["data"]["prompt_submitted"])
                self.assertEqual(self.post(data=msg)["job"]["id"], job["id"])
        self.assertEqual(self.submits, 0)
        self.assertEqual(self.store.list("local", kind="Cost"), [])

    def test_reference_revocation_between_upload_submit_and_adoption(self):
        asset, body = self.reference_setup()

        def revoke():
            self.store.update(
                asset["data"]["source_id"],
                {"status": "revoked"},
                tenant_id="local",
                expected_revision=1,
            )

        # First accept a legitimate request, leave it running.
        queued = self.post(data=body)["job"]
        # A distinct request is revoked after uploading reference pixels but before sending the prompt.
        self.on_upload = revoke
        stopped = self.post(data={**body, "idempotency_key": uuid4().hex, "seed": 73})["job"]
        self.assertEqual(stopped["data"]["stage"], "failed")
        self.assertEqual(stopped["data"]["issue"]["code"], "MEDIA_REVOKED")
        self.assertFalse(stopped["data"]["prompt_submitted"])
        self.assertEqual(self.submits, 1)
        self.assertEqual(
            self.post(data={**body, "idempotency_key": uuid4().hex}, status=403)["code"],
            "MEDIA_REVOKED",
        )
        self.status = "done"
        denied = self.post("/" + queued["id"] + "/refresh")
        self.assertEqual(denied["job"]["data"]["stage"], "failed")
        self.assertIsNone(denied["asset"])
        self.assertEqual(self.store.list("local", kind="Cost"), [])

    def test_reference_lost_prompt_ack_reconciles_existing_exact_graph(self):
        _, body = self.reference_setup()
        self.lost = True
        unknown = self.post(data=body)["job"]
        self.assertEqual(unknown["data"]["stage"], "outcome_unknown")
        self.assertEqual(
            unknown["data"]["reference_upload"]["verified_bytes"],
            len(next(iter(self.input_files.values()))),
        )
        self.assertEqual(
            self.post(data={**body, "idempotency_key": uuid4().hex}, status=409)["code"],
            "GENERATION_RECONCILE_FIRST",
        )
        restored = self.post("/" + unknown["id"] + "/refresh")["job"]
        self.assertEqual(restored["data"]["stage"], "queued")
        self.assertEqual(
            restored["data"]["reference_asset_ref"], unknown["data"]["reference_asset_ref"]
        )
        self.assertEqual(self.submits, 1)
        self.assertEqual(self.paths.count("/upload/image"), 1)
