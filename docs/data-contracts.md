# 数据合同与持久化规范

## 范围与依据

本规范以用户批准的 `contracts/atlas.json` 为唯一功能目录。原始 23 模块、178 功能、534 路线、742 控件、64 实体、12 状态机完整保存。`contracts/entities/` 为 64 个逐实体 JSON Schema；`record.schema.json` 为统一版本信封；`command.schema.json` 为强制命令信封。接口的操作级入参以实时 `/openapi.json` 为准。

原 HTML 的实体字段中有描述文本和部分结构化 `fieldSchema`，并非完整可执行 SQL。当前工程对字段形状作了明确补充：标量、枚举、集合、结构化配置、版本化引用、时间区间、UTC 时间戳分别处理。每个推断字段标 `x-validation-level: engineering-structural`，明确来源枚举标 `source-enum`；供应商原始载荷与未定义细节标 `named-object-extension` 或 `named-descriptive-payload`。这些扩展形状不能被当作供应商语义或全部领域算法已完成验收。

## 统一事实信封

每个事实固定为 `id, kind, tenant_id, project_id, version, data, refs, created_at, hash, status`。ID 随机生成，不以路径或外部账号 ID 代替。身份来自服务端；请求中的租户与角色不构成权限。实体私有数据在 `data`，所有稳定依赖在 `refs`：`{id, version, kind, field, fields, range?}`。写入时固定版本，同一字段的多个合法目标类型按联合类型验证。历史关系不可覆盖。`fields` 标明被消费的源字段；`range` 为半开区间。

每次创建和修订在一个 `BEGIN IMMEDIATE` 事务中写入事实版本、引用索引和事件/outbox。SQLite 外键和不可变触发器共同约束；业务修订只插入新版本并移动当前指针。事务之间通过 CAS `expected_revision` 防止覆盖；同一 idempotency_key/command_id 绑定完整请求摘要，重放返回原结果，换请求体返回 409。数据 JSON 禁止非有限数字，禁止明文 API 密钥和 token 凭据；VoiceMap.token 表示语音词元，属于合法业务字段。

## 字段决策

| 主题 | 工程规范 |
| --- | --- |
| 金额 | 商品 `price` 支持非负十进制定点字符串，兼容显式数值；必须有 ISO 三位币种。CSV 不经浮点损失精度。费用 estimated 与 actual 区分，实际未知保留 null。 |
| 缺失 | null 必须保留其语义。Metric.value_or_null 为 null 时必须 missing_reason；零与无权限/未知/未适用不能互换。 |
| 时间 | 实际事件时间为 RFC3339 UTC/带偏移字符串；计划发布同时记录本地墙钟、IANA 时区、UTC 与 DST 取舍；不把模糊本地时间擅自归一。 |
| 媒体时间 | TimeMap 与 Timeline 保存显式时基和半开区间，源/输出时钟分开。源映射精度与容差显式记录；当前 FFmpeg 输出不声称逐帧 VFR 完美逆映射。 |
| 引用 | 普通 ID 字符串在事务内解析并冻结为精确版本，显式 typed ref 保留指定版本。跨租户不可解析；执行事务不能隐式跨项目，资源共享受专用路径约束。 |
| 参数 | 六层顺序为项目、序列、轨道、片段、区间、关键帧；显式值/继承/删除覆盖/提案不同。朝向匹配覆盖本层通用值，上层硬锁可约束子层，人工锁冲突只产生提案。 |
| 草稿 | 领域实体允许按步骤形成草稿，Schema 不把原文全部 fields 都擅自标必填。实际执行接口另检查所选路线必需输入、权限、能力与质量门槛；保存草稿不是完成。 |
| 扩展 | JSON Schema 保留额外字段以兼容原文扩展与适配器，建议新扩展写入 extensions，并在接口 Pydantic 模型声明。用户写入不能伪造 Run/Artifact/Post/Cost 等执行事实。 |

## 重点实体约束

ImportBatch/ImportEntry 将逐文件接收、错误与去重留痕；size_bytes/transferred_bytes 为非负整数；原件只在完整接收、hash 与媒体探测后提交。浏览器上传支持 4 MiB 分块、服务端持久 offset、逐块 SHA-256 与整文件 hash 校验。相同 offset/相同 hash 的块可幂等重发，错误 offset 或 hash 被拒绝；重新选择同一文件后可跨连接续传，完整核验后才入库。未完成上传可取消；大文件后台文件夹监视尚未启用。通用 JSON 请求实际接收上限为 4,000,000 字节，分块接口精确允许 4,194,304 字节（4 MiB），全文件接口由媒体执行器独立限制。

