"""Export the server contract and a human-readable operation inventory."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import app

schema = app.openapi()
encoded = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
check_only = "--check" in sys.argv
if check_only:
    if (ROOT / "contracts/openapi.json").read_text() != encoded:
        raise SystemExit("OpenAPI snapshot is stale; run python scripts/export_contracts.py")
else:
    (ROOT / "contracts/openapi.json").write_text(encoded)
lines = [
    "# 前后端接口规范",
    "",
    "版本：0.1.0；源架构 schema 6.0。机器可读合同为 `contracts/openapi.json`，服务启动后同一合同从 `/openapi.json` 读取。",
    "",
    "## 共同规则",
    "",
    "所有业务接口以 `/api/v1` 开头。同源浏览器与 API 使用同一事实账本，JSON 成功返回 `{data, trace_id}`，失败返回 `{error:{code,message,details},trace_id}`；二进制媒体和 SSE 使用合同声明的专用响应。",
    "",
    "服务端决定 tenant_id、actor_id、role。local 模式仅接受回环地址；team 模式使用 Bearer 身份，浏览器媒体和 SSE 使用 POST /session 换取的 HttpOnly 短期 Cookie。会话撤销会重新检查服务端密钥映射。",
    "",
    "修改既有事实必须带 expected_revision；409 后重新读取差异，不自动覆盖。Commands、模型调用和工作流启动各有幂等键；请求超时不等于执行失败，先查询原调用/运行。文件分块按已确认 offset 续传。所有引用必须固定版本且在同租户/明确项目内。",
    "",
    "实体草稿形状见64份JSON Schema；Pydantic声明操作请求，媒体配方还有实际素材探测与参数组合验证。源码未明确的嵌套载荷仍有扩展区，不能仅凭字段存在就视为供应商已接通。",
    "",
    "## 状态码",
    "",
    "| 状态 | 处理 |",
    "|---|---|",
    "| 200 / 201 | 读取或事实已提交；查看 data 中领域状态，不推断业务已完成 |",
    "| 401 / 403 | 身份、角色、租户、当前授权或同源条件不满足 |",
    "| 404 | 版本或实际字节不存在 |",
    "| 409 | CAS、锁、上传offset、幂等或结果核对冲突 |",
    "| 413 | 请求体或媒体资源超过上限 |",
    "| 422 | 数据类型、语义、必需输入或路线验收不满足 |",
    "| 503 | 依赖、能力或存储暂不可用 |",
    "",
    "## 全部已实现 HTTP 操作",
    "",
    "| 方法 | 路径 | 操作 | 请求体 Schema |",
    "|---|---|---|---|",
]
operations = 0
for path, methods in schema["paths"].items():
    for method, operation in methods.items():
        operations += 1
        content = operation.get("requestBody", {}).get("content", {})
        wire = content.get("application/json", {}).get("schema", {})
        body = wire.get("$ref", "").rsplit("/", 1)[-1] or (
            "binary"
            if "application/octet-stream" in content
            else "object / runtime guards" if wire else "—"
        )
        lines.append(f"| {method.upper()} | `{path}` | {operation.get('summary', '')} | `{body}` |")
lines += [
    "",
    f"共 {operations} 个 HTTP 操作。完整 path/query/header 条件、枚举、范围、错误与二进制声明在 OpenAPI 中。",
    "",
    "## 工作流和组件扩展",
    "",
    "`GET /execution/capabilities` 返回当前真实路线处理器与对应 operation_args Schema。节点直接输入使用 `{record_id,version,value}`，上游输入使用 `{node_id,output,index?,value}`，value 是 id/ref/data/version。上游绑定必须存在于真实DAG祖先，禁止读取仅在画布上相邻的节点。",
    "",
    "新增实际执行器必须同时实现：能力准入、冻结输入、原文质量门槛、预算和时限、持久检查点、fencing写入、真实产物/错误/未知结果、事件与回归。静态注册到目录并不会自动成为可执行能力。",
    "",
    "参考：`docs/data-contracts.md`、`docs/media-capabilities.md`、`docs/architecture.md`、`docs/implementation-matrix.md`。",
]
if not check_only:
    (ROOT / "docs/api-reference.md").write_text("\n".join(lines) + "\n")
print(
    json.dumps(
        {"paths": len(schema["paths"]), "operations": operations, "openapi": schema["openapi"]}
    )
)
