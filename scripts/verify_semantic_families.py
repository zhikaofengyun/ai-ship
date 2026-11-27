"""Import three known real images/videos and one derivative, index, group and retire by HTTP."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_production import ProductionBase


def run(source, output):
    known = json.loads((ROOT / "evidence/material-intake/result.json").read_text())["sources"]
    case = ProductionBase()
    case.setUp()
    try:

        def post(path, body):
            r = case.client.post(
                "/api/v1/production/" + path, json={"project_id": case.pid, **body}
            )
            r.raise_for_status()
            return r.json()["data"]

        with tempfile.TemporaryDirectory() as tmp:
            moving = Path(tmp) / "coffee-pan.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-loop",
                    "1",
                    "-framerate",
                    "12",
                    "-i",
                    str(source / "coffee.png"),
                    "-t",
                    "2",
                    "-vf",
                    "crop=480:320:20+20*t:30,scale=256:170",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-threads",
                    "1",
                    str(moving),
                ],
                check=True,
            )
            versions = {}
            for path in [*(source / k for k in known), moving]:
                blob = path.read_bytes()
                dg = hashlib.sha256(blob).hexdigest()
                if path.name in known:
                    assert known[path.name]["sha256"] == dg
                result = case.client.post(
                    "/api/v1/media/import",
                    params={"project_id": case.pid, "filename": path.name},
                    content=blob,
                )
                result.raise_for_status()
                r = result.json()["data"]["version"]
                versions[r["id"]] = {
                    "id": r["id"],
                    "version": r["version"],
                    "filename": path.name,
                    "file_sha256": dg,
                }
            indexed = post("asset-index/frames/build", {"limit": 12})
            assert indexed["processed"] == len(versions) and not indexed["errors"], indexed
            pin = lambda v: {"id": v["id"], "version": v["version"]}
            audit = post(
                "asset-families/suggest",
                {
                    "request_id": uuid4().hex,
                    "members": [pin(v) for v in versions.values()],
                    "threshold": 0.80,
                },
            )["audit"]
            d = audit["data"]
            assert len(d["groups"]) == 1 and len(d["unmatched"]) == 2, d
            group = d["groups"][0]
            assert {versions[k]["filename"] for k in group["members"]} == {
                "coffee.png",
                "coffee-pan.mp4",
            }
            accept = post(
                "asset-families/group",
                {
                    "request_id": uuid4().hex,
                    "mode": "review_clip",
                    "members": [pin(versions[k]) for k in group["members"]],
                    "name": "咖啡杯照片与取景视频",
                    "reason": "复核用例确认咖啡杯、托盘和木桌画面",
                    "audit": pin(audit),
                },
            )["cluster"]
            retire = post(
                "asset-families/group",
                {
                    "request_id": uuid4().hex,
                    "mode": "retire",
                    "name": "撤销复核样例",
                    "reason": "验证可逆操作",
                    "group": pin(accept),
                },
            )["cluster"]
            assert (
                retire["id"] == accept["id"]
                and retire["version"] == accept["version"] + 1
                and retire["data"]["status"] == "retired"
            )
            report = {
                "inputs": versions,
                "sources": known,
                "index": indexed,
                "audit": d,
                "audit_pin": pin(audit),
                "confirmed": pin(accept),
                "retired": pin(retire),
            }
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    finally:
        case.tearDown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.sources, args.output)
