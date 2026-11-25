"""Build the product acceptance crosswalk from the immutable uploaded HTML and implementation audit."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/AI_Video_Agent_Atlas.html"
MACHINE = ROOT / "contracts/feature-workflows.json"
REPORT = ROOT / "docs/feature-workflows.md"


def read_sources():
    source = SOURCE.read_bytes()
    data = re.search(
        rb'<script\s+type="application/json"\s+id="atlas-data">(.*?)</script>', source, re.S
    )
    if not data:
        raise ValueError("缺少 HTML 原始 atlas-data")
    atlas = json.loads(data[1])
    contract = json.loads((ROOT / "contracts/implementation.json").read_bytes())
    assert atlas == json.loads((ROOT / "contracts/atlas.json").read_bytes())
    assert hashlib.sha256(source).hexdigest() == contract["source_html_sha256"]
    text = source.decode("utf-8")
    declaration = re.search(r"const GROUPS=\[(.*?)\n\];", text, re.S)
    assert declaration, "原文缺少顶层分区"
    groups = [
        {"id": gid, "label": label, "modules": ast.literal_eval(modules)}
        for gid, label, modules in re.findall(
            r"\{id:'([^']+)',label:'([^']+)'[^}]*?modules:(\[[^]]*\])", declaration[1]
        )
    ]
    for index, group in enumerate(groups):
        group["index"] = index
    members = [mid for group in groups for mid in group["modules"]]
    assert (
        len(groups) == len({g["id"] for g in groups}) == 8
        and len(set(members)) == len(members) == len(atlas["modules"]) == 23
    )
    assert set(members) == {m["id"] for m in atlas["modules"]}
    web = re.search(r"const GROUPS\s*=\s*(\[.*?\]);", (ROOT / "web/app.js").read_text(), re.S)
    assert web and ast.literal_eval(web[1]) == [
        [g["label"], g["modules"]] for g in groups
    ], "产品分区与原始 HTML 不一致"
    return atlas, contract, groups


def module_view(module, features, module_index, audited, group):
    result = {
        "id": module["id"],
        "group_id": group["id"],
        "group_label": group["label"],
        "group_index": group["index"],
        "module_index_in_group": group["modules"].index(module["id"]),
        "label": module["label"],
        "source_pointer": f"/modules/{module_index}",
        "required_operation": module["engineeringContract"]["uiOperation"],
        "expected_events": module["engineeringContract"]["emittedEvent"],
        "required_acceptance": module["engineeringContract"]["acceptance"],
        "features": [],
    }
    for index, feature in features:
        fid = feature["id"]
        implementation = audited[fid]
        assert (
            implementation["module"] == module["id"] and implementation["label"] == feature["label"]
        )
        execution = implementation["execution"]
        result["features"].append(
            {
                "id": fid,
                "label": feature["label"],
                "goal": feature["goal"],
                "source_pointer": f"/features/{index}",
                "workspace": {
                    "partition": group["label"],
                    "module": module["label"],
                    "select": feature["label"],
                    "location": "#module/" + module["id"] + "/" + fid,
                },
                "source_inputs": feature["inputs"],
                "source_outputs": feature["outputs"],
                "source_routes": [
                    {
                        "id": route["id"],
                        "label": route["label"],
                        "suggested_when": route.get("suggestedWhen"),
                        "directly_selectable_in_source": route.get("directSelection", False),
                        "registered_local_workflow_handler": route["id"]
                        in execution["registered_workflow_routes"],
                        "source_pointer": f"/features/{index}/routes/{ri}",
                        "source_fields_missing": [
                            field for field in ("preserves", "recovery") if not route.get(field)
                        ],
                    }
                    for ri, route in enumerate(feature["routes"])
                ],
                "source_steps": feature["operation"]["steps"],
                "implementation_steps": [s["action"] for s in feature["implementation"]["steps"]],
                "source_controls": [
                    {"key": c["key"], "label": c["label"], "type": c["type"]}
                    for c in feature["operation"]["controls"]
                ],
                "source_quality_gates": feature["operation"]["qualityGates"],
                "source_failure_paths": feature["operation"]["failurePaths"],
                "state_machines": {
                    "execution": feature["executionMachine"],
                    "domain": feature["domainMachine"],
                },
                "data_dependencies": [
                    {"feature": c["featureId"], "direction": c["direction"], "edge": c["edgeId"]}
                    for c in feature["operation"]["connections"]
                ],
                "delivered": {
                    "status": implementation["status"],
                    "label": implementation["status_label"],
                    "operable_scope": implementation["implemented_subset"],
                    "limits": implementation["remaining_scope"],
                    "direct_endpoints": execution["direct_api_endpoints"],
                    "commands": [
                        command["command"] for command in execution["control_plane_commands"]
                    ],
                    "code": implementation["code"],
                    "test_definitions": implementation["test_definitions"],
                    **(
                        {
                            "verification_runs": [
                                {
                                    "id": r["id"],
                                    "path": r["path"],
                                    "sha256": r["sha256"],
                                    "checks": r["checks"],
                                }
                                for r in implementation["verification_runs"]
                            ]
                        }
                        if implementation["verification_runs"]
                        else {}
                    ),
                    "full_business_acceptance": implementation["full_business_acceptance"],
                },
            }
        )
    return result


def build():
    atlas, contract, groups = read_sources()
    audited = {f["id"]: f for f in contract["features"]}
    source_ids = [feature["id"] for feature in atlas["features"]]
    assert len(audited) == len(source_ids) == len(set(source_ids)) == 178 and set(audited) == set(
        source_ids
    )
    modules = [
        module_view(
            module,
            [(i, f) for i, f in enumerate(atlas["features"]) if f["module"] == module["id"]],
            mi,
            audited,
            next(g for g in groups if module["id"] in g["modules"]),
        )
        for mi, module in enumerate(atlas["modules"])
    ]
    assert len(modules) == 23 and sum(len(m["features"]) for m in modules) == 178
    return {
        "format": "atlas-feature-workflows-1",
        "source": "docs/AI_Video_Agent_Atlas.html#atlas-data",
        "source_sha256": contract["source_html_sha256"],
        "source_pointer_base": "原 HTML 的 atlas-data 所含 JSON，同值副本为 contracts/atlas.json",
        "reading": "source 为原文操作、合同与门槛。delivered 为实际可用范围及边界。注册工作流处理器只表示特定子集可编排。分区入口不表示每个选项均有执行器。",
        "groups": groups,
        "modules": modules,
    }


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(report):
    lines = [
        "# 逐分区操作与交付核对",
        "",
        "原始 HTML 将 23 个模块归入 8 个顶层分区。此索引保留原文分区名称和归属。每个功能先列原文要求的操作与路线，再列可实际执行的工作和剩余条件。正文供验收人员核对，产品面向操作者的界面另行实现。",
        "",
        "机器清单位于 [contracts/feature-workflows.json](../contracts/feature-workflows.json)。每项含原文 JSON 指针、输入输出、逐控件类型、质量门槛、失败恢复、上下游关系、状态机，以及已实现接口、命令和测试定义。原文路线上写有可选条件时，点击和保存配置本身不足以验收该路线。",
        "",
        "## 分区索引",
        "",
        "| 分区 | 模块 | 功能数 | 原文操作 |",
        "| --- | --- | ---: | --- |",
    ]
    for module in sorted(
        report["modules"], key=lambda m: (m["group_index"], m["module_index_in_group"])
    ):
        lines.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    (
                        module["group_label"],
                        module["label"],
                        len(module["features"]),
                        module["required_operation"],
                    ),
                )
            )
            + " |"
        )
    for module in sorted(
        report["modules"], key=lambda m: (m["group_index"], m["module_index_in_group"])
    ):
        lines += [
            "",
            "## " + module["label"],
            "",
            f"原文要求验收的结果为“{module['required_acceptance']}”",
            "",
        ]
        for feature in module["features"]:
            status = feature["delivered"]
            lines.extend(
                [
                    f"### {feature['id']} {feature['label']}",
                    "",
                    f"**原文操作**　{' → '.join(feature['source_steps'])}",
                    "",
                    f"**可选路线**　{' ／ '.join(r['label'] for r in feature['source_routes'])}",
                    "",
                    f"**数据流转**　{'、'.join(feature['source_inputs']) or '按入口录入'} → {'、'.join(feature['source_outputs']) or '按路线取用'}",
                    "",
                    f"**当前能做的事**　{status['label']}。{status['operable_scope']}",
                    "",
                    f"**待验收范围**　{status['limits']}",
                    *(
                        [
                            "",
                            "**实测凭据**　"
                            + " · ".join(
                                f"[{', '.join(c['criterion'] for c in r['checks'])}](../{r['path']})"
                                for r in status["verification_runs"]
                            ),
                        ]
                        if status.get("verification_runs")
                        else []
                    ),
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build()
    for file, content in [
        (MACHINE, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (REPORT, render(report)),
    ]:
        if args.check:
            if not file.is_file() or file.read_text() != content:
                raise SystemExit(f"逐功能核对表与源文件不一致: {file.name}")
        else:
            file.write_text(content)
    features = [f for m in report["modules"] for f in m["features"]]
    counts = {
        k: sum(len(f[k]) for f in features)
        for k in [
            "source_routes",
            "source_controls",
            "source_steps",
            "source_quality_gates",
            "source_failure_paths",
        ]
    }
    print(
        json.dumps(
            {
                "groups": len(report["groups"]),
                "modules": len(report["modules"]),
                "features": len(features),
                **counts,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
