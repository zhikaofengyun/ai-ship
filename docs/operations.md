# 运行、身份、供应商与恢复

## 运行边界

默认是单机开发/验证栈，服务只监听 `127.0.0.1:8765`，SQLite 和内容寻址对象位于 `AI_SHIP_DATA_DIR`。不要把本地身份模式直接暴露给其他机器。当前没有生产级部署证明、分布式调度、共享会话、自动密钥轮换或生产恢复演练。

`python scripts/run.py --help`列出启动参数。脚本检查 FFmpeg/ffprobe 是否存在；缺失时工作台仍可启动，相关媒体操作明确拒绝。环境变量优先于默认数据路径，启动前核对 `AI_SHIP_DB` 是否有显式覆盖。脚本不会自动加载 `.env`；应通过进程管理器或当前 shell 明确注入配置。

| 变量 | 作用 |
| --- | --- |
| `AI_SHIP_MODE` | `local` 或 `team`，其他值拒绝 |
| `AI_SHIP_DATA_DIR` | 持久数据根，默认 `.data` |
| `AI_SHIP_DB` | 可选显式 SQLite 路径；备份也使用它 |
| `AI_SHIP_API_KEYS` | 团队 Bearer 身份到 `{tenant_id,actor_id,role}` 的服务器 JSON 映射 |
| `AI_SHIP_SESSION_TTL` | 浏览器会话秒数，限制在 60–3600，默认 3600 |
| `AI_SHIP_PROVIDERS` | 服务端模型注册 JSON，默认空，不会产生外部调用 |
| `AI_SHIP_COMFY_PROFILES` | 自托管图片、视频及参考图工作流注册表，详见 [生成合同](ai-production-runtime.md) |
| `AI_SHIP_ALLOWED_ORIGINS` | 必要时显式列出的写请求 Origin；默认同源 |
| `AI_SHIP_TRUSTED_HOSTS` | 本地模式额外 Host 名；不放宽回环来源限制 |
| `AI_SHIP_CHROMIUM` | 仅回归脚本使用的官方 Chromium 路径 |

## 团队身份

由管理员生成高熵令牌，并在服务器进程环境设置映射，例如：

```json
{
  "REPLACE_WITH_A_RANDOM_SERVER_TOKEN": {
    "tenant_id": "team-a",
    "actor_id": "editor-01",
    "role": "editor"
  }
}
```

此示例字符串不是可部署密钥。不要把真实令牌写入 Git、业务记录、URL、日志或浏览器长期存储。角色为 owner/editor/reviewer/viewer/worker；通常写入限 owner/editor/worker，批准限 owner/reviewer，角色最终以服务端映射为准。

设置 `AI_SHIP_MODE=team` 后，以 HTTPS 反向代理对外提供同源服务。代理必须明确限制可信转发来源；不要信任任意客户端的 forwarded 头。当前只对回环开发允许 HTTP 会话。设置页可以把 Bearer 换成短期 HttpOnly、SameSite=Strict cookie，供视频 Range、文件、SSE 使用；远程 cookie 为 Secure。令牌撤销或角色变更后，后续请求与长操作安全边界重新核对。

会话只在当前进程内保存令牌摘要；服务器重启后重新建立，多进程需要粘性路由或未来共享会话实现。这版没有直接启用多 worker。身份配置可热读，但从宿主环境修改到运行进程通常仍需要进程管理器重载；不要把修改终端变量误当作已修改服务进程。不能把 `local` 模式通过反向代理公开：代理的回环来源可能获得固定 local-owner 权限。

## 配置真实模型

当前适配器仅支持 HTTPS Chat Completions 文本请求，不支持 Responses API、流式输出、模型自动工具执行、图像/音视频生成。平台账号与模型供应商是不同适配器，设置一个模型密钥不会开启自动发布。

1. 核对供应商当前官方 API、模型名、价格、上下文限制与响应模型别名。
2. 将密钥置于服务器环境，如 `MY_PROVIDER_KEY`。当前只实际读取环境变量；可保存的 `secret:` 引用尚未接入外部 secret store。
3. 将对应注册对象写入 `AI_SHIP_PROVIDERS`。
4. 在工作台创建 ModelProfile，`endpoint_ref` 引用注册名称，`model_id` 位于白名单，`credential_ref` 使用同一 `env:` 名称。
5. 为项目设置同币种预算，人工进行有界真实联调。可用性目录初始为 `configured_not_probed`，不会声称已经验证。

注册结构如下，所有占位值须替换为核实后的实际值；价格占位字符串会被拒绝，以免错误使用零价：