Timeline 检查重复轨道、片段 ID、序列归属、引用索引、正时长区间与关键帧时钟/锚定组合。通用更新产生新的草稿 revision，已锁版历史保持。JSON Patch 只允许显式 add/replace/remove，不能借补丁抹掉锁或改归属；局部影响只取真实引用索引，不拿导航关系图当执行 DAG。

ModelProfile 只存 credential_ref（env:/secret:），配置健康状态初始为 unprobed；模型名相同不能伪装不同供应商能力。AgentDeployment 约束轮数、深度、并发、token 和预算，角色重复绑定被拒绝。供应商接口不支持或缺凭据时明确阻塞。

Cost 的手工预留与模型调用使用同一金额规则：每条事实按 actual、calculated_charge、reserved/estimated 的优先级计入一次，预算比较使用 Decimal。actual 表示已核对账单；calculated_charge 是真实 token 用量乘已冻结配置价格，不能冒充账单。未知调用保留费用上界。数字项目预算按 USD 解释；结构化预算为 `{amount,currency}` 或 `{max_cost,currency}`，禁止跨币种无汇率混算，命令临时 limit 只能收紧项目上限。ReleaseBudget 只释放标明 `manual_budget_reservation`、`not_sent` 且状态仍为 reserved 的手工预留，清零当前 reserved，同时保留历史版本和 estimated；已计量费用与未知供应商结果不能被释放抹除。未计价的本地渲染耗时作为 `unpriced_cost_refs` 单独披露，预算门禁范围明确是 `priced_monetary_costs`，不声称已覆盖设备及人工总成本。货币费用缺少可核验金额时阻断后续预留。

Publication 草稿、真实提交、人工回执与 Post 分开。实际发布状态不能通过通用 CRUD 写成成功。PlatformEvent 保存来源种类和回执证据；发布预检重新核查当前来源权利、hash、批准范围与账号能力。人工回执保留人工来源，不冒充 webhook 或服务器探测。

RenderManifest 保存实际工具版本、渲染规格、片段使用数据、产物 hash、字节落点与映射；目前没有保存完整 FFmpeg argv，不以“可查看规格”冒充“命令级逐字重放”。技术 QC 通过只表示已测的尺寸/帧率/时长/音轨符合合同，不代替内容、商品事实或版权验收。ReplayManifest 当前生成依赖闭包和缺口报告，不虚称已执行隔离重放。

## SQL 结构与迁移

正式迁移文件为 `contracts/migrations/001_immutable_control_plane.sql`。`records` 保存身份和当前版本指针，`revisions` 保存不可变事实内容，`reference_index` 保存版本依赖，`events` 为持久事务事件，`command_results` 保存幂等结果，`leases` 保存 fencing token，`consumer_offsets` 预留消费水位。SQLite WAL + FULL 同步、外键、15 秒忙等待、短写事务适用于本地单节点。模型/FFmpeg 长耗时调用不持有全程数据库事务。多主机团队部署应迁移 PostgreSQL 与外部对象存储，并重新验收跨进程作业租约、备份恢复、保留/删除政策和密钥管理；这版不把 SQLite 多进程等同分布式系统。

## 状态与事件

12 套原始状态机可通过 `/api/v1/machines/{id}`读取；`/validate` 只确认声明迁移是否存在，明确不授予执行。实际任务控制处理器对暂停、取消、恢复和未知副作用保持守卫：执行中发停止请求不等于已经停止，结果未知优先核对，通用命令不能提交“已核对”布尔值绕过供应商回执。

`GET /api/v1/events`提供持久事件查询；`GET /api/v1/events/stream`提供 SSE，event 为 fact、id 为持久 seq，可用 after 或 Last-Event-ID 恢复。读取按 seq 升序分批，避免断线期间大量事件时跳过中间事实。SSE 重新检查身份，15 秒心跳，连接断开后退出；它投射数据库事件，不伪造任务进度。

## 本地执行器和浏览器身份

`POST /api/v1/execution/start`将冻结的 Workflow 版本编译为真实本地任务；支持的 10 条路线与准确入参 schema 可从 `/execution/capabilities`读取。每个节点通过 `input_bindings`绑定已保存的 `{record_id,version}`，或读取图中真实上游 `{node_id,output}`；不能通过画布位置或任意代码建立隐式依赖。`operation_args`是实际处理器参数，原文 `parameters`是功能控件配置，两者不会悄悄互相替代。

