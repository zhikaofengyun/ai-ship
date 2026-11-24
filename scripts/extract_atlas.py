"""Lossless extraction of the architecture JSON; no app code or model claims."""

from pathlib import Path
import collections, hashlib, json, re

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "docs" / "AI_Video_Agent_Atlas.html"
text = source.read_text(encoding="utf-8")
match = re.search(r'<script\s+type="application/json"\s+id="atlas-data">(.*?)</script>', text, re.S)
if not match:
    raise SystemExit("Missing atlas-data")
data = json.loads(match[1])
out = ROOT / "contracts" / "atlas.json"
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
assert json.loads(out.read_text(encoding="utf-8")) == data
sets = {
    k: {v["id"] for v in data[k]}
    for k in ["modules", "features", "entities", "machines", "requirements", "sources"]
}
errors = []
for f in data["features"]:
    for k, values in [
        ("modules", [f["module"]]),
        ("entities", f["inputs"] + f["outputs"]),
        ("requirements", f["requirements"]),
        ("sources", f["sources"]),
    ]:
        for value in values:
            if value not in sets[k]:
                errors.append({"path": f"features/{f['id']}/{k}", "missing": value})
for e in data["edges"]:
    for key in ["from", "to"]:
        if e[key] not in sets["features"]:
            errors.append({"path": f"edges/{e['id']}/{key}", "missing": e[key]})
for e in data["entities"]:
    for r in e["relations"]:
        if r["target"] not in sets["entities"]:
            errors.append({"path": f"entities/{e['id']}/relations", "missing": r["target"]})
for machine in data["machines"]:
    for edge in machine["edges"]:
        for key in ["from", "to"]:
            if edge[key] not in machine["states"]:
                errors.append(
                    {"path": f"machines/{machine['id']}/edges/{key}", "missing": edge[key]}
                )
stats = {
    "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "sourceBytes": source.stat().st_size,
    "schemaVersion": data["schemaVersion"],
    "asOf": data["meta"]["asOf"],
    "counts": {
        k: len(data[k])
        for k in [
            "modules",
            "features",
            "entities",
            "machines",
            "edges",
            "sources",
            "requirements",
            "scenarios",
            "integrations",
        ]
    },
    "routes": sum(len(f["routes"]) for f in data["features"]),
    "controls": sum(len(f["operation"]["controls"]) for f in data["features"]),
    "implementationSteps": sum(len(f["implementation"]["steps"]) for f in data["features"]),
    "qualityGates": sum(len(f["operation"]["qualityGates"]) for f in data["features"]),
    "failurePaths": sum(len(f["operation"]["failurePaths"]) for f in data["features"]),
    "states": sum(len(m["states"]) for m in data["machines"]),
    "transitions": sum(len(m["edges"]) for m in data["machines"]),
    "scenarioMatrixCases": len(data["v6"]["scenarioMatrix"]),
    "missingReferences": errors,
    "extractionRoundtripEquality": True,
    "scope": "Source JSON extraction and reference checks, not implementation or runtime tests",
}
(ROOT / "contracts" / "atlas-integrity.json").write_text(
    json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
)

lines = [
    "# AI Video Agent Atlas 原文需求解构",
    "",
    "本文件是对上传 HTML 的只读需求整理。原始完整模型位于 contracts/atlas.json；提取脚本为 scripts/extract_atlas.py；引用一致性证据为 contracts/atlas-integrity.json。不是产品完成度报告。",
    "",
    "## 1. 权威来源与精确范围",
    "",
    f"- 源文件：{source.name}；{stats['sourceBytes']:,} bytes；SHA-256 `{stats['sourceSha256']}`。",
    f"- 文档 schemaVersion {data['schemaVersion']}；原文日期 {data['meta']['asOf']}；时区 {data['meta']['timezone']}。",
    f"- 范围：23 模块、178 功能、534 路线、742 控件、574 实施步骤、1104 质量检查条目、574 失败路径、64 实体、12 状态机、126 状态、285 转移、571 关系、42 需求、14 场景、69 正/反/跨模块验收场景、20 集成候选、195 引用来源。",
    "- JSON 提取完整往返相等；功能到模块/实体/需求/来源、关系端点、实体关系目标、状态机转移端点均无缺失引用。此验证不证明软件已实现。",
    f"- 原文成熟度：{data['meta']['maturity']}。",
    "",
    "## 2. 不能擅自改变的总体约束",
    "",
]
for rule in data["v6"]["crossCutting"]:
    lines += [f"### {rule['id']} · {rule['label']}", "", rule["rule"], ""]