```json
{
  "my-provider": {
    "base_url": "https://YOUR_OFFICIAL_PROVIDER/v1",
    "api_key_env": "MY_PROVIDER_KEY",
    "provider_identity": "verified-provider-identity",
    "models": ["verified-model-id"],
    "response_model_aliases": {"verified-model-id": ["verified-model-snapshot"]},
    "context_limit": 32768,
    "input_price_per_million": "REPLACE_WITH_VERIFIED_DECIMAL",
    "output_price_per_million": "REPLACE_WITH_VERIFIED_DECIMAL",
    "currency": "USD"
  }
}
```

代理固定追加 `/chat/completions`，不跟随重定向，不读取浏览器指定的任意地址或密钥。`provider_identity` 和响应别名由服务器管理员声明，不能由用户改显示名称伪装模型异构。价格快照以每百万 input/output tokens 计；`calculated_charge` 是实际用量乘该快照，`actual` 保留给已核对账单，不混为一谈。

数字项目预算按 USD；其他币种用 `{amount,currency}` 或 `{max_cost,currency}`。未配置汇率不会跨币种混算。输入费用上界按 UTF-8 字节保守估计，不承诺等同每个供应商 tokenizer；真实用量与预算超出另存事实。本地设备/人工成本未计价，以未知记录披露，不能称总成本已完整覆盖。

超时未知请求保留预算并禁止相同实际请求换键重发；当前没有对所有供应商统一可用的自动核对路径。管理员应保留 trace_id、ToolCall 和供应商证据，按供应商账单/请求查询确认后再开发专用 reconcile 处理器。禁止直接改数据库状态来“修好”未知结果。

## 媒体与存储

当前单文件上限 256 MiB，上传块上限 4 MiB，媒体时长最多 3600 秒；每进程最多两个 FFmpeg 子进程，单个处理过程最多 300 秒。模型账本和费用检查达到 500 条时明确阻断，部分普通查询最多 2000 条；需要实际分区/分页方案才能扩大容量。浏览器选择文件/文件夹不等于后台文件夹监视；大文件高并发、长片、VFR、色彩管理、专有编码和专业工程往返尚未完成生产验收。真实处理支持列表见 `media-capabilities.md`。

对象按 hash 保存于租户目录，已提交字节不可覆盖。备份不能只复制正在运行的 SQLite 主文件而忽略 WAL；使用提供的 SQLite backup 脚本。磁盘容量、加密、外部保存期限与跨地域副本需部署方另行配置和验收。

```bash
python scripts/backup.py --data-dir .data --output backups/ship-backup.zip
python scripts/restore.py backups/ship-backup.zip --destination restored-data
```

备份包含一致性数据库快照和不可变对象；不包含未完成上传、会话、进程环境中的密钥。恢复先验证 ZIP 路径、条目、大小、SHA-256、SQLite 完整性/外键以及数据库所声明的可用对象，再写入全新目录；不会合并覆盖旧工作区。停止应用后再把 `AI_SHIP_DATA_DIR` 指向恢复目录，同时移除/更新旧 `AI_SHIP_DB` 覆盖。恢复数据库不会回滚供应商账单或外部操作，必须另外核对快照后发生的外部事实。没有执行真实切换之前不能说生产恢复完成。

## 容器

仓库提供非 root Dockerfile 和 loopback 端口的 Compose 配方。运行前必须设置 `AI_SHIP_API_KEYS`；命名卷保存 `/data`。本次交付对容器配方进行代码核对，是否实际构建/启动以回归和 CI 结果为准，不把配方存在当作容器验收。

```bash
docker compose up --build
```

Compose 默认不注入任何真实供应商密钥，需要部署方明确声明所需环境变量。外部入口、TLS、服务管理、资源配额、磁盘告警与备份计划没有自动替用户创建。

## 排错与可核对性

每个 JSON 响应有 trace_id，响应头为 X-Trace-Id。优先保留请求 ID、对象精确版本、Run/ToolCall、事件序号和原始错误码。403 是权限/来源阻断，409 常表示版本/幂等/未知结果/准入冲突，422 是合同错误；不得仅因按钮恢复可点就换幂等键重复付费调用。

`/api/v1/health`报告当前运行能力，`/execution/capabilities`给出实际处理器；两者不能替代真实外部账号探测。SSE 使用持久 seq 恢复，15 秒心跳；页面断线后应重读事实，不能用浏览器动画推断任务是否完成。

本次失败尝试另观察到一条已核实的断线日志：空库 SSE 在15秒首次心跳之前被浏览器关闭时，当前 Starlette 1.6.0 / Uvicorn 的中间件组合可能记录 `RuntimeError: No response returned.`。实际堆栈为客户端断开后的 AnyIO EndOfStream，未进入业务写入，不会重发或回滚事实。这里只说明该已确认路径，不建议全局忽略所有同名异常；其他出现位置仍须按 trace_id 排查。

扩展到多节点前应完成数据库/对象存储迁移、共享会话、跨进程作业拥有者、生产负载、真实供应商与平台联调、完整权限政策和回滚/恢复演练。这些属于当前明确未完成范围。
