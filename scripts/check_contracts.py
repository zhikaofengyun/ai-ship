"""Exhaustive source/contract integrity checks, explicitly not business completion."""

from pathlib import Path
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.catalog import ATLAS, FEATURES, ENTITIES, MACHINES
from app.schemas import SCHEMAS
from jsonschema import Draft202012Validator
from scripts.build_implementation_manifest import build_manifest, EXPECTED_COUNTS, STATUS_IDS


def check():
    source = ROOT / "docs" / "AI_Video_Agent_Atlas.html"
    match = re.search(
        r'<script\s+type="application/json"\s+id="atlas-data">(.*?)</script>',
        source.read_text(),
        re.S,
    )
    if not match:
        raise AssertionError("No source atlas-data")
    assert json.loads(match[1]) == ATLAS, "Source JSON drift"
    modules = {m["id"] for m in ATLAS["modules"]}
    sources = {s["id"] for s in ATLAS["sources"]}
    requirements = {r["id"] for r in ATLAS["requirements"]}
    counts = {
        "modules": len(modules),
        "features": len(FEATURES),
        "entities": len(ENTITIES),
        "machines": len(MACHINES),
        "routes": 0,
        "controls": 0,
        "quality_gates": 0,
        "failure_paths": 0,
        "states": 0,
        "transitions": 0,
    }
    assert (
        counts["modules"] == 23
        and counts["features"] == 178
        and counts["entities"] == 64
        and counts["machines"] == 12
    )
    source_gaps = []
    for f in FEATURES.values():
        assert f["module"] in modules
        assert set(f["inputs"] + f["outputs"]) <= set(ENTITIES)
        assert set(f["sources"]) <= sources
        assert set(f["requirements"]) <= requirements
        assert f["executionMachine"] in MACHINES
        assert len({r["id"] for r in f["routes"]}) == len(f["routes"])
        for route in f["routes"]:
            assert route["completionRule"]
            missing = [key for key in ("preserves", "recovery") if not route.get(key)]
            if missing:
                source_gaps.append({"route_id": route["id"], "missing_source_fields": missing})
        controls = f["operation"]["controls"]
        assert len({c["key"] for c in controls}) == len(controls)
        for control in controls:
            assert control["type"] and control["label"] and control["scope"] and control["impact"]
        counts["routes"] += len(f["routes"])
        counts["controls"] += len(controls)
        counts["quality_gates"] += len(f["operation"]["qualityGates"])
        counts["failure_paths"] += len(f["operation"]["failurePaths"])
    for edge in ATLAS["edges"]:
        assert edge["from"] in FEATURES and edge["to"] in FEATURES
    for kind, entity in ENTITIES.items():
        assert kind in SCHEMAS
        Draft202012Validator.check_schema(SCHEMAS[kind])
        for relation in entity["relations"]:
            assert relation["target"] in ENTITIES
        path = ROOT / "contracts" / "entities" / f"{kind}.schema.json"
        assert path.exists(), str(path)
        assert json.loads(path.read_text()) == SCHEMAS[kind], f"Generated schema stale: {kind}"
    for machine in MACHINES.values():
        assert machine["initial"] in machine["states"]
        assert set(machine["terminal"]) <= set(machine["states"])
        counts["states"] += len(machine["states"])
        for edge in machine["edges"]:
            assert edge["from"] in machine["states"] and edge["to"] in machine["states"]
            assert edge["guard"] and edge["audit"]
            counts["transitions"] += 1
    assert counts["routes"] == 534 and counts["controls"] == 742 and counts["transitions"] == 285
    implementation_path = ROOT / "contracts" / "implementation.json"
    implementation_counts = None
    if implementation_path.exists():
        implementation = json.loads(implementation_path.read_text())
        assert (
            implementation == build_manifest()
        ), "Implementation manifest stale or unsupported claim: run scripts/build_implementation_manifest.py"
        assert implementation["schema_version"] == "implementation-2"
        assert (
            len(implementation["features"])
            == len({f["id"] for f in implementation["features"]})
            == 178
        )
        assert {f["id"] for f in implementation["features"]} == set(FEATURES)
        assert implementation["counts"]["by_status"] == EXPECTED_COUNTS
        implementation_counts = implementation["counts"]
        for f in implementation["features"]:
            assert f["id"] in STATUS_IDS[f["status"]]
            for flag in (
                "full_business_acceptance",
                "production_verified",
                "external_live_verified",
            ):
                assert f[flag] == any(
                    r["current"] for r in f.get("acceptance_reviews", {}).get(flag, [])
                ), "Acceptance must derive from an independent current-runtime decision"
            assert f["source_route_ids"] == [r["id"] for r in FEATURES[f["id"]]["routes"]]
            assert f["source_quality_gates"] == FEATURES[f["id"]]["operation"]["qualityGates"]
            assert f["contract_request"]["executes_business_work"] is False
            assert set(f["execution"]["registered_workflow_routes"]) <= set(f["source_route_ids"])
            for path in f.get("code", []):
                assert (ROOT / path).is_file(), f"Mapped code missing: {path}"
        matrix_rows = {}
        for line in (ROOT / "docs/implementation-matrix.md").read_text().splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 6 and re.fullmatch(r"[A-Z]\d\d", cells[0]):
                assert cells[0] not in matrix_rows, "Duplicate implementation matrix row"
                matrix_rows[cells[0]] = cells[2]
        assert matrix_rows == {
            f["id"]: f["status_label"] for f in implementation["features"]
        }, "Markdown/JSON classification drift"
    from scripts.feature_workflow_audit import build as build_workflows, render as render_workflows

    workflows = build_workflows()
    assert json.loads((ROOT / "contracts/feature-workflows.json").read_text()) == workflows
    assert (ROOT / "docs/feature-workflows.md").read_text() == render_workflows(workflows)
    assert len(workflows["groups"]) == 8
    return {
        "passed": True,
        "source_navigation_groups": len(workflows["groups"]),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_roundtrip_equal": True,
        "counts": counts,
        "missing_references": [],
        "source_gaps": source_gaps,
        "implementation_counts": implementation_counts,
        "boundary": "全部源定义与工程映射结构检查；不证明所有业务路线或外部接入已实现。",
    }


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