已执行节点固定产物引用并写入检查点。幂等重发返回同一运行；暂停/取消先等当前本地操作到安全检查点，已经完成的产物保留。恢复不会重复已完成节点，输入当前版本改变、角色/凭据撤销、步数/次数/时限耗尽会阻塞。工作进程写入受 fencing token 与租约到期双重检查；中断后存在运行中或未知节点必须先核对，不能重发冒充恢复。新事实的 execution_context 保存 run/node/token，帮助核对“产物已提交但检查点未提交”的窗口；只有同节点、真实可读取并符合输出合同的候选能通过 `/reconcile`接受。

浏览器团队身份可用 `POST /session`将有效 Bearer 身份换为最长一小时的 HttpOnly、SameSite=Strict 同源 cookie，供视频 Range、图片、下载与 SSE 使用；令牌不进入 URL。远程请求须 HTTPS，Secure cookie；回环开发允许 HTTP。会话只在内存保存 backing key 摘要，每次请求按当前服务端密钥映射核身份，服务器重启失效；`DELETE /session`退出。长运行节点在提交与安全边界再次核对角色/凭据，SSE 在身份变化后退出。多进程/多主机部署仍需共享会话服务或粘性路由。

## 64 类实体目录

所有源字段都保留来源注记。表中“源字段数”不包含统一信封与工程新增的 name/title/extensions。

