from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
ATLAS_PATH = ROOT / "contracts" / "atlas.json"
ATLAS_BYTES = ATLAS_PATH.read_bytes()
ATLAS = json.loads(ATLAS_BYTES)
SOURCE_SHA256 = hashlib.sha256(ATLAS_BYTES).hexdigest()
FEATURES = {f["id"]: f for f in ATLAS["features"]}
ENTITIES = {e["id"]: e for e in ATLAS["entities"]}
MACHINES = {m["id"]: m for m in ATLAS["machines"]}
MODULES = {m["id"]: m for m in ATLAS["modules"]}
COUNTS = {
    "modules": len(MODULES),
    "features": len(FEATURES),
    "entities": len(ENTITIES),
    "machines": len(MACHINES),
    "routes": sum(len(f["routes"]) for f in FEATURES.values()),
    "controls": sum(len(f.get("operation", {}).get("controls", [])) for f in FEATURES.values()),
    "edges": len(ATLAS["edges"]),
    "requirements": len(ATLAS["requirements"]),
    "scenarios": len(ATLAS["scenarios"]),
}


def catalog():
    return {
        **ATLAS,
        "capabilities": ATLAS["platformCapabilities"],
        "counts": COUNTS,
        "source_sha256": SOURCE_SHA256,
        "schema_version": ATLAS["schemaVersion"],
        "as_of": ATLAS["meta"]["asOf"],
        "boundary": "目录完整保留原 HTML；功能实现状态由实现清单独立声明，目录覆盖不代表已完成真实执行。",
    }


def workspace_catalog():
    """Exact UI-relevant fields from the same authoritative Atlas as the full catalog."""
    return {
        "modules": [
            {k: module[k] for k in ("id", "label", "scope")} for module in ATLAS["modules"]
        ],
        "features": [
            {
                **{
                    k: feature[k]
                    for k in (
                        "id",
                        "module",
                        "label",
                        "goal",
                        "inputs",
                        "outputs",
                        "parameters",
                        "checks",
                        "failure",
                    )
                },
                "routes": [{k: route[k] for k in ("id", "label")} for route in feature["routes"]],
                "operation": {
                    k: feature["operation"][k] for k in ("inputs", "outputs", "controls")
                },
            }
            for feature in ATLAS["features"]
        ],
        "entities": ATLAS["entities"],
        "agentSystem": {"roles": ATLAS["agentSystem"]["roles"]},
        "counts": COUNTS,
        "schema_version": ATLAS["schemaVersion"],
        "source_sha256": SOURCE_SHA256,
    }