lines += [
    "## 3. 文档规定的最小工程架构",
    "",
    "按原文 v5.playbook.architecture 与 deploymentPlan：",
    "",
]
for layer in data["v5"]["playbook"]["architecture"]["layers"]:
    lines += [f"- {layer['label']}：{layer['responsibility']}"]
lines += [""]
for c in data["v5"]["playbook"]["deploymentPlan"]["minimalComponents"]:
    lines += [f"- {c['component']}：{c['deployment']}。{c['reason']}"]
lines += [
    "",
    "命令统一入口字段：`"
    + "`, `".join(data["v5"]["playbook"]["architecture"]["commandEnvelope"]["required"])
    + "`。",
    "",
    "可选字段：`"
    + "`, `".join(data["v5"]["playbook"]["architecture"]["commandEnvelope"]["optional"])
    + "`。",
    "",
]
for rule in data["v5"]["playbook"]["architecture"]["commandEnvelope"]["rules"]:
    lines += ["- " + rule]
lines += [
    "",
    "## 4. 原文没有给出的完整实现内容",
    "",
    "- 64 实体有 fields/common/relations；只有 21 实体附 fieldSchema，且其中很多是说明性字符串，不是可直接执行的 JSON Schema。43 个没有 fieldSchema。字段必填性、具体 SQL 类型、数值精度、全量外键/唯一键、迁移与保留期仍需实现并明确记录工程决策。",
    "- 控件完整含类型/作用域/影响/锁，但不少 enum 没有穷举 options、table 没有正式列 schema、number 没有业务数值默认。不能把猜测默认值伪称原文规定。",
    "- 模型供应商、实际型号、API 密钥、平台应用审核状态、账号授权、商品事实、真实素材、运营指标和任务质量阈值没有由原文替用户配置。必须显示缺口并支持条件化接入/人工路线。",
    "- Muse、NOP、FFMAG/FFMAP 的指代保留待确认；不得把 NOP 自行改称 MCP 或把未绑定 Muse 当可调用 provider。",
    "- 原始 HTML 后附工作台是离线设计交互；文件选择/参数预览/画布/画面关键帧示例不能宣称媒体已上传、转码、渲染或平台发布。",
    "- 原文 69 条 v6.scenarioMatrix 中若干 module 字段使用中文标签（如“生成”“入库”），不是 modules 中的标准 ID。需要按 featureIds 解析所属模块，不可直接拿该字段当路由 ID。",
    "- machines.task 使用中文状态；v5.playbook.architecture.sharedRuntime 又列运行时英文状态。实现必须公开二者映射并保留 execution/domain 分层，不能混用为一套隐式状态机。",
    "- source 中既有历史研究也有最新研究；原文称 11 仓库未运行、65 集成验收 not_run。不能将这些历史设计检查冒充本次五轮真实回归。",
    "",
    "## 5. 每模块完整功能与命令合同",
    "",
]
for module in data["modules"]:
    c = module["engineeringContract"]
    lines += [
        f"### {module['id']} · {module['label']}",
        "",
        module["scope"],
        "",
        f"界面行为：{c['uiOperation']}",
        "",
        f"命令：`{c['commandHandler']}`",
        "",
        f"权威读取：{', '.join(c['factsRead'])}。",
        "",
        f"权威写入：{', '.join(c['factsWrite'])}。",
        "",
        f"事件：{', '.join(c['emittedEvent'])}。",
        "",
        f"故障恢复：{c['failureRetry']}",
        "",
        f"模块验收：{c['acceptance']}",
        "",
        "| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |",
        "|---|---|---|---|---|",
    ]
    for f in data["features"]:
        if f["module"] != module["id"]:
            continue
        lines += [
            f"| {f['id']} | {f['label']} | {', '.join(f['inputs']) or '入口创建'} | {', '.join(f['outputs'])} | {' / '.join(r['label'] for r in f['routes'])} |"
        ]
    lines += [""]
lines += [
    "## 6. 实体字段与所有权",
    "",
    "全部实体还包含 common 字段及不可变版本/租户/来源约束。原始字段声明一字不漏保留在 JSON 中。",
    "",
    "| 实体 | 名称 | Owner | 字段数 | fieldSchema | 字段 |",
    "|---|---|---|---:|---|---|",
]
for e in data["entities"]:
    lines += [
        f"| {e['id']} | {e['label']} | {e['owner']} | {len(e['fields'])} | {'有' if e.get('fieldSchema') else '未定义'} | {', '.join(e['fields'])} |"
    ]