| 类型 | 中文名 | 所属模块 | 源字段数 | 源关系数 | Schema 文件 |
| --- | --- | --- | ---: | ---: | --- |
| `Project` | 项目版本 | project | 10 | 1 | `Project.schema.json` |
| `Product` | 商品事实 | project | 8 | 1 | `Product.schema.json` |
| `Brief` | 创作简报 | trend | 8 | 2 | `Brief.schema.json` |
| `Trend` | 热点证据 | trend | 6 | 0 | `Trend.schema.json` |
| `Source` | 来源与授权 | acquire | 14 | 0 | `Source.schema.json` |
| `Asset` | 原始素材 | ingest | 7 | 3 | `Asset.schema.json` |
| `AssetVersion` | 素材衍生版本 | ingest | 27 | 6 | `AssetVersion.schema.json` |
| `Shot` | 镜头片段 | label | 11 | 1 | `Shot.schema.json` |
| `Tag` | 标签断言 | label | 30 | 3 | `Tag.schema.json` |
| `Index` | 检索索引版本 | search | 6 | 1 | `Index.schema.json` |
| `Cluster` | 相似素材族 | search | 9 | 6 | `Cluster.schema.json` |
| `AssetScore` | 素材候选评分 | assetops | 24 | 3 | `AssetScore.schema.json` |
| `Usage` | 使用与曝光账本 | assetops | 35 | 10 | `Usage.schema.json` |
| `Script` | 脚本版本 | voice | 7 | 1 | `Script.schema.json` |
| `Audio` | 音频版本 | voice | 29 | 5 | `Audio.schema.json` |
| `VoiceMap` | 口播语义时间图 | voice | 19 | 3 | `VoiceMap.schema.json` |
| `Music` | 音乐与节拍 | music | 25 | 4 | `Music.schema.json` |
| `Mix` | 混音方案 | music | 14 | 3 | `Mix.schema.json` |
| `Plan` | 分镜计划 | plan | 10 | 3 | `Plan.schema.json` |
| `Timeline` | 统一时间线 | edit | 32 | 6 | `Timeline.schema.json` |
| `RenderJob` | 渲染任务 | render | 7 | 1 | `RenderJob.schema.json` |
| `Artifact` | 成片与交付物 | render | 19 | 7 | `Artifact.schema.json` |
| `Account` | 平台账号 | account | 21 | 3 | `Account.schema.json` |
| `Grant` | 授权引用 | security | 24 | 1 | `Grant.schema.json` |
| `Publication` | 发布意图与映射 | publish | 47 | 7 | `Publication.schema.json` |
| `PlatformEvent` | 平台状态事件 | publish | 18 | 2 | `PlatformEvent.schema.json` |
| `Metric` | 表现快照 | metrics | 40 | 4 | `Metric.schema.json` |
| `Comment` | 评论信号 | metrics | 28 | 4 | `Comment.schema.json` |
| `Insight` | 可验证分析 | learn | 13 | 2 | `Insight.schema.json` |
| `Experiment` | 实验记录 | learn | 9 | 2 | `Experiment.schema.json` |
| `Policy` | 策略版本 | learn | 11 | 2 | `Policy.schema.json` |
| `Workflow` | 工作流版本 | agents | 23 | 1 | `Workflow.schema.json` |
| `Run` | 执行实例 | harness | 16 | 2 | `Run.schema.json` |
| `ToolCall` | 工具调用账本 | harness | 23 | 2 | `ToolCall.schema.json` |
| `Approval` | 确认与授权单 | security | 21 | 32 | `Approval.schema.json` |
| `Memory` | 可追溯图记忆 | data | 8 | 1 | `Memory.schema.json` |
| `Event` | 事务与事件 | data | 18 | 1 | `Event.schema.json` |
| `Audit` | 审计证据 | ops | 9 | 1 | `Audit.schema.json` |
| `Cost` | 成本与配额 | ops | 10 | 1 | `Cost.schema.json` |
| `Capability` | 能力快照 | harness | 25 | 1 | `Capability.schema.json` |
| `View` | 工作台配置 | workspace | 8 | 1 | `View.schema.json` |
| `Post` | 实际平台帖子 | publish | 25 | 5 | `Post.schema.json` |
| `AgentRole` | Agent 角色与职责版本 | agents | 19 | 3 | `AgentRole.schema.json` |
| `SkillVersion` | 技能定义与评测版本 | harness | 19 | 6 | `SkillVersion.schema.json` |
| `TaskContract` | 可验收任务契约 | agents | 24 | 5 | `TaskContract.schema.json` |
| `RunGraph` | 可调整运行图版本 | agents | 28 | 9 | `RunGraph.schema.json` |
| `DecisionRecord` | 有证据的选择与分歧记录 | agents | 26 | 4 | `DecisionRecord.schema.json` |
| `ChangeSet` | 字段与区间变更事务 | agents | 30 | 7 | `ChangeSet.schema.json` |
| `EvaluationSuite` | 冻结评测与故障注入套件 | learn | 22 | 4 | `EvaluationSuite.schema.json` |
| `PreferenceProfile` | 可解释偏好与自治设置 | workspace | 19 | 2 | `PreferenceProfile.schema.json` |
| `AlignmentBinding` | 语义—音频—镜头绑定 | voice | 25 | 13 | `AlignmentBinding.schema.json` |
| `RenderManifest` | 实际渲染与区间谱系清单 | render | 32 | 8 | `RenderManifest.schema.json` |
| `DeliveryPackage` | 交付与回导包 | render | 27 | 11 | `DeliveryPackage.schema.json` |
| `ProductionTrace` | 全程生产事实投影 | metrics | 23 | 13 | `ProductionTrace.schema.json` |
| `ImportBatch` | 导入批次 | acquire | 11 | 0 | `ImportBatch.schema.json` |
| `ImportEntry` | 逐文件导入记录 | acquire | 22 | 4 | `ImportEntry.schema.json` |
| `TransformRecipe` | 素材处理配方版本 | ingest | 16 | 0 | `TransformRecipe.schema.json` |
| `TransformRun` | 实际素材处理记录 | ingest | 21 | 6 | `TransformRun.schema.json` |
| `BlobLocation` | 媒体对象位置 | data | 14 | 0 | `BlobLocation.schema.json` |
| `TimeMap` | 源到输出时间与贡献映射 | data | 15 | 8 | `TimeMap.schema.json` |
| `ModelProfile` | 模型配置版本 | harness | 23 | 1 | `ModelProfile.schema.json` |
| `AgentDeployment` | 异构团队部署版本 | agents | 21 | 3 | `AgentDeployment.schema.json` |
| `Hypothesis` | 效果解释与实验假设 | learn | 18 | 3 | `Hypothesis.schema.json` |
| `ReplayManifest` | 隔离重放依赖清单 | data | 16 | 5 | `ReplayManifest.schema.json` |

## 验证边界与依据

自动回归覆盖 schema 拒绝路径、同租户引用、联合类型、CAS 并发竞争、事务回滚、幂等冲突、人工锁、空值/零、有限预算和租约 fencing。目录结构覆盖不替代全部 534 条路线的供应商端真实验收，具体执行覆盖另见实现清单与回归证据。

实现依据：

- SQLite 事务与 BEGIN IMMEDIATE：https://www.sqlite.org/lang_transaction.html
- SQLite 外键：https://www.sqlite.org/foreignkeys.html
- SQLite WAL：https://www.sqlite.org/wal.html
- FastAPI 静态文件：https://fastapi.tiangolo.com/tutorial/static-files/
- FastAPI 安全工具：https://fastapi.tiangolo.com/reference/security/
- JSON Schema Draft 2020-12：https://json-schema.org/draft/2020-12