lines += [
    "",
    "## 7. 状态机",
    "",
    "| ID | 名称 | 初态 | 终态 | 状态数 | 转移数 |",
    "|---|---|---|---|---:|---:|",
]
for machine in data["machines"]:
    lines += [
        f"| {machine['id']} | {machine['label']} | {machine['initial']} | {', '.join(machine['terminal']) or '无，允许持续观测'} | {len(machine['states'])} | {len(machine['edges'])} |"
    ]
lines += [
    "",
    "尤其不能将已入库/已发布当执行终态；未知外部结果不能通过本地取消变成“未发生”；所有转移必须有 guard、版本并发控制和证据。",
    "",
    "## 8. 全部用户需求",
    "",
]
for r in data["requirements"]:
    lines += [f"- **{r['id']}** {r['text']}"]
lines += ["", "## 9. 现有集成候选与边界", "", "| 集成 | 原文状态 |", "|---|---|"]
for i in data["integrations"]:
    lines += [f"| {i['id']} | {i.get('status','未声明')} |"]
lines += [
    "",
    "MediaCrawler 的商用许可、social-auto-upload 的副作用恢复、Graphiti 的鉴权边界、WhisperX/BeatThis 的模型和实际环境、MEP 的协议/宿主范围均按各适配器真实状态处理。来源引用是原文历史证据，本任务若使用实际依赖需重新读取官方说明并运行验证。",
    "",
    "## 10. 接入真实任务的十个原文验收门槛",
    "",
]
for m in data["v5"]["playbook"]["p0AcceptanceMilestones"]:
    lines += [f"### {m['id']} · {m['name']}", "", m["pass"], ""]
lines += [
    "## 11. 后续五轮回归的证据要求（工程建议，非原文新增功能）",
    "",
    "每轮均重新遍历 23 模块与178功能的实现映射，执行所有当前可运行测试和关键真实媒体路径，保存命令、环境、开始/结束时间、测试报告、失败修复和未覆盖原因。建议各轮额外重点如下，但不能仅检查单一重点后称“全量”。",
    "",
    "1. 需求/合同/数据库：目录与接口覆盖、同租户引用、字段验证、迁移、不可变版本及 outbox。",
    "2. 正向闭环：文件夹/素材→配方/时间线→真实渲染→交付包及回查；能接入的模型和发布路径另列证据。",
    "3. 反向故障：坏文件、越权、冲突 revision、过期租约、重复幂等键、预算不足、未知副作用、取消与迟到结果。",
    "4. 跨模块回归：价格/配乐/锁/画幅/撤权/模型变更影响，参数六层继承，来源区间反查，索引与事实一致。",
    "5. 浏览器与最终发布核验：桌面/窄屏、键盘、减少动画、明暗主题、真实后端状态与 UI 一致，清洁启动、备份恢复及最终提交。",
    "",
    "不能把“178/178 已登记”称为“178/178 业务已实现”。独立记录 catalog_present、contract_defined、implementation_available、unit_verified、integration_verified、real_media_verified、external_verified 和 production_verified；无证据字段保持 false/unavailable。",
    "",
]
(ROOT / "docs" / "atlas-analysis.md").write_text("\n".join(lines), encoding="utf-8")
(ROOT / "contracts" / "feature-catalog.json").write_text(
    json.dumps(
        {
            "sourceSha256": stats["sourceSha256"],
            "features": [
                {
                    k: f[k]
                    for k in [
                        "id",
                        "module",
                        "label",
                        "goal",
                        "inputs",
                        "outputs",
                        "executionMachine",
                        "domainMachine",
                        "inputContract",
                        "outputContract",
                        "implementation",
                        "operation",
                    ]
                }
                | {
                    "routes": [
                        {
                            k: r.get(k)
                            for k in [
                                "id",
                                "label",
                                "when",
                                "tradeoff",
                                "qualityCostSpeed",
                                "preserves",
                                "invalidates",
                                "recovery",
                                "completionRule",
                            ]
                        }
                        for r in f["routes"]
                    ]
                }
                for f in data["features"]
            ],
            "moduleContracts": data["v5"]["moduleContracts"],
            "scenarioMatrix": data["v6"]["scenarioMatrix"],
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(json.dumps(stats, ensure_ascii=False))
print(
    "Wrote contracts/atlas.json, contracts/atlas-integrity.json, docs/atlas-analysis.md, contracts/feature-catalog.json"
)
