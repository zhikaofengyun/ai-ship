# AI Video Agent Atlas 原文需求解构

本文件是对上传 HTML 的只读需求整理。原始完整模型位于 contracts/atlas.json；提取脚本为 scripts/extract_atlas.py；引用一致性证据为 contracts/atlas-integrity.json。不是产品完成度报告。

## 1. 权威来源与精确范围

- 源文件：AI_Video_Agent_Atlas.html；8,218,282 bytes；SHA-256 `65a0e6e7f19b0e087be4ff0adcd1166c7073211caa5988f739df05b47971dee4`。
- 文档 schemaVersion 6.0；原文日期 2026-11-22；时区 America/Los_Angeles。
- 范围：23 模块、178 功能、534 路线、742 控件、574 实施步骤、1104 质量检查条目、574 失败路径、64 实体、12 状态机、126 状态、285 转移、571 关系、42 需求、14 场景、69 正/反/跨模块验收场景、20 集成候选、195 引用来源。
- JSON 提取完整往返相等；功能到模块/实体/需求/来源、关系端点、实体关系目标、状态机转移端点均无缺失引用。此验证不证明软件已实现。
- 原文成熟度：完整架构设计与离线交互示例；真实媒体与平台接入待验收。

## 2. 不能擅自改变的总体约束

### RULE-CONTROLS · 参数可编辑与约束优先级

每个控件有类型、单位、作用域、影响和人工锁。用户给定值、可覆盖建议值、示例值、实际测量、硬质量门槛分别保存；没有依据时不编数值默认。数学域与用户指定范围可做边界。

### RULE-INHERIT · 统一参数继承

项目→序列→轨道→片段→区间→关键帧逐字段解析，同层朝向匹配覆盖通用值。缺省继承、显式值和删除覆盖不同；上层硬锁按声明覆盖子层，人工锁仅生成提案。

### RULE-FORMATS · 内容模式条件依赖

口播、卡点、纯音乐、产品演示、混合路线各自绑定必要输入。Voice Map是口播语义主轴，不强迫纯音乐构造台词。模板与自由创作都编译到可编辑时间线与相同质量合同。

### RULE-GRAPH · 图谱与实际执行分离

引力图是功能关系导航；拖动布局只改View。执行画布使用有类型端口、条件参数、数据依赖和有界循环编译；连线存在不等于可运行。

### RULE-LINEAGE · 真实贡献与变化传播

每次使用建立独立实例；转场叠层混音可多源；定格倒放坡道支持多值反查。变化沿实际字段/区间引用传播，未知映射扩大检查，不沿所有控制边整图重跑。

### RULE-DATA · 数据与知识分层

真实原件与回执是事实，评价与假设是有范围推断，索引和生产轨迹是可重建视图。NULL、未知、未适用、零和失败不同。实际写入由各模块权威拥有者提交。

### RULE-AGENTS · 核心始终异构多智能体

核心规划、创作裁决、效果分析保持多职责和必要独立复核。资源不足可串行异构小队或有界人机协作；确定性工具可以单worker执行。角色名字和不同模型名都不能替代独立实证。

### RULE-LOOP · 有界优化与停止

每循环明确目标、输入快照、输出验收、费用/时间/轮次/委派界限和无进展规则；证据不足允许停止为未决。候选经过离线、适用小范围验证与晋升才改活动策略。

### RULE-QUALITY · 质量成本取舍

硬事实、许可、当前授权、真实音画映射、关键可读性先过门，再比较美感、时延、费用和多样性。低预算减少候选、并发或效果复杂度，不能将错误内容评成合格。

### RULE-EXTERNAL · 外部效果与恢复

准入、发送、平台接纳、公开、指标成熟分别记录。未知发送/计费不得换键或换通道重发；本地回滚不擦除远端事实。停止新动作可以先执行，恢复重新准入。

### RULE-REPLAY · 可复刻等级如实声明

记录实际参数、随机抽样、字节落点、时空映射、工具环境和响应。逐位一致、容差内可比和语义重演分级；未记录或非确定性模型不承诺完全相同。

### RULE-RELEASE · 候选与生效分开

媒体、策略、模型、技能和工作流均可保存候选；只有适用验收和当前授权具备才晋升。批准、排期和配置保存不等于实际生效，回退对象也需当前合法可用。

### RULE-CLAIMS · 设计、演示与真实执行区分

HTML交互、静态合同验证、纯逻辑测试、浏览器测试、真实媒体处理和生产联调分别报告。本轮只证明模型一致性与设计覆盖，不声称后端已连接或完整产品已实现。

## 3. 文档规定的最小工程架构

按原文 v5.playbook.architecture 与 deploymentPlan：

- 交互与人工控制：对话、素材库、预览时间线、无限画布、Agent 任务抽屉、发布日历和效果面板都调用同一命令入口。
- 目标与业务控制面：校验 TaskContract，编译 Workflow/RunGraph，处理事件与有限循环，冻结输入版本和预算，再将合法任务派发给角色或工具。
- 异构多 Agent 集群：按内容类型与当前缺口组建小队；每个角色使用独立配置、受限工具、按需证据和结构化产物。
- 媒体与平台执行面：执行文件扫描、探测、标注、画面调整、ASR、渲染、上传、状态查询等确定任务；隔离长耗时 CPU/GPU 作业与控制 API。
- 事实、版本与知识面：关系库保存权威元数据和账本，对象存储保存原件与产物，事件日志驱动可重建索引；知识和评价引用真实事实。

- Web UI + 控制 API：一个应用服务即可起步。统一命令、鉴权、运行状态与 SSE/WebSocket 事件推送
- 关系数据库：团队场景优先 PostgreSQL；原型可用 SQLite 但须明确并发与迁移边界。素材/版本/使用/发布/成本/任务为权威；事务 outbox 不依赖额外消息中间件才能成立
- 对象存储：本机/服务器文件对象目录起步并保留可替换 ObjectStoreAdapter；需要多 worker 时迁至兼容对象存储。原件、代理、衍生、预览和成片分类型保留，数据库只存引用及校验信息
- 作业与媒体 worker：首期数据库 job table + 有限 worker；控制服务与 FFmpeg/模型进程隔离。有租约、重试、取消和恢复，按实测瓶颈增加 CPU/GPU 并发
- 模型调用代理：后端集中解引用 secret_ref、限流和预算；按角色路由。可以先用现有 API 与本地确定性工具，避免为每个动作启动大模型
- 检索与知识：先全文/结构化查询，必要时 pgvector；Graphiti 或独立图服务在跨素材知识检索确有收益后添加。减少早期运维与重复数据，不影响完整谱系和事实账本
- 本机接入：浏览器选择文件夹用于明确批次；需要常驻扫描和大文件续传再加受控本地 companion。符合用户网盘下载到电脑的实际来源，同时不假设网页能任意后台读取磁盘

命令统一入口字段：`command_id`, `command_type`, `project_id`, `actor_id`, `actor_role`, `target_id`, `expected_revision`, `input_version_refs`, `parameters`, `policy_version`, `idempotency_key`, `trace_id`, `reason`。

可选字段：`approval_ref`, `model_profile_ref`, `budget_reservation_ref`, `lease_fencing_token`, `parent_command_id`, `affected_time_ranges`。

- 同一 idempotency_key 与同一规范化参数返回同一结果；相同键不同参数应拒绝。
- 模型只有工具能力不能自动获得某账号或某素材的权利。
- 本地草稿变更、生成费用、平台发布分别形成明确作用范围，避免一个按钮暗含全部副作用。
- 任何写入都记录 before/after revision；跨对象提交先校验后一次事务落账。

## 4. 原文没有给出的完整实现内容

- 64 实体有 fields/common/relations；只有 21 实体附 fieldSchema，且其中很多是说明性字符串，不是可直接执行的 JSON Schema。43 个没有 fieldSchema。字段必填性、具体 SQL 类型、数值精度、全量外键/唯一键、迁移与保留期仍需实现并明确记录工程决策。
- 控件完整含类型/作用域/影响/锁，但不少 enum 没有穷举 options、table 没有正式列 schema、number 没有业务数值默认。不能把猜测默认值伪称原文规定。
- 模型供应商、实际型号、API 密钥、平台应用审核状态、账号授权、商品事实、真实素材、运营指标和任务质量阈值没有由原文替用户配置。必须显示缺口并支持条件化接入/人工路线。
- Muse、NOP、FFMAG/FFMAP 的指代保留待确认；不得把 NOP 自行改称 MCP 或把未绑定 Muse 当可调用 provider。
- 原始 HTML 后附工作台是离线设计交互；文件选择/参数预览/画布/画面关键帧示例不能宣称媒体已上传、转码、渲染或平台发布。
- 原文 69 条 v6.scenarioMatrix 中若干 module 字段使用中文标签（如“生成”“入库”），不是 modules 中的标准 ID。需要按 featureIds 解析所属模块，不可直接拿该字段当路由 ID。
- machines.task 使用中文状态；v5.playbook.architecture.sharedRuntime 又列运行时英文状态。实现必须公开二者映射并保留 execution/domain 分层，不能混用为一套隐式状态机。
- source 中既有历史研究也有最新研究；原文称 11 仓库未运行、65 集成验收 not_run。不能将这些历史设计检查冒充本次五轮真实回归。

## 5. 每模块完整功能与命令合同

### project · 项目与目标

任务、商品事实、语言、范围

界面行为：建立产品视频项目，填写 SKU/真实卖点/禁用主张、受众语言、内容类型、目标时长、质量与资源预算，比较并冻结创作基线。

命令：`CreateProjectVersion → BindProductFacts → ValidateBrief → FreezeProjectBaseline`

权威读取：Product, Capability, PreferenceProfile, Policy。

权威写入：Project, Product 版本, Brief, TaskContract, Audit。

事件：project.baseline_frozen, brief.approved, task.contract_created。

故障恢复：缺商品证据或约束冲突返回具体字段；超预算给出可选缩减方案；修改已冻结目标创建新 revision 并计算依赖失效，不覆盖进行中版本。

模块验收：同一项目能同时创建口播和纯音乐任务；任何成片均指向确定的 Brief/Product 版本；时长、画幅、语言和预算都能在任务预检看到。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| P01 | 目标与任务范围 | 入口创建 | Project | 结构化简报 / 对话澄清 / 导入既有方案 |
| P02 | 商品事实与主张 | Project | Product | 商品数据连接 / 商家表格导入 / 人工事实录入 |
| P03 | 受众与本地化 | Project, Product | Project, Brief | 已有受众画像 / 规则与市场简报 / 人工本地审阅 |
| P04 | 质量与资源档位 | Project | Project, Cost | 约束预算求解 / 固定成本上限 / 人工选择档位 |
| P05 | 能力与工作流配置 | Project, Capability | Workflow | 能力驱动组合 / 已验证配方复用 / 人工组合节点 |
| P06 | 基线与版本冻结 | Project, Workflow, Policy | Project, Workflow, Policy | 事务快照 / 版本化清单 / 人工确认快照 |

### trend · 热点与创意

热点证据、受众、创意路线

界面行为：输入选定来源或授权热点数据，查看来源时间、热度口径和与商品的关联；对比三种可配置数量的创意路线并选定简报。

命令：`ImportTrendEvidence → VerifyTrendWindow → ProposeCreativeRoutes → ApproveBriefRevision`

权威读取：Project, Product, Metric 历史汇总, Memory, Source。

权威写入：Trend, Brief 候选, DecisionRecord。

事件：trend.evidence_recorded, creative.routes_proposed, brief.revision_approved。

故障恢复：来源失效标过期并停止依赖它的实时趋势主张；无合适热点允许原创产品路线；不得用模拟热度填补缺失。

模块验收：每条热点有 URL/导入文件、采集与发生时间和可见数据；创意被选或拒绝的理由可查，热点失效不破坏已保存证据。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| T01 | 热点来源连接 | Project, Source | Trend | 官方或授权数据源 / RSS/公开公告 / 人工链接与观察 |
| T02 | 趋势核实与过期 | Trend | Trend | 多来源交叉核验 / 可信源白名单 / 人工事实确认 |
| T03 | 主题商品与受众匹配 | Trend, Product, Project | Brief | 语义匹配与约束 / 类目与规则匹配 / 人工选择方向 |
| T04 | 多路线创意 | Brief, Policy | Brief, Script | 多角色独立提案 / 检索成功案例结构 / 人工导演方向 |
| T05 | 同质化预检 | Brief, Cluster, Usage | Brief, Insight | 多层相似度检测 / 规则签名比较 / 人工候选对照 |
| T06 | 创意简报批准 | Brief, Product, Project | Brief, Approval | 规则预授权 / 人工选优 / 小样本概念评审 |

### acquire · 素材获取

上传、授权采集、同步

界面行为：从本机选文件夹或拖入视频，显示相对路径、大小、检测类型、入队/跳过/失败清单；下载网盘素材后可增量导入，或接入有权限的来源适配器。

命令：`ScanSelectedFolder → RegisterImportBatch → HashAndDeduplicate → EnqueueAssetProbe → ResumeImportBatch`

权威读取：Source, Grant, Asset 已知 hash, Capability。

权威写入：Source, Asset 暂存记录, Run, ToolCall, Event。

事件：asset.received, import.file_skipped, import.batch_completed。

故障恢复：文件读取中断按文件或分块继续，只有完整 hash 校验后才提交原件；重选文件夹可匹配已有 hash；浏览器无持续目录权限时用重新选择或本地接入服务，不谎称后台持续监控。

模块验收：同一文件重复导入不重复保存原件；一个坏文件不阻断整批；任务明确哪些文件读到、哪些跳过、哪些需要重新授权。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| A01 | 本地上传与导入 | Project | Source, Asset | 断点分片上传 / 对象存储导入 / 离线清单导入 |
| A02 | 链接与多源获取 | Source, Project | Asset, Source | 官方下载/媒体API / 权利人提供直链 / 人工下载后导入 |
| A03 | 采集适配器管理 | Capability, Project | Capability, Source | 官方适配器 / 授权第三方适配器 / 人工导入适配器 |
| A04 | 来源与权利核验 | Source, Asset | Source, Approval | 结构化许可校验 / 人工审阅授权 / 更换自有/授权素材 |
| A05 | 批量队列与去重 | Source, Cost | Asset, Run | 带额度的队列 / 增量同步 / 人工分批导入 |
| A06 | 增量与失效同步 | Source, Asset, PlatformEvent | Source, Event | Webhook增量 / 周期条件查询 / 人工更新清单 |
| A07 | 本地文件夹批次与断点导入 | Project, Source, ImportBatch, ImportEntry, Asset | ImportBatch, ImportEntry, Source, Asset, BlobLocation | 浏览器目录导入 / 本地连接器同步 / 对象清单接收 |

### generate · AI素材生成

缺口、生成、验收、替换

界面行为：从缺失分镜发起图片/视频/背景生成，查看提示词、参考图、模型能力、费用估算和候选；只重做不合格段并保留已验素材。

命令：`CreateGenerationIntent → ReserveGenerationBudget → SubmitGenerationJob → ReconcileGenerationResult → AcceptGeneratedAsset`

权威读取：Plan, Product, Source, Capability, Cost, TaskContract。

权威写入：Run, ToolCall, Cost, Asset, AssetVersion, DecisionRecord。

事件：generation.submitted, generation.reconciled, generated_asset.accepted。

故障恢复：供应商超时先查原 job；能力/权限不支持则拒绝提交；图像事实不符进入质量返工，不能自动用不存在产品细节填真实商品镜头。

模块验收：每个接受候选含 prompt/reference/model/参数/供应商 job/产物 hash 和实际费用；重复回调只落一次产物；生成内容进入与外采素材相同的质量及来源流程。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| G01 | 镜头缺口规划 | Plan, Product, Project | Plan, Run | 分镜缺口驱动 / 创意候选先生成 / 人工生成简报 |
| G02 | 图片生成与参考 | Plan, Source, Product | Asset, ToolCall, Source | 已授权图像API / 本地开放模型 / 自有摄影或素材库 |
| G03 | 视频生成与重绘 | Plan, AssetVersion, Source | Asset, ToolCall, Source | 视频模型API / 本地视频模型 / 实拍/库存/图文动效 |
| G04 | 异步生成与费用核对 | Run, ToolCall, Cost | ToolCall, Cost, Asset | 任务ID查询+回调 / 同类服务切换 / 人工核对并接管 |
| G05 | 生成候选验收 | Asset, Product, Plan | AssetVersion, Approval | 自动技检+多模态审核 / 独立模型交叉复核 / 人工选优或替代实拍 |
| G06 | 生成版本与局部重做 | AssetVersion, Plan, ToolCall | AssetVersion, Plan | 局部编辑能力 / 单镜头重生成 / 替换库存镜头 |

### ingest · 入库与来源

原件、逐素材配方、变换实例、时码映射、存储位置

界面行为：检查原视频，套用可调的水印/字幕清理、1.011–1.022 缩放、音量、调色、保序精剪、随机图层和插入片段配方；并排预览后批准衍生版入库。

命令：`ProbeMedia → ArchiveOriginal → PreviewTransformRecipe → ExecuteTransformPlan → ValidateDerivative → CommitAssetVersion`

权威读取：Asset, Source, Policy, Capability, Tag。

权威写入：AssetVersion, ToolCall, ChangeSet, Audit, Event。

事件：asset.original_archived, asset.derivative_created, asset.available, asset.rights_revoked。

故障恢复：任何处理不覆盖原件；处理失败保留参数、日志和临时对象待清理；关键遮挡修复失败可换源；撤权从可选池移除并传播到未来发布任务，历史记录保留可审计标记。

模块验收：从衍生版能还原顺序操作、实际参数、seed、父版本、时间/空间映射和存放对象；最大音量不能导致削波；质量失败不得标 available。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| I01 | 隔离与媒体探测 | Asset, Source | Asset, Audit | 隔离沙箱探测 / 第二解码器校验 / 人工修复或退回 |
| I02 | 原件与对象归档 | Asset, Source | Asset, Event | 对象存储+事务元数据 / 备用存储端 / 本地离线暂存 |
| I03 | 衍生与画面变换 | Asset, AssetVersion | AssetVersion, Timeline | 非破坏变换参数 / 生成代理/转码副本 / 外部编辑后导回 |
| I04 | 技术归一与代理 | Asset | AssetVersion | 标准媒体执行器 / 备用编码后端 / 直接使用原件 |
| I05 | 入库批准与分层 | Asset, Tag, Approval | AssetVersion, Event, Approval | 策略化自动批准 / 人工审核队列 / 限制用途入库 |
| I06 | 撤权与删除传播 | Source, Asset, Usage, Publication, Post | Event, Audit | 依赖图传播失效 / 事务账本全量对账 / 人工隔离与逐项核销 |
| I07 | 逐素材处理配方与重放 | Asset, AssetVersion, Source, TransformRecipe, Tag, BlobLocation | TransformRecipe, TransformRun, AssetVersion, TimeMap, BlobLocation | 逐素材自适应 / 固定配方批处理 / 人工锁定局部 |

### label · 多模态标注

镜头、主体、动作、场景

界面行为：展开视频镜头条，查看商品/数量/动作/场景/字幕/声音/画质标签及对应帧段；可人工纠错、锁定 SKU、拆合镜头并独立提取音轨。

命令：`DetectShots → SampleFramesForAnalysis → AnnotateMultimodalEvidence → CorrectTagAssertion → ExtractAudioAsset`

权威读取：AssetVersion, Product, Source, 标签词表版本。

权威写入：Shot, Tag, Audio, DecisionRecord, Event。

事件：asset.shots_indexed, tag.assertion_recorded, tag.corrected, audio.extracted。

故障恢复：低置信、跨帧不一致和 OCR/ASR 冲突保留 unknown；人工修正使对应索引和分镜候选失效；分析抽帧不修改视频播放内容。

模块验收：标签具有版本、来源帧/音频区间、模型/人工作者和置信依据；查某 SKU 能跳到真实出现区间；音轨有父素材和样本时基。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| L01 | 镜头切分与采样 | AssetVersion | Shot | 场景检测+自适应采样 / 固定间隔加关键事件 / 人工切镜头 |
| L02 | 主体商品与数量 | Shot, Product | Tag | 视觉模型+检测证据 / 专类检测/分割 / 人工核验与标签 |
| L03 | 场景动作与连续性 | Shot | Tag | 短时序视觉分析 / 关键帧组合分析 / 人工分段标注 |
| L04 | 文字对白与声音 | Shot, Audio | Tag, Script | OCR+ASR组合 / 备用识别器 / 人工字幕/文本导入 |
| L05 | 编辑作用与画质 | Shot, Tag, Brief | Tag, AssetScore | 规则+视觉评估 / 技术指标先行 / 人工导演评审 |
| L06 | 标签体系与纠错 | Tag, Product | Tag, Event | 结构化可扩展本体 / 自由标签加归一映射 / 人工合并拆分 |
| L07 | 原声提取与音轨建档 | Asset, AssetVersion | Audio | 媒体探测后分离音轨 / 备用解码器/原始音频导入 / 人工录制或明确无声 |

### search · 索引与聚类

检索、相似族、索引版本

界面行为：按商品、动作、画幅、质量、授权、长度和使用压力筛选；语义搜索后查看每个命中片段、相似族和选中理由。

命令：`BuildVersionedIndex → QueryAssets → HybridRankCandidates → ReconcileDuplicateFamily → RebuildIndexPartition`

权威读取：AssetVersion, Shot, Tag, Source, Usage, AssetScore, Index, Cluster。

权威写入：Index, Cluster, 候选查询证据, DecisionRecord。

事件：index.partition_ready, duplicate.family_updated, search.candidates_ranked。

故障恢复：向量/图服务不可用降级全文和条件检索；过期索引不能让已撤权素材重新出现；近似相似不能自动删除不同原件，保留相似度方法与人工裁决。

模块验收：精确 hash 去重、视觉近似和语义同类分开；结果能回到镜头证据；重建索引不改变权威素材 ID，索引水位和模型版本可见。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| S01 | 全文与条件检索 | Tag, AssetVersion, Project | Index, Shot | 结构化过滤+全文索引 / 事务库条件查询 / 人工筛选集合 |
| S02 | 语义与混合检索 | Index, VoiceMap, Brief | Shot, AssetScore | 多模态混合检索 / 文本语义+条件过滤 / 关键词与人工召回 |
| S03 | 精确与近似重复 | Asset, AssetVersion, Shot | Cluster | 哈希+感知指纹 / 局部特征与时序比对 / 人工对照疑似组 |
| S04 | 语义聚类与素材族 | Tag, Index, Cluster | Cluster | 多特征聚类 / 来源/拍摄批次归组 / 人工族管理 |
| S05 | 索引更新与迁移 | Tag, AssetVersion, Event | Index, Event | 增量索引+对账 / 影子全量重建 / 事务库降级检索 |
| S06 | 候选证据与重排 | Shot, AssetScore, VoiceMap | Plan, Insight | 约束重排 / 规则优先排序 / 人工锁定候选 |

### assetops · 素材沉淀

质量、频率、曝光、冷却

界面行为：查看素材资产卡：原件/衍生、质量、标签完整度、使用片段、成片、账号、帖子和实际效果；设置冷却、探索比例和归档。

命令：`ReserveAssetUsage → CommitClipUsage → RecalculateAssetEvidenceScore → ApplyCooldown → ArchiveOrRestoreAsset`

权威读取：AssetVersion, Shot, Tag, Usage, Metric, Post, Experiment, Policy。

权威写入：Usage, AssetScore, AssetVersion 可用状态, DecisionRecord。

事件：asset.usage_reserved, clip.usage_committed, asset.score_updated, asset.cooldown_changed。

故障恢复：预留过期释放并保留审计；并发以租约防冲突；缺效果不补零；评分算法升级保留旧分数与新版本，不能把全部成片效果直接赋给一个片段。

模块验收：任一素材能查出全部成片内的出现区间和重用次数；实际发布计数与草稿使用区分；综合分权重、样本、时间窗、缺失项和适用范围透明。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| U01 | 素材使用账本 | Timeline, Artifact, Publication, Post, Metric, RenderManifest, TimeMap | Usage, Event | 时间线展开写账 / 导出清单回填 / 人工补录与核对 |
| U02 | 并发预留与配额 | Shot, Cluster, Usage, Plan | Usage, Plan | 事务预留+租约 / 分区额度发放 / 串行人工分配 |
| U03 | 质量与效果分层 | AssetScore, Metric, Usage, Insight | AssetScore | 多维独立评分 / 规则质量+人工效果 / 人工精选池 |
| U04 | 冷却与受众疲劳 | Usage, Cluster, AssetScore | AssetScore, Policy | 曝光加权冷却 / 次数与时间窗冷却 / 人工禁用/保留池 |
| U05 | 探索与长期沉淀 | AssetScore, Usage, Policy | AssetScore, Plan | 约束探索分配 / 新旧素材配额 / 人工专题选集 |
| U06 | 降级归档与复苏 | AssetVersion, AssetScore, Source | AssetVersion, Event | 分层存储规则 / 低优先级候选池 / 人工归档复苏 |
| U07 | 素材评价向量与证据校准 | AssetVersion, Tag, Usage, Metric, AssetScore, Policy, Hypothesis | AssetScore | 证据评分 / 冷启动质量评分 / 专家修订 |

### voice · 脚本与Voice Map

口播、词时码、语义画面

界面行为：编辑脚本并绑定商品事实，导入或生成口播；在词句波形上校正时间锚，拖选对应商品/动作镜头，看到未对齐价格或数量。

命令：`CreateFactBoundScript → ImportOrSynthesizeVoice → AlignTranscript → BuildSemanticAnchors → BindVisualEvidence → RevalidateAlignment`

权威读取：Product, Script, Audio, Shot, Tag, Timeline, Source。

权威写入：Script, Audio, VoiceMap, AlignmentBinding, ChangeSet。

事件：voice.audio_created, voice.anchors_aligned, alignment.binding_updated, alignment.invalidated。

故障恢复：数字/金额或噪声导致缺时码时显式 untimed，允许人工修正；音频或脚本变更局部失效；无口播任务为 not_applicable，继续音乐/画面路线。

模块验收：每个语义锚可从词句跳到音频与画面；数量、动作和 SKU 分别有证据；错画面不能仅因时间卡准而通过；必要语义优先于可选卡点。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| V01 | 脚本生成与事实绑定 | Brief, Product, Policy | Script | 模型生成+事实约束 / 结构库辅助写作 / 人工脚本导入 |
| V02 | 翻译发音与读法 | Script, Product, Project | Script | 本地化模型+术语表 / 专业翻译服务 / 母语人工校正 |
| V03 | 口播生成或导入 | Script, Source | Audio, Source | 合规TTS服务 / 本地TTS / 真人配音/上传 |
| V04 | 转录与词句时码 | Audio | Script, VoiceMap | ASR+强制对齐 / 另一ASR/对齐器 / 人工字幕及打点 |
| V05 | 语义锚点与实体 | VoiceMap, Script, Product | VoiceMap | 实体与关系抽取 / 规则词典匹配 / 人工语义标注 |
| V06 | 语义画面对齐 | VoiceMap, Shot, Tag, AlignmentBinding | Plan | 约束匹配与重排 / 人工锁镜头+自动补空 / 生成缺失镜头/改脚本 |
| V07 | 卡点容差与节奏 | VoiceMap, Music, Plan | Plan, Mix | 多约束时间求解 / 语义优先对齐 / 人工调节关键点 |
| V08 | 变更失效与重对齐 | Audio, Script, VoiceMap, Event, ChangeSet | VoiceMap, Event | 依赖区间增量更新 / 全音频重新对齐 / 人工定位确认 |
| V09 | 语义绑定证据 | Script, VoiceMap, Audio, Product, Shot, Tag, Plan, Timeline, TaskContract, DecisionRecord, Approval, AlignmentBinding | AlignmentBinding, DecisionRecord, Audit | 证据约束自动绑定 / 人工关键锚点与证据锁定 / 缺口保留并重选/改稿 |

### music · BGM与声音

节拍、情绪、混音、权利

界面行为：选择授权音乐，浏览波形、拍点/强拍/乐段，调整保留段、音量包络、ducking 与声效；原声、口播、BGM 可独立开关。

命令：`AnalyzeMusicStructure → ValidateMusicUsage → ProposeBeatAnchors → BuildMixPlan → MeasureAndNormalizeAudio → VerifySync`

权威读取：Music, Audio, VoiceMap 可选, Timeline, Source, Project。

权威写入：Music, Mix, ToolCall, DecisionRecord。

事件：music.beats_analyzed, mix.version_created, mix.qc_completed。

故障恢复：不可靠节拍允许手动关键点，不强制匀速 BPM；许可不适用换曲或选择平台内人工附曲；响度/削波失败调整混音并重测，不能仅放大原声。

模块验收：无口播也能产出卡点/纯音乐视频；每个实际音轨进入最终混音 manifest；输出有实测响度/真峰值及音画同步结果，试听能定位具体问题段。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| B01 | 音乐与声音理解 | Audio, Asset | Music, Tag | 多路音频分析 / 本地分析模型 / 信号处理与人工描述 |
| B02 | 拍点强拍与段落 | Audio, Music | Music | 适用节拍模型 / 信号算法检测 / 人工打点或不贴拍 |
| B03 | 音乐选择与许可 | Music, Project, Source, Account | Music, Approval | 平台商业曲库 / 已授权曲库/原创音乐 / 无BGM/仅环境声 |
| B04 | BGM生成与改编 | Brief, Plan, Source | Music, Asset, Source | 音乐生成服务 / 本地音乐模型 / 授权库存剪配 |
| B05 | 混音与响度 | Audio, Music, Timeline, ChangeSet | Mix | 可复现音频处理链 / 备用处理后端 / 人工混音导入 |
| B06 | 音画同步与缺陷检查 | Mix, VoiceMap, Timeline | Approval, Audit | 自动检测+关键区间审阅 / 第二播放器/独立检测 / 人工逐段听看 |

### plan · 分镜与选材

多方案、约束、衔接、预留

界面行为：在模板、非模板或混合路线之间选择；对比分镜卡片，替换候选、固定关键商品镜头、设置节奏和画幅，查看缺素材槽位。

命令：`ProposeStoryPlans → RetrieveShotCandidates → SolveDurationAndContinuity → ReserveSelectedShots → CommitPlanVersion`

权威读取：Brief, Product, VoiceMap 可选, Music, Shot, Usage, Capability。

权威写入：Plan, TaskContract 子任务, Usage 预留, DecisionRecord。

事件：plan.candidates_ready, plan.shot_gap_detected, plan.version_committed。

故障恢复：时长/顺序/语义不可同时满足时返回冲突槽位；可延长、删可选段、换素材或补拍，但不能默默违反硬约束；预留失败重新选材。

模块验收：同一任务至少可表示不同内容路线；每镜头有目的、时长、入出点、来源与转场条件；原素材相对顺序规则能被验证；缺口不是空白成片。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| N01 | 创作结构与路由 | Brief, Project, Policy, VoiceMap | Plan | 多候选导演规划 / 结构库自由拼装 / 人工分镜/文件夹规则 |
| N02 | 镜头召回与选择 | VoiceMap, Shot, Tag, AssetScore, Plan, AlignmentBinding | Plan | 多模态约束召回 / 条件索引/文件夹选材 / 人工选片加缺口生成 |
| N03 | 时长衔接与可解性 | Plan, VoiceMap, Music, Tag, AlignmentBinding | Plan | 约束求解+连续性评分 / 规则搜索与局部优化 / 人工解冲突/改简报 |
| N04 | 变体与重复控制 | Plan, Cluster, Usage, Policy | Plan | 分镜组合差异优化 / 分层轮换与配额 / 人工创意改写/补拍 |
| N05 | 规格安全区与节奏 | Plan, Project, Account, Capability | Plan | 参数化版式与节奏 / 通用竖屏母版再适配 / 人工重构关键画面 |
| N06 | 素材预留与定稿 | Plan, Usage, AssetVersion, Approval | Plan, Event | 事务预留后冻结 / 分区配额批量冻结 / 人工分配串行定稿 |
| N07 | 缺口与局部返工 | Plan, Audit, Event, ChangeSet | Plan, Brief | 缺口驱动局部重规划 / 扩大候选池再匹配 / 改脚本/补拍/人工接管 |

### edit · 编辑与审阅

时间轴、局部修改、版本

界面行为：预览并裁切、分割、保序精剪、插入说明、加字幕/图层、调色和混音；用对话做局部修改，锁住不许变化的轨道/范围，对比版本后锁版。

命令：`ImportTimelineIR → ProposeTimelinePatch → ComputeImpactSet → ApplyChangeSetCAS → PreviewAffectedRanges → LockTimelineRevision`

权威读取：Plan, Timeline, AssetVersion, Mix, AlignmentBinding, Approval。

权威写入：Timeline, ChangeSet, Usage, DecisionRecord, Audit。

事件：timeline.patch_proposed, timeline.revision_committed, timeline.locked, dependency.invalidated。

故障恢复：版本冲突返回三方差异，不覆盖他人修改；不支持效果显示兼容缺口；操作撤销创建逆 patch；锁定范围冲突要求具体取舍，不能整片重做抹掉人工编辑。

模块验收：任一输出区间可查使用的素材与源区间；局部 patch 不改无关轨道；变速/插帧/删帧后语音、字幕、音乐映射一致；模板和非模板共用 Timeline IR。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| E01 | 统一时间线与导入 | Plan, AssetVersion, Audio, Mix, AlignmentBinding, DeliveryPackage | Timeline | 内部规范时间线 / Hyperframes适配导入 / 外部编辑清单回导 |
| E02 | 基础剪辑与音轨编辑 | Timeline, AssetVersion, Audio | Timeline | 原生时间线操作 / Hyperframes编辑SDK / 外部编辑器交接 |
| E03 | 实时预览与定位 | Timeline, VoiceMap, AssetVersion | View, Audit | 代理媒体逐点预览 / 局部短片预渲染 / 外部播放器审阅 |
| E04 | 字幕文字与多语言 | Timeline, Script, VoiceMap, Project, AlignmentBinding | Timeline | 时间码字幕编辑 / SRT/ASS等字幕导入 / 手动文本层与打点 |
| E05 | 画面音效与版式 | Timeline, Mix, Project | Timeline, Mix | 可组合效果栈 / 预烘焙局部效果 / 人工简化视觉版本 |
| E06 | AI局部修改与人工锁 | Timeline, Brief, Approval | Timeline, ToolCall | 结构化编辑patch / MEP/工具协议适配 / 人工执行建议步骤 |
| E07 | 历史撤销与协作冲突 | Timeline, Event | Timeline, Audit | 版本事务+逆操作 / 分支编辑后合并 / 独占编辑租约 |
| E08 | 成片审阅与锁版 | Timeline, Audit, Source, Project | Approval, Timeline, Audit | 规则检查+人工关键点 / 双人/专家复核 / 退回局部修正 |
| E09 | 制作变更差异与精确重算 | Timeline, ChangeSet, Script, Audio, Music, Mix, Product, AlignmentBinding, RenderManifest, Publication, Approval, Grant, TaskContract, DecisionRecord, ProductionTrace | ChangeSet, Timeline, Event, DecisionRecord, Audit | 字段与区间依赖差量提交 / 保守扩段或重建新分支 / 人工选段合并与受限接管 |

### render · 渲染与交付

规格、队列、质检、导出

界面行为：选择尺寸、帧率、编码、封面和交付格式，冻结依赖；查看队列、分段进度和真实渲染日志，导出成片/工程并支持外部编辑回导。

命令：`FreezeRenderManifest → ReserveRenderResources → ExecuteRenderJob → VerifyWholeArtifact → PackageDelivery → ValidateExternalRoundtrip`

权威读取：Timeline 锁版, AssetVersion, Mix, Capability, Cost, RenderManifest。

权威写入：RenderJob, Artifact, RenderManifest 实际值, DeliveryPackage, Cost, Audit。

事件：render.queued, render.segment_committed, artifact.qc_passed, delivery.package_ready。

故障恢复：缺字体/素材先拒绝；可重用 hash 一致且已验的片段；后端切换验证效果兼容；全片质检失败保留产物为 rejected，不伪装成功导出。

模块验收：最终文件尺寸/帧率/时长/音轨实测匹配合同；manifest 列实际依赖、编码器构建、时间映射和输出 hash；外部回导能识别新增/缺失素材与不可逆效果。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| Y01 | 渲染规格与依赖冻结 | Timeline, Account, Capability, Approval, Mix | RenderJob | 能力匹配渲染配置 / 通用可移植母版 / 人工配置与短样测试 |
| Y02 | 队列资源与限流 | RenderJob, Cost, Capability | RenderJob, Cost | 队列+资源租约 / 云/本地备用工作池 / 串行或延迟渲染 |
| Y03 | 多渲染后端 | RenderJob, Timeline, Mix, RenderManifest | Artifact, RenderJob | FFmpeg媒体管线 / Hyperframes浏览器渲染 / 外部渲染服务/人工交付 |
| Y04 | 分段恢复与产物一致 | RenderJob, Artifact, Event, RenderManifest, ChangeSet | RenderJob, Artifact | 确定性分段缓存 / 整条从最近完整版本重跑 / 人工转移任务包 |
| Y05 | 全片技术与内容质检 | Artifact, Timeline, VoiceMap, Product, AlignmentBinding, RenderManifest | Audit, Approval | 独立解码检测+语义抽检 / 第二工具链交叉验证 / 人工完整播放验收 |
| Y06 | 成片与工程导出 | Artifact, Timeline, Usage, Approval, RenderManifest, DeliveryPackage | Artifact, Event | 平台下载/对象存储链接 / 打包离线交付 / 外部存储同步 |
| Y07 | 封面与多尺寸派生 | Artifact, Timeline, Project | Artifact | 时间线派生重排 / 成片安全裁切转码 / 人工封面与专用版 |
| Y08 | 渲染Manifest与逐片谱系 | RenderJob, Timeline, Mix, AlignmentBinding, AssetVersion, Audio, Source, Artifact, RenderManifest, Run, ToolCall, Approval | RenderManifest, Audit | 冻结清单加执行回执补全 / 外部文件与保守映射导入 / 人工逐段核对与整片重建 |
| Y09 | 交付包与外部编辑回导 | Artifact, Timeline, RenderManifest, DeliveryPackage, Source, Approval, Grant, AssetVersion, Audio, ChangeSet, ProductionTrace, View | DeliveryPackage, Artifact, Timeline, ChangeSet, Audit, Event | 兼容适配器的工程往返 / 成片/代理与批注的受限往返 / 人工清单交接与逐项回导 |

### account · 账号与排程

授权、分配、时区、配额

界面行为：连接真实账号，看到已获 scope/可用发布类型/数据字段/授权期限；为视频分配账号和当地发布时间，查看额度占用和人工范围。

命令：`RegisterOAuthGrant → ProbeAccountCapabilities → AssignArtifactToAccount → ReserveScheduleSlot → ValidateScheduleVersion`

权威读取：Account, Grant, Capability, Artifact, Policy, Cost。

权威写入：Account, Grant 引用, Capability 快照, Publication 草稿, Approval。

事件：account.authorized, account.capability_changed, publication.schedule_reserved, grant.revoked。

故障恢复：令牌撤销暂停依赖任务；配额不足进入排队或人工调整；保留 UTC 与原始时区，处理 DST 歧义；能力未核实不能把按钮标可自动完成。

模块验收：同一成片跨账号映射分别存在；排程可解释真实本地时间；用户能分辨 Content Posting/Business/Shop 不同授权；所有能力带检测时间和限制。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| J01 | 账号与应用授权 | Project, Capability, Approval | Account, Grant | 正规OAuth/平台授权 / 减少权限的有限接入 / 人工账号登记 |
| J02 | 令牌健康与撤权 | Account, Grant, PlatformEvent | Grant, Account, Event | 后端自动刷新与轮换 / 重新授权连接 / 暂停自动化并保留人工运营 |
| J03 | 视频与账号分配 | Artifact, Project, Account, Product, Usage | Publication | 约束分配+人工可调 / 规则队列分配 / 人工拖拽分配 |
| J04 | 排期日历与时区 | Publication, Account, Policy | Publication, Event | 应用调度器 / 平台草稿/原生排程交接 / 导出提醒与人工发布 |
| J05 | 账号配额与并发预留 | Account, Capability, Publication, Cost | Cost, Publication | 账号级事务令牌桶 / 保守本地配额分区 / 降低频率或延期 |
| J06 | 身份权限与审批范围 | Account, Grant, Project, Approval, Artifact | Approval | 角色+账号范围授权 / 逐次发布确认 / 交接账号责任人 |
| J07 | 账号能力测试与降级 | Account, Grant, Capability, Audit | Capability, Account | 只读能力探测+官方规则 / 沙盒/测试账号验收 / 人工核对后台权限 |

### publish · 发布与回执

提交、核对、审核、公开

界面行为：预览最终成片、标题、隐私/交互/商业披露/商品绑定和账号，按实际获批通道提交；查看上传、审核、公开状态，支持取消和人工回填。

命令：`PreflightPublication → FreezePublicationIntent → SubmitApprovedIntent → ReconcileRemoteStatus → BindActualPost → CancelOrCompensate`

权威读取：Artifact, RenderManifest, Account, Grant, Capability, Approval, Product。

权威写入：Publication, PlatformEvent, Post, ToolCall, Audit, Usage 曝光映射。

事件：publication.submitted, publication.outcome_unknown, post.bound, post.visibility_changed, publication.cancelled。

故障恢复：已提交超时先按原 publish/job ID 查询；重复 webhook 幂等；草稿等待平台端完成；无适用 API 生成待发布包并核验人工回执；取消不虚构撤回已经公开的帖子。

模块验收：发布意图和实际 Post 分开；只有真实 ID/可见性回执才标 published；修改成片会新建意图版本并使旧审批过期，不能悄换在途文件。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| F01 | 发布通道选择 | Account, Grant, Capability, Publication | Publication | Business Accounts发布 / Content Posting或Shop专用通道 / 导出并人工发布 |
| F02 | 发布前校验与披露 | Publication, Artifact, Account, Source, Capability | Approval, Publication | 官方creator/preflight+本地检查 / 平台内草稿检查 / 人工审查后交接 |
| F03 | 带商品与商业信息 | Publication, Product, Account, Capability, DeliveryPackage, Artifact | Publication, Approval, DeliveryPackage | Shop shoppable接口 / 平台草稿内人工挂商品 / 只导出待挂商品交接包 |
| F04 | 一键确认与任务提交 | Publication, Approval, Artifact, Cost, DeliveryPackage | Publication, ToolCall | 受控API批量任务 / 草稿上传交接 / 人工发布任务包 |
| F05 | 异步上传与结果核对 | Publication, ToolCall, PlatformEvent | Publication, PlatformEvent | 官方状态查询+验证回调 / 轮询与人工账号核对 / 冻结重发并人工结案 |
| F06 | 审核可见性与最终映射 | Publication, PlatformEvent, Account, Post | Publication, Event, Post | 官方状态+post_id核实 / 人工打开本人帖子核对 / 保留处理中/未知 |
| F07 | 取消撤下与补偿 | Publication, Approval, PlatformEvent, Event, Post | Publication, Audit, Post | 取消本地任务并调用允许的撤下能力 / 平台后台人工撤下 / 冻结后续使用与持续核对 |
| F08 | 批次复盘与人工回填 | Publication, Artifact, Account, Audit | Publication, Event, Post | 帖子ID校验回填 / 平台导出清单对账 / 人工逐条关联 |
| F09 | 版本漂移与在途发布迁移 | Publication, Artifact, ChangeSet, Approval, Grant, PlatformEvent, ToolCall, Post, Account, Capability, ProductionTrace | Publication, Event, Audit, DecisionRecord | 未发送意图的版本化复验 / 已提交或未知结果的对账后迁移 / 冻结自动写入并交责任人处置 |

### metrics · 数据与评论

指标快照、评论、权限

界面行为：从账号或帖子查看可获取的播放/互动/观看质量/商品数据，导入官方报表，展开采集时间、口径、分母、延迟和原始回执；从数据跳回成片及素材。

命令：`CollectAuthorizedMetrics → ValidateMetricDefinition → StoreMetricSnapshot → BackfillLateWindow → QueryProductionTrace`

权威读取：Post, Publication, Account, Grant, Capability, Usage, Artifact。

权威写入：Metric, Comment, PlatformEvent, ProductionTrace 投影, Audit。

事件：metrics.snapshot_recorded, metrics.window_backfilled, metrics.quality_flagged, production.trace_projected。

故障恢复：无权限字段写 unavailable，不写 0；迟到数据新建快照；速率限制有界排队；导入列名/单位/时区不明时阻止参与比较并提示映射。

模块验收：任一数值可追到来源和口径；自然/付费流量与不同观察窗不混合；评论分页和删除状态可追踪；全链视图能指出缺失链接而非编造路径。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| M01 | 授权数据采集 | Publication, Account, Grant, Capability, Post | Metric, PlatformEvent | Business insights/Shop授权接口 / Display基础数据 / 官方导出或人工导入 |
| M02 | 指标字典与数据质量 | Metric, PlatformEvent, Capability | Metric, Audit | 版本化指标契约 / 源内分析不跨源合并 / 人工核对并隔离未知 |
| M03 | 观看留存与互动 | Metric, Publication, Timeline, VoiceMap, Post | Insight | Business观看质量分析 / 基础播放互动趋势 / 平台图表人工导入 |
| M04 | 评论采集与洞察 | Publication, Account, Grant, Comment, Post | Comment, Insight | 自有账号评论接口 / 官方导出/人工摘录 / 仅使用站内人工反馈 |
| M05 | 带货效果与归因 | Metric, Product, Publication, Experiment, Post | Metric, Insight | Shop授权分析 / 合规导出对账 / 内容效果单独优化 |
| M06 | 延迟回补与窗口切片 | Metric, Capability, Event | Metric, Event | 按端点窗口分片回补 / 保守短窗口+对账 / 定期官方导出补齐 |
| M07 | 数据面板与可追溯查询 | Metric, Publication, Artifact, Usage, Insight, Post, ProductionTrace | View, Insight | 可追溯分析视图 / 预聚合报表 / 导出明细手工核对 |
| M08 | 全程生产轨迹回溯 | Product, Script, Audio, VoiceMap, AlignmentBinding, Shot, Tag, Plan, Timeline, Mix, RenderManifest, Artifact, DeliveryPackage, Account, Publication, Post, Metric, Comment, Insight, Experiment, Policy, Run, DecisionRecord, ChangeSet, Event, TaskContract, Grant, Approval, ProductionTrace, ReplayManifest, BlobLocation, TransformRun, TimeMap | ProductionTrace, View | 按版本与事件增量构建轨迹 / 按作品点查事实快照 / 只读审计视图快照 |
| M09 | 片段贡献假设与异构复盘 | Metric, Post, Usage, Timeline, VoiceMap, Music, Hypothesis, AgentDeployment, DecisionRecord | Hypothesis, Insight, DecisionRecord | 区间复盘 / 帖子级对照 / 受控实验复盘 |

### learn · 分析与实验

归因、候选、测试、晋升

界面行为：选择基线和候选视频，提出开头/镜头/节奏/文案等改动假设，运行多 Agent 复盘；设置实验变量、观察窗、样本门槛和负向护栏并比较结果。

命令：`FreezeAnalysisCohort → RunMultiAgentDiagnosis → CreateExperiment → EvaluateFrozenSuite → PromoteOrRollbackPolicy`

权威读取：Metric, Post, Usage, Artifact, Experiment, Policy, EvaluationSuite, DecisionRecord。

权威写入：Insight, Experiment, Policy 候选/晋升版本, Memory 候选, DecisionRecord。

事件：insight.hypothesis_proposed, experiment.started, experiment.evaluated, policy.promoted, policy.rolled_back。

故障恢复：样本不足/混杂无法排除标 inconclusive；不不断偷看数据后随意提前停止；回归失败保留旧策略；负向护栏触发停止后对未发布任务撤销候选。

模块验收：为什么好/差明确拆为可观察内容问题、统计关联和待实验原因；策略晋升能复算同批数据；素材贡献不能由单条全片结果直接确定。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| X01 | 分析口径与混杂因素 | Metric, Publication, Usage, Project, Post | Insight | 分层分析与匹配 / 同账号同窗口对照 / 人工核验后描述统计 |
| X02 | 基线与候选策略 | Policy, Insight, Workflow, DecisionRecord, TaskContract, PreferenceProfile, EvaluationSuite | Policy, Experiment, ChangeSet | 版本分支与配置差异 / 可验证配方复制 / 人工制定最小候选 |
| X03 | 离线评测与回归 | Policy, Experiment, Timeline, VoiceMap, Artifact, EvaluationSuite, TaskContract | Experiment, Insight, DecisionRecord | 冻结样本集重放 / 影子计划对比 / 人工盲评与关键例回归 |
| X04 | 试验分配与小流量 | Experiment, Policy, Account, Cost, Approval, Artifact | Experiment, Publication | 可行的随机分组试验 / 分阶段账号或时段对照 / 仅离线候选与人工试投 |
| X05 | 样本与试验护栏 | Experiment, Metric, Cost, Audit, EvaluationSuite | Experiment, Insight, DecisionRecord | 预定义统计与业务护栏 / 序贯监测规则 / 人工证据审阅或延长观察 |
| X06 | 受控晋升与生效 | Experiment, Policy, Approval, Workflow, DecisionRecord, ChangeSet | Policy, Workflow, Event | 事务化策略指针切换 / 分项目分批晋升 / 人工批准后定时生效 |
| X07 | 回滚与失败学习 | Policy, Experiment, Insight, Run, DecisionRecord, ChangeSet | Policy, Experiment, Event, Memory, DecisionRecord, ChangeSet | 自动护栏回滚 / 人工选择已验证版本 / 暂停扩量并用保守配方 |
| X08 | 评测套件与故障注入 | TaskContract, EvaluationSuite, Source, Capability, Run, Audit, Policy | EvaluationSuite, Insight, Audit | 冻结案例加可控故障注入 / 影子流量与变形关系测试 / 人工盲评与表格化关键例 |

### agents · Agent协作

异构部署、证据并行、质询裁决、有界改图

界面行为：按任务建立导演/素材/声音/剪辑/质检/分析小队，逐角色配置模型和工具；查看分工、依赖、证据、质询和裁决，接管或只重跑某节点。

命令：`CompileSquadPlan → LeaseAgentTask → SubmitEvidencePacket → ChallengeClaims → ArbitrateReview → CommitRunGraphPatch`

权威读取：AgentRole, SkillVersion, Capability, TaskContract, RunGraph, Policy, Cost, Approval。

权威写入：TaskContract, RunGraph, Run, DecisionRecord, ChangeSet, Event。

事件：agent.task_leased, agent.evidence_submitted, agent.disagreement_recorded, run_graph.patch_committed, agent.handoff_requested。

故障恢复：子 Agent 超时释放/转移租约并保留证据；分歧不以票数直接决定事实；图变更做 CAS 和影响校验；递归/预算上限阻止无限委派。

模块验收：至少一个任务可用不同能力模型协作；每角色输出有 schema 与证据；旧 lease 不能提交；人工锁不被重规划覆盖；不依赖单 Agent 自评通过。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| C01 | 角色与能力编组 | Project, Workflow, Capability, Cost, TaskContract, AgentRole, SkillVersion | Workflow, AgentRole, DecisionRecord | 按能力组合角色池 / 异构小队串行执行与独立复核 / 人机混合分工 |
| C02 | 动态依赖规划 | Project, Workflow, Capability, Plan, TaskContract, AgentRole, SkillVersion, RunGraph, PreferenceProfile | Workflow, RunGraph, DecisionRecord | 类型化DAG规划与校验 / 已验证子流程拼装 / 人工可视化编排 |
| C03 | 派工与执行租约 | Workflow, Capability, Run, Cost, TaskContract, RunGraph, AgentRole, SkillVersion | Run, Event, RunGraph | 依赖就绪的优先级队列 / 受限串行调度 / 人工领取任务 |
| C04 | 并发冲突与人工锁 | Run, Timeline, Policy, Event, RunGraph, ChangeSet, DecisionRecord | Run, Timeline, Event, ChangeSet, DecisionRecord | 对象版本CAS与短租约 / 隔离分支后显式合并 / 单写者人工仲裁 |
| C05 | 局部重跑与重新规划 | Event, Workflow, Run, Timeline, RunGraph, ChangeSet, TaskContract | Workflow, Run, Event, ChangeSet, RunGraph | 依赖哈希与区间失效传播 / 阶段级检查点恢复 / 人工圈定重做范围 |
| C06 | 交接与人工接管 | Run, Approval, Audit, Workflow, TaskContract, AgentRole | Run, Approval, Event | 结构化接管单 / 授权的替代角色接续 / 延期或导出工作包 |
| C07 | 共享上下文与独立复核 | Run, Memory, Insight, Product, TaskContract, AgentRole, DecisionRecord | Run, Insight, Approval, DecisionRecord | 证据包加独立复核角色 / 确定性规则与抽样人审 / 专家联合审阅 |
| C08 | 目标与验收契约 | Project, Brief, PreferenceProfile, Policy, TaskContract | TaskContract, DecisionRecord, Event | 按目标自动拆契约 / 复制已验证任务配方 / 人工任务单与验收清单 |
| C09 | 证据化分歧裁决 | TaskContract, DecisionRecord, Insight, Approval, AgentRole, Run | DecisionRecord, Insight, Approval | 证据矩阵与独立裁判 / 确定性约束先裁决 / 有权人员对照审阅 |
| C10 | 安全局部改图事务 | ChangeSet, RunGraph, TaskContract, Run, Approval, DecisionRecord, EvaluationSuite | ChangeSet, RunGraph, Workflow, Event, Audit | 事务化局部补丁与安全点 / 从检查点建立隔离分支 / 暂停争议范围后人工提交 |
| C11 | 异构团队部署与有界质询 | TaskContract, AgentRole, ModelProfile, RunGraph, Capability | AgentDeployment, RunGraph, DecisionRecord | 完整异构团队 / 有限核心团队 / 人机专家补位 |

### harness · Harness与工具

运行、契约、隔离、恢复

界面行为：管理能力目录、模型配置和技能版本；对任务查看工具参数/结果、检查点、分支回放、运行中输入和失败类别，按冻结配置恢复。

命令：`RegisterToolContract → TestModelProfile → StartRuntimeAdapter → ExecuteTypedTool → FlushCheckpoint → ResumeFromCheckpoint → RouteRuntimeInput`

权威读取：Capability, SkillVersion, AgentRole, TaskContract, Policy, Cost。

权威写入：ToolCall, Run, Event, Audit, Cost, Capability 测试结果。

事件：tool.contract_registered, runtime.step_completed, runtime.checkpoint_durable, runtime.resumed, runtime.input_received。

故障恢复：schema 错误不当成功；模型能力不足预检阻止；append/flush 分清，落盘失败暂停推进；外部副作用由专门账本对账；历史格式不兼容明确拒绝恢复并保留文件。

模块验收：断电恢复从可验证检查点继续；相同工具请求不重复落账；模型切换有配置版本及成本变化；前后端读取同一 runtime 事件序列。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| H01 | 工具输入输出契约 | Capability, Run, Approval, SkillVersion, TaskContract | ToolCall, Capability | 版本化schema与业务校验 / 外部工具契约适配层 / 人工工作单输入输出 |
| H02 | 协议与能力发现 | Capability, Grant, Workflow, SkillVersion | Capability, Event | 显式注册的协议适配器 / 固定HTTP/RPC能力清单 / 文件清单与人工交付协议 |
| H03 | 模型路由与降级 | Capability, Run, Cost, Project, TaskContract, PreferenceProfile, SkillVersion, ModelProfile | Run, ToolCall, Cost, DecisionRecord | 已评测的能力路由 / 合法的备用本地或云模型 / 规则执行或人工判断 |
| H04 | 上下文组装与压缩 | Run, Memory, Workflow, Product, TaskContract, DecisionRecord, PreferenceProfile | Run, Audit | 按任务检索与分层上下文 / 分阶段摘要与检查点 / 人工固定最小证据包 |
| H05 | 执行沙箱与资源边界 | Run, Capability, Grant, Cost, TaskContract, RunGraph | Run, Audit | 隔离容器或VM加凭据代理 / 受限原生命令worker / 授权托管处理或人工离线执行 |
| H06 | 执行日志与耐久检查点 | Run, ToolCall, Event, RunGraph, Grant | Run, Event, Audit, RunGraph | Harness session加业务检查点 / 业务事件日志加最小worker状态 / 只读归档加人工恢复单 |
| H07 | 幂等与外部副作用 | ToolCall, Publication, Event, Approval | ToolCall, Event, Audit | 业务意图ID加远端幂等/查询 / 单写者outbox加主动对账 / 人工核对后有限重做 |
| H08 | 运行底座版本与评测 | Capability, Workflow, Run, Audit, SkillVersion, EvaluationSuite | Capability, Approval, Event, SkillVersion | 固定版本加回归沙箱 / 并行兼容adapter试用 / 冻结版本并人工替代受阻能力 |
| H09 | 技能版本登记与适配 | Source, SkillVersion, Capability, EvaluationSuite, Approval | Source, SkillVersion, Capability, Audit | 审核技能包加隔离契约测试 / 固定白名单能力配方 / 人工技能说明与交付模板 |
| H10 | 运行图分支与沙盒回放 | RunGraph, Run, TaskContract, ChangeSet, Capability, EvaluationSuite, ReplayManifest | RunGraph, Run, Event, Audit | 检查点分支与隔离重执行 / 只读轨迹与差异检查 / 人工重建最小安全复现 |
| H11 | 事件收件与运行中命令 | Event, RunGraph, Run, TaskContract, ChangeSet, Grant | Event, ChangeSet, Audit | 耐久消息适配与可跟踪更新 / 事务命令收件箱加轮询 / 人工操作单与回执导入 |
| H12 | 模型配置、评测与健康路由 | ModelProfile, Capability, EvaluationSuite, Cost, AgentRole | ModelProfile, Capability | 兼容接口 / 本地模型 / 专用多模态接口 |

### data · 数据与记忆

事实库、溯源、图记忆

界面行为：输入素材/成片/帖子/任务 ID 查完整谱系；查看版本、处理节点、使用区间、数据缺口；执行授权的备份、恢复、删除和索引重建。

命令：`CommitDomainTransactionAndOutbox → ResolveLineageGraph → RebuildReadProjection → ExpireOrEraseReferencedData → RestoreConsistentSnapshot`

权威读取：全体权威实体, Event, Artifact/AssetVersion 对象 manifest。

权威写入：Event, Audit, Memory, Index, ProductionTrace, 数据保留/删除记录。

事件：domain.transaction_committed, projection.watermark_advanced, data.erasure_propagated, backup.restore_verified。

故障恢复：投影可重试不重写事实；对象暂缺标 unavailable 并可重试拉取；恢复时校验关系库与对象版本水位；删除传播失败进入待办，不能仅删索引就宣称完成。

模块验收：每个派生对象父版本存在，图无悬空边；从源素材能到帖子再到实验，反向亦可；原始事实和推断记忆明确区分；备份恢复后 manifest 全量校验。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| D01 | 权威事实与业务账本 | Asset, Usage, Publication, Metric, Policy, Event, Post, AgentRole, SkillVersion, TaskContract, RunGraph, DecisionRecord, ChangeSet, EvaluationSuite, PreferenceProfile | Event, Audit | 事务关系库与唯一约束 / 单机嵌入式事务库 / 只读快照加待提交队列 |
| D02 | 事务事件与Outbox | Event, ToolCall, Run, ChangeSet | Event, Audit | 同事务Outbox与消费者去重 / 按版本增量拉取 / 签名或校验清单导入 |
| D03 | 全链身份与谱系 | Event, AssetVersion, Timeline, Publication, Metric, Post, Audio, VoiceMap, Artifact, Run, ToolCall, TaskContract, RunGraph, DecisionRecord, ChangeSet, SkillVersion, EvaluationSuite, PreferenceProfile | Event, Audit | 每次产出写入依赖清单 / 产物清单离线重建关联 / 人工对照回执和编辑历史 |
| D04 | 时间语义记忆 | Event, Insight, Policy, Grant, DecisionRecord, PreferenceProfile, TaskContract | Memory, Run | Graphiti派生上下文图 / 关系库加全文向量检索 / 版本化文档与人工偏好表 |
| D05 | 派生索引一致性 | Event, Index, Memory, Grant | Index, Audit | 增量投影与版本水位 / 新索引全量重建后切换 / 权威元数据直接查询 |
| D06 | 保留期与删除核销 | Source, Grant, Asset, Usage, Memory, Event, PreferenceProfile, DecisionRecord | Event, Audit | 谱系传播加逐项核销 / 权威账本扫描对账 / 隔离受影响范围与人工清理 |
| D07 | 数据备份与一致恢复 | Event, Asset, Index, Audit, RunGraph, TaskContract, SkillVersion | Event, Audit | 一致快照与日志恢复 / 加密离线导出包恢复 / 只读原件与回执重建 |
| D08 | 对象落点、缓存与可复刻清单 | Asset, AssetVersion, TransformRun, RenderManifest, BlobLocation, TimeMap, Run, ToolCall, ReplayManifest | BlobLocation, ReplayManifest | 读取已验字节 / 确定性重渲染 / 记录响应回放 |

### security · 权限与内容边界

身份、凭据、权限、审查

界面行为：分配项目/素材/账号访问，配置凭据引用，确认操作范围，审阅内容权利；在运行中撤权或紧急停止后看到受影响任务。

命令：`EvaluateAccessPolicy → ResolveScopedSecret → ValidateContentScope → IssueScopedApproval → RevokeGrantAndCancelDependents`

权威读取：Account, Grant, Source, Policy, Approval, RunGraph。

权威写入：Grant, Approval, Audit, Event, 受影响任务状态。

事件：approval.issued, grant.revoked, run.cancel_requested, content.scope_failed。

故障恢复：权限变更使未执行副作用重新预检；已生效副作用走回执和补偿；OCR/字幕/文件内指令仅当内容数据，不能改变 Agent 权限。

模块验收：密钥不出现在模型上下文/HTML/日志/导出；撤销账号授权后不会再自动提交新发布；批准范围包含具体版本和账号，改版后不能复用旧批准。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| K01 | 身份与项目访问 | Project, Account, Grant, Approval, AgentRole, TaskContract | Grant, Audit | 集中身份与资源策略 / 项目隔离成员表 / 短期限定任务授权 |
| K02 | 凭据托管与调用代理 | Grant, Account, Capability, ToolCall, TaskContract | Grant, ToolCall, Audit | 密钥库加按需调用代理 / 专用服务账号与隔离worker / 人工平台操作和回执导入 |
| K03 | 不可信素材与指令隔离 | Source, Asset, Tag, Memory, ToolCall, SkillVersion, TaskContract | Audit, Run | 输入分区与工具最小权限 / 先抽取受限结构字段 / 隔离预览与人工录入 |
| K04 | 内容权利与业务守门 | Source, Product, AssetVersion, Account, Approval, Policy, ChangeSet, TaskContract | Approval, Audit | 规则校验加必要人工审核 / 专业审阅限定内容 / 替换素材或移除主张 |
| K05 | 精确范围确认 | Approval, Artifact, Account, Cost, Workflow, ChangeSet, TaskContract, SkillVersion, RunGraph, EvaluationSuite, PreferenceProfile | Approval, Event | 单次操作确认 / 范围明确的批量预授权 / 人工执行后导入凭证 |
| K06 | 撤权取消与紧急停止 | Grant, Run, Publication, Approval, Event, Post, RunGraph, TaskContract, ToolCall | Grant, Run, Event, Audit | 集中撤权与执行闸门 / 隔离账号或worker网络出口 / 人工平台撤销并隔离任务 |

### ops · 运行与观测

追踪、资源、成本、恢复

界面行为：按任务/素材/账号看耗时、失败、队列、费用和质量；冻结故障现场，重试可重试节点、对账未知外部任务，比较版本和资源档位。

命令：`IngestTraceEvent → ReconcileUsageCost → ScheduleResourceLease → DetectQualityRegression → RecoverFailedTask → ValidateAdapterUpgrade`

权威读取：Run, ToolCall, Event, Cost, Capability, EvaluationSuite, Artifact。

权威写入：Audit, Cost 实际结算, 告警, DecisionRecord, Capability 版本。

事件：ops.alert_raised, cost.reconciled, resource.lease_expired, adapter.regression_failed, task.recovery_completed。

故障恢复：告警去重并带真实影响，不每个小错误都弹窗；配额耗尽暂停新任务，已在途费用继续对账；适配升级不兼容回退旧版本并保留失败样本。

模块验收：端到端 trace_id 串起命令、Agent、工具、对象与平台回执；预估与实际费用分开；停止和失败任务仍有成本记录；资源公平性和排队原因可解释。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| O01 | 全链追踪与审计 | Run, ToolCall, Event, Publication, Experiment, Post, TaskContract, RunGraph, DecisionRecord, ChangeSet, SkillVersion | Audit | 持久业务审计加分布式trace / 集中结构化日志与查询 / 离线诊断包与人工对账 |
| O02 | 费用预留与实际结算 | Project, Run, ToolCall, Cost, TaskContract | Cost, Event | 预估预留与结算账本 / 固定单价包与硬次数配额 / 人工批准费用或延期 |
| O03 | 资源配额与公平调度 | Run, Cost, Capability, Account, TaskContract, AgentRole | Run, Cost, Event | 分资源队列与租约 / 限并发批处理窗口 / 迁移获准资源或人工缩批 |
| O04 | 运行健康与质量告警 | Run, Audit, Metric, Index, Cost, RunGraph, TaskContract | Insight, Event | 分层指标与异常告警 / 周期探针与抽样质检 / 人工值守与故障工单 |
| O05 | 方案质量成本评测 | Capability, Run, Cost, Audit, EvaluationSuite, TaskContract | Capability, Insight, DecisionRecord | 代表性样本基准 / 小规模影子对比 / 人工预览与保守容量估算 |
| O06 | 任务故障恢复与对账 | Run, ToolCall, Event, Publication, Cost, Post, RunGraph, TaskContract | Run, Event, Audit, RunGraph, DecisionRecord | 检查点恢复与在途对账 / 换worker重做安全计算 / 人工核对与挂起结案 |
| O07 | API与契约变化管理 | Capability, Grant, PlatformEvent, Audit, SkillVersion | Capability, Workflow, Event, SkillVersion, ChangeSet | 版本固定与兼容探测 / 已验证替代接口适配 / 人工通道或停用受影响功能 |

### workspace · 工作台与扩展

图谱、工作流画布、参数编译、模型配置、追踪与接管

界面行为：在图谱、素材、时间线、画布和效果间联动；将常用节点封装成模块；设置自动执行范围、布局和模型偏好，手机可查看/批准而不挤满画布。

命令：`LoadWorkspaceProjection → SaveWorkflowIR → RegisterSubworkflowCapability → ApplyScopedPreference → ResolveReviewInbox`

权威读取：View, Workflow, RunGraph, PreferenceProfile, ProductionTrace, Capability。

权威写入：View, Workflow 版本, Capability 子流程, PreferenceProfile, Approval。

事件：workspace.view_saved, workflow.version_saved, subworkflow.registered, preference.updated, review.action_recorded。

故障恢复：本地布局保存失败不影响业务任务；插件缺能力显示缺口，不假装已接通；无画布条件可切列表；离线动作仅为草稿，恢复连接后重新验版本。

模块验收：所有入口操作相同版本数据；拖动节点不改执行顺序；每个参数可检查、修改、重置和导出；无需顶部导航也能明确返回/定位，键盘与移动端有等价操作。

| ID | 功能 | 输入类型 | 输出类型 | 可选路线 |
|---|---|---|---|---|
| W01 | 对话与结构化控制 | Project, View, Capability, PreferenceProfile | Project, Workflow, View, TaskContract | 对话生成结构草稿 / 表单与参数面板 / 导入项目配置或工作单 |
| W02 | 可编辑流程与方案切换 | Workflow, Capability, Run, View, RunGraph, TaskContract, ChangeSet | Workflow, View, Run, RunGraph, ChangeSet | 依赖图编辑与变更预览 / 步骤列表与条件面板 / 目标驱动自动重排草稿 |
| W03 | 素材与编辑联动 | AssetVersion, Shot, Plan, Timeline, View, ChangeSet | Timeline, Plan, View, ChangeSet | 关联素材抽屉与时间线 / 分镜卡片式编辑 / 外部编辑导出导回 |
| W04 | 审阅与接管收件箱 | Approval, Run, Artifact, Audit, View, TaskContract, DecisionRecord, ChangeSet | Approval, Run, View, DecisionRecord | 按原因分类的审阅队列 / 作品内逐项审阅 / 受权限控制的审阅工作包 |
| W05 | 账号发布与效果导航 | Account, Publication, Metric, Usage, Artifact, View, Post, ProductionTrace | View, Insight | 同一身份链的关联面板 / 分区列表与统一搜索 / 官方导出对照视图 |
| W06 | 插件与能力扩展 | Capability, Grant, Approval, View, SkillVersion | Capability, View, Audit, SkillVersion | 审核注册的扩展目录 / 管理员配置的固定适配器 / 人工导入导出扩展点 |
| W07 | 无障碍与多端工作区 | View, Project, Run, PreferenceProfile | View | 响应式面板与完整键盘路径 / 低动效列表模式 / 静态导出与受控操作清单 |
| W08 | 偏好记忆与自治级别 | Project, PreferenceProfile, DecisionRecord, Insight, Approval, Policy | PreferenceProfile, View, Event, DecisionRecord | 明确设置与分层继承 / 有来源的偏好假设与小范围验证 / 会话临时选择与无长期记忆 |
| W09 | 可编辑工作流画布与子流程 | Workflow, Capability, AgentDeployment, TransformRecipe, RunGraph, ChangeSet, View | Workflow, ChangeSet, View | 模板组合 / 自由节点编排 / Agent生成草稿 |

## 6. 实体字段与所有权

全部实体还包含 common 字段及不可变版本/租户/来源约束。原始字段声明一字不漏保留在 JSON 中。

| 实体 | 名称 | Owner | 字段数 | fieldSchema | 字段 |
|---|---|---|---:|---|---|
| Project | 项目版本 | project | 10 | 有 | project_id, tenant_id, goals, language, market, policy_version, brand_rules, parameter_defaults, quality_profiles, delivery_variants |
| Product | 商品事实 | project | 8 | 未定义 | product_id, SKU, quantity, price, currency, validity, evidence, evidence_source_id |
| Brief | 创作简报 | trend | 8 | 未定义 | brief_id, audience, intent, claims, constraints, source_refs, project_version, trend_refs |
| Trend | 热点证据 | trend | 6 | 未定义 | trend_id, platform, region, observed_at, expires_at, raw_ref |
| Source | 来源与授权 | acquire | 14 | 未定义 | source_id, canonical_url, owner, grant_scope, expires_at, provenance, origin_kind, rights_evidence_refs, license_snapshot, provenance_manifest, purpose_scope, market_scope, processing_scope, distribution_scope |
| Asset | 原始素材 | ingest | 7 | 未定义 | asset_id, source_id, bytes_hash, object_key, media_probe, provenance_sources, rights_approval_id |
| AssetVersion | 素材衍生版本 | ingest | 27 | 有 | asset_version_id, asset_id, parent_id, transform, hash, status, generation_manifest, prompt_ref, prompt_version, model_provider, model_version, seed, generation_parameters, reference_asset_versions, provider_job_id, license_snapshot, accepted_ranges, blob_location_refs, transform_run_ref, input_contributors, time_map_ref, materialization_kind:baked/non_destructive, quality_report_refs, probe_color_profile, spatial_maps, content_protection, parent_contributions |
| Shot | 镜头片段 | label | 11 | 未定义 | shot_id, asset_version_id, source_in, source_out, frame_rate, keyframes, source_timebase_num, source_timebase_den, source_pts_start, source_pts_end, vfr_pts_map_ref |
| Tag | 标签断言 | label | 30 | 未定义 | tag_id, shot_id, interval, entity, count, action, confidence, method, schema_version, subject, SKU, quantity, person, scene, environment, camera_angle, shot_scale, emotion, capture_quality, language, commercial_claim, editorial_role, evidence_frame_refs, reviewer_lock, subject_kind:shot/audio, subject_ref, audio_version_id, timebase, evidence_audio_range, product_id |
| Index | 检索索引版本 | search | 6 | 未定义 | index_id, embedding_model, dimensions, schema, source_revision, built_at |
| Cluster | 相似素材族 | search | 9 | 未定义 | cluster_id, members, similarity, algorithm, reviewed_at, member_kind, family_root_kind, family_root_ref, cluster_level:exact/near/semantic/provenance |
| AssetScore | 素材候选评分 | assetops | 24 | 未定义 | score_id, shot_id, quality, relevance, fatigue, confidence, policy_version, asset_version_ref, context_dimensions, dimension_scores, observed_score, coverage, sample_n, exposure_n, data_mature_at, uncertainty, weight_version, calibration_version, input_snapshot_refs, eligibility, tier, tier_reason, evaluated_at, expires_at |
| Usage | 使用与曝光账本 | assetops | 35 | 有 | usage_id, family_id, shot_id, artifact_id, timeline_range, source_range, transforms, music_cue, market, exposure, publication_id, batch_id, metric_snapshot_refs, account_id, audience_scope, clip_use_id, post_refs, post_exposures[]:post_record_id/observed_metric_refs/method/estimated_or_measured/confidence, audience_overlap_unknown, reservation_id, reservation_status:reserved/committed/released/expired, lease_owner, lease_expires_at, fencing_token, release_reason, usage_window, actual_use_committed_at, timeline_revision, asset_version_id, lineage_completeness, unresolved_source_refs, timeline_id, music_id, time_map_ref, transform_run_refs |
| Script | 脚本版本 | voice | 7 | 未定义 | script_id, revision, text, language, claims, product_bindings, pronunciation |
| Audio | 音频版本 | voice | 29 | 有 | audio_id, source_id, generated_or_imported, voice_rights, transcript, sample_rate, duration, asset_id, asset_version_id, object_ref, stream_index, source_pts_start, source_timebase_num, source_timebase_den, sample_count, normalized_offset_samples, channel_layout, bytes_hash, parent_audio_version_id, speed_map, resample_map, mapping_source_timebase, mapping_target_timebase, generation_manifest, voice_model_version, pronunciation_version, provider_job_id, script_version, voice_configuration |
| VoiceMap | 口播语义时间图 | voice | 19 | 未定义 | voicemap_id, audio_version, token, normalized_reading, time_range, entity, quantity, role, align_method, confidence, anchor_id, timing_status:untimed/estimated/aligned/verified, audio_hash, audio_sample_range, timebase_num, timebase_den, product_id, validation_status, script_version |
| Music | 音乐与节拍 | music | 25 | 未定义 | music_id, rights, market, use_type, beats, downbeats, sections, confidence, analysis_version, audio_version_id, platform_sound_ref, tempo_bpm, genre, timbre, instrumentation, vocal_presence, analysis_timebase, analysis_confidence, media_status:media_received/audio_ready/platform_reference, asset_id, asset_version_id, source_id, onsets, emotion, analysis_outputs |
| Mix | 混音方案 | music | 14 | 有 | mix_id, voice_id, music_id, gain, ducking, loudness, cue_ranges, version, audio_tracks, audio_bus_graph, gain_automation, processing_chains, measurement_report, audio_clock_maps |
| Plan | 分镜计划 | plan | 10 | 未定义 | plan_id, version, slots, anchors, candidates, constraints, reservations, policy_version, voice_map_version, slot_shot_refs |
| Timeline | 统一时间线 | edit | 32 | 有 | timeline_id, revision, tracks, clip_uses, source_time, output_time, locks, effects, captions, timebase_num, timebase_den, source_pts_map, expected_revision, change_set_id, source_asset_version_refs, audio_version_refs, usage_refs, plan_version, mix_version, sequences, parameter_bindings, keyframe_curves, effect_stacks, mask_tracks, motion_tracks, color_pipeline, text_layouts, orientation_overrides, parameter_resolution_report, brand_constraints, primary_sequence_id, legacy_clock_projection |
| RenderJob | 渲染任务 | render | 7 | 未定义 | render_job_id, timeline_revision, render_profile, seed, worker, checkpoint, status |
| Artifact | 成片与交付物 | render | 19 | 未定义 | artifact_id, render_job_id, checksum, spec, quality_report, variant_family, origin_kind:render/external_delivery/project_export/derivative/snapshot_export, timeline_revision, parent_artifact_id, transform_manifest, external_delivery_ref, mix_version, kind:video/image/subtitle/project/manifest, handoff_manifest, runtime_compatibility, approval_id, snapshot_refs, snapshot_watermark, redaction_manifest |
| Account | 平台账号 | account | 21 | 有 | account_id, platform_subject, region, owner, capability_version, health, timezone, identity_status:unverified/verified/manual_reference, api_connection_status, grant_refs, manual_operator_ref, provider, identity_namespace:developers_user/business_account/shop_seller/shop_creator/shop, shop_bindings, linked_accounts, app_id, identity_scope:app/global/manual_reference, external_identity_key, identity_market, connection_generation, current_grant_refs |
| Grant | 授权引用 | security | 24 | 未定义 | grant_id, account_id, scopes, consent, secret_ref, revoked_at, grant_kind:internal_acl/platform_oauth/service, provider_subject, resource_scope, expires_at, raw_expiry_field, raw_expiry_value, expiry_unit, provider, refresh_generation, raw_expiry_unit, token_family, resource_identity_namespace, app_id, authorization_family, refresh_expires_at, refresh_expiry_raw, connection_generation, authorization_attempt_ref |
| Publication | 发布意图与映射 | publish | 47 | 未定义 | publication_id, account_id, artifact_id, metadata_hash, idempotency_key, publish_id, post_refs, expected_post_count, remote_draft_state, late_receipt_policy, platform_music_id, platform_music_parameters, music_application_status:requested/confirmed/unknown, metadata_snapshot_ref, metadata_schema_version, channel_adapter_version, creator_info_snapshot_ref, requested_local_datetime, iana_timezone, scheduled_at_utc, dst_resolution, schedule_mode:local/platform/manual, schedule_generation, scheduler_lease_ref, provider_schedule_id, submission_request_ref, raw_receipt_refs, local_state, remote_submission_state, reconcile_state, import_origin, correlation_confidence, approval_id, requested_metadata_snapshot_ref, observed_metadata_snapshot_ref, metadata_application_status, post_info_ignored_fields, platform_audio_mix_snapshot_ref, precheck_task_refs, precheck_violation_result, precheck_quality_result, remote_media_upload_session_ref, provider, adapter_id, grant_snapshot_ref, capability_snapshot_ref, platform_music_ref |
| PlatformEvent | 平台状态事件 | publish | 18 | 未定义 | event_id, provider, publication_id, event_type, signature_result, event_time, received_at, raw_ref, post_record_id, provider_event_id, delivery_id, app_id, subscription_id, source_kind:webhook/poll/manual, dedupe_key, dedupe_basis, native_payload_schema_ref, provider_object_refs |
| Metric | 表现快照 | metrics | 40 | 未定义 | metric_snapshot_id, account_id, metric_name, value_or_null, unit, window, observed_at, source, attribution_rule, api_version, currency, timezone, denominator, paid_or_organic, missing_reason, data_mature_after, refund_rule, raw_ref, provider, platform_post_id, post_record_id, scope_kind:post/account/product/shop, scope_subject_ref, product_id, provider_product_id, provider_shop_id, scope_schema_version, publication_id, metric_semantics_version, metric_semantics_effective_at, date_boundary, requested_coverage, actual_coverage, latest_available_date, query_signature, catalog_version, rules_version, value_type, unit_confidence, partial_error_refs |
| Comment | 评论信号 | metrics | 28 | 未定义 | comment_id, parent_id, language, topic, sentiment, permission, retention, redaction, provider, account_id, platform_comment_id, raw_ref, text_or_redacted_excerpt, body_retention_basis, observed_at, platform_updated_at, visibility_state, deleted_at, classification_model, classification_version, classification_evidence_version, post_record_id, body_completeness, body_unavailable_reason, body_version, parent_platform_comment_id, platform_post_id, publication_id |
| Insight | 可验证分析 | learn | 13 | 未定义 | insight_id, hypothesis, population, metric_refs, confounders, uncertainty, scope, evidence, hypothesis_refs, agent_deployment_ref, evidence_grade, analysis_dataset_hash, counter_evidence_refs |
| Experiment | 实验记录 | learn | 9 | 未定义 | experiment_id, baseline, candidate, unit, assignment, windows, guardrails, stop_rule, result |
| Policy | 策略版本 | learn | 11 | 有 | policy_version, parent, typed_config, supported_schema, evaluation, rollout, rollback_ref, release_candidate, promotion_gates, release_state, activation_receipts |
| Workflow | 工作流版本 | agents | 23 | 有 | workflow_id, version, DAG, optional_nodes, guards, budgets, checkpoints, promotion, policy_version, template_parent_ref, node_catalog_snapshot, typed_port_bindings, parameter_overrides, subflow_refs, loop_contracts, compiler_version, compiled_ir_hash, validation_report_ref, parameter_schemas, route_input_requirements, edge_semantics, bounded_loop_contract, candidate_release |
| Run | 执行实例 | harness | 16 | 未定义 | run_id, workflow_version, project_id, context_ref, state, checkpoint, trace_id, attempt_id, parent_run_id, worker_id, lease_id, lease_expires_at, fencing_token, attempt_status, side_effect_reconciled_at, parent_attempt_id |
| ToolCall | 工具调用账本 | harness | 23 | 未定义 | call_id, run_id, input_hash, permission, output_ref, usage, side_effect, outcome, attempt_id, worker_id, fencing_token, provider_request_id, idempotency_scope, negative_outcome_evidence, closed_at, operation_kind, subject_ref, capability_id, model_profile_ref, actual_provider_model, request_config_hash, response_blob_ref, input_evidence_refs |
| Approval | 确认与授权单 | security | 21 | 未定义 | approval_id, principal, scope, artifact_hash, limits, expires_at, decision, kind:review_finding/content_check/rights_check/execution_authorization/manual_receipt, status:proposed/approved/rejected/expired/revoked, review_evidence_refs, subject_kind, subject_id, subject_version, subject_hash, operation, actor_role, policy_version, metadata_hash, metadata_schema_version, account_id, artifact_id |
| Memory | 可追溯图记忆 | data | 8 | 未定义 | memory_id, episode, source_record, valid_time, observed_time, correction, tenant_scope, source_record_event |
| Event | 事务与事件 | data | 18 | 未定义 | event_id, aggregate_id, aggregate_version, correlation_id, causation_id, outbox, consumer_offset, correlation_run_id, command_id, message_kind:query/signal/update/event/manual, command_payload_ref, admission_status:received/accepted/rejected/expired, processing_status:pending/running/waiting/completed/failed/unknown/cancelled, dedup_key, expected_object_version, command_result_refs, error_fields, expires_at |
| Audit | 审计证据 | ops | 9 | 未定义 | audit_id, actor, action, object_version, before_after, reason, trace_id, immutable_at, trace_run_id |
| Cost | 成本与配额 | ops | 10 | 未定义 | cost_id, reservation_id, provider, account, operation, estimated, actual, currency, limits, call_id |
| Capability | 能力快照 | harness | 25 | 未定义 | capability_id, provider, version, schema, scopes, limits, region, tested_at, availability, app_id, endpoint, doc_revision, granted_scopes, observed_limit, sandbox_or_production, fallback_ids, grant_id, documentation_verified_at, application_approval_state, account_authorization_state, read_probe_state, publish_probe_state, production_observed_state, schema_evidence_ref, source_conflict_refs |
| View | 工作台配置 | workspace | 8 | 未定义 | view_id, principal, layout, saved_filters, selections, operation_scope, revision, project_id |
| Post | 实际平台帖子 | publish | 25 | 未定义 | post_record_id, provider, account_id, platform_post_id, publication_id, publish_id, artifact_id, correlation_evidence, association_status, actual_published_at, visibility, moderation, last_observed_at, last_known_remote_state, observation_permission, remote_deletion_evidence, duplicate_intent_flag, observed_music_refs, music_observation_evidence, observed_metadata_snapshot_ref, metadata_application_status_by_field, metadata_observation_evidence_refs, final_media_status:verified_same/verified_transformed/unknown, submission_to_post_time_map_ref, media_mapping_evidence_refs |
| AgentRole | Agent 角色与职责版本 | agents | 19 | 未定义 | agent_role_id, label, objective, role_kind:agent/rule/human/hybrid, capability_refs, skill_version_refs, allowed_actions, denied_actions, resource_scopes, budget_policy, independence_group, context_policy, delegation_limit, approval_policy, status:draft/ready/disabled/retired, accountable_principal, default_model_profile_ref, deployment_refs, output_schema_ref |
| SkillVersion | 技能定义与评测版本 | harness | 19 | 未定义 | skill_version_id, skill_key, version_hash, source_id, definition_ref, input_schema, output_schema, side_effect_class:read/compute/local_write/external_write, capability_refs, required_scopes, supported_modalities, compatibility, resource_estimate, license_snapshot, evaluation_suite_id, status:candidate/validated/active/retired, signature_or_digest, migration_notes, evaluation_result_refs |
| TaskContract | 可验收任务契约 | agents | 24 | 未定义 | task_contract_id, goal, success_criteria, input_bindings, required_outputs, optional_outputs, acceptance_tests, hard_constraints, soft_preferences, budget_caps, deadline, owner_role_id, required_skill_refs, preference_profile_id, baseline_policy_version, authorization_requirements, stop_conditions, delegation_scope, parent_contract_id, status:draft/ready/blocked/satisfied/withdrawn, scope_revision, source_goal_ref, accountable_principal, completion_evidence_refs |
| RunGraph | 可调整运行图版本 | agents | 28 | 未定义 | run_graph_id, graph_revision, goal_contract_id, workflow_version, nodes, edges, active_run_refs, checkpoint_refs, applied_change_ids, expected_graph_revision, branch_kind:live/sandbox/replay, selection_snapshot, resource_plan, parent_graph_id, loop_budget, parked_nodes, immutable_completed_refs, node_contract_refs, role_refs, skill_version_refs, status:draft/validated/running/pausing/paused/awaiting_reconciliation/parked/completed/withdrawn, state_revision, resume_from_state, stop_requested_at, last_known_effect_state, deployment_ref, compiled_workflow_ref, node_attempt_refs |
| DecisionRecord | 有证据的选择与分歧记录 | agents | 26 | 有 | decision_id, question, decision_kind:selection/conflict/routing/evaluation/promotion/recovery, subject_refs, options, proposal_refs, evidence_refs, constraint_results, contradiction_refs, score_breakdown, scoring_method, selected_option, selection_reason, confidence, uncertainty, approval_refs, created_from_run_id, reviewer_role_refs, decider_principal, validity_window, status:proposed/decided/blocked/superseded, source_decision_id, independence_evidence, evidence_delta, economic_stop_reason, promotion_candidate_ref |
| ChangeSet | 字段与区间变更事务 | agents | 30 | 未定义 | change_set_id, target_graph_id, base_graph_revision, base_object_refs, operations, affected_node_ids, affected_artifact_refs, invalidation_plan, retained_refs, lock_conflicts, safe_point_policy, preconditions, decision_id, approval_id, expected_revisions, commit_status:draft/validating/shadow_testing/awaiting_admission/waiting_safe_point/applying/verifying/committed/conflicted/awaiting_reconciliation/parked/rejected/cancelled/compensating/compensated, commit_event_id, compensating_change_id, submitted_by, effect_class, validation_evidence_refs, result_object_refs, plan_version, plan_hash, state_revision, execution_owner_feature_id, owner_run_ref, resume_from_state, last_known_effect_state, reconciliation_refs |
| EvaluationSuite | 冻结评测与故障注入套件 | learn | 22 | 未定义 | evaluation_suite_id, name, version_hash, scope_kind:media/agent/runtime/policy/combined, dataset_refs, case_specs, assertions, dimension_weights, hard_fail_rules, judge_role_refs, ground_truth_refs, privacy_scope, expected_variance, run_budget, required_capabilities, holdout_policy, source_snapshot, baseline_refs, status:draft/frozen/retired, coverage_gaps, task_contract_refs, result_refs |
| PreferenceProfile | 可解释偏好与自治设置 | workspace | 19 | 未定义 | preference_profile_id, owner_scope:person/team/project, owner_ref, profile_version, parent_profile_id, explicit_preferences, inferred_hypotheses, provenance_refs, confidence, conflicts, allowed_learning_scope, autonomy_band:manual/assist/bounded_auto, override_precedence, expiry_policy, revocation_state:active/paused/revoked, active_policy_version, consent_record, negative_preferences, style_examples_refs |
| AlignmentBinding | 语义—音频—镜头绑定 | voice | 25 | 未定义 | alignment_binding_id, script_ref, voice_map_ref, audio_ref, product_refs, claim_ref, semantic, audio_sample_range, audio_timebase, anchor_ids, candidate_evidence, selected_shot_refs, plan_ref, slot_id, timeline_ref, clip_use_ids, output_ranges, clock_transform_ref, validation_status, verification_evidence_refs, decision_ref, task_contract_ref, approval_refs, binding_hash, invalidated_by_change_ref |
| RenderManifest | 实际渲染与区间谱系清单 | render | 32 | 有 | manifest_id, render_job_ref, timeline_ref, mix_ref, binding_refs, dependency_refs, render_profile_snapshot, backend_id, backend_version, build_flags, runtime_versions, command_or_graph_ref, resources, seed, output_timebase, audio_sample_rate, segments, output_map, actual_outputs, status, transform_run_refs, blob_location_refs, time_map_refs, replay_level, replay_tolerances, missing_dependencies, effective_parameter_snapshot, resolution_dependencies, effect_runtime_state, text_shape_snapshot, color_chain_snapshot, audio_routing_snapshot |
| DeliveryPackage | 交付与回导包 | render | 27 | 未定义 | delivery_package_id, purpose, status, timeline_ref, artifact_refs, render_manifest_refs, asset_version_refs, audio_refs, license_source_refs, approved_usage, files, compatibility_snapshot, loss_report, missing_dependencies, recipient_ref, access_scope, expires_at, external_receipt_refs, parent_package_ref, reimport_mapping, unresolved_refs, change_set_ref, product_refs, target_account_ref, intended_publication_ref, commercial_metadata, pending_actions |
| ProductionTrace | 全程生产事实投影 | metrics | 23 | 未定义 | production_trace_id, root_object_ref, query_scope, source_versions, event_watermark, built_at, stages, links, visibility_filter, unresolved_refs, staleness, deletion_state, run_refs, decision_refs, change_refs, binding_refs, manifest_refs, package_refs, post_refs, metric_refs, comment_refs, experiment_refs, policy_refs |
| ImportBatch | 导入批次 | acquire | 11 | 有 | import_batch_id, connector_ref, root_handle_ref, root_label, manifest_hash, entry_count, status, checkpoint, created_by, started_at, finished_at |
| ImportEntry | 逐文件导入记录 | acquire | 22 | 有 | import_entry_id, import_batch_id, relative_path, file_identity, size_bytes, mtime, stable_since, content_hash, transferred_bytes, chunk_manifest, status, error, source_id, asset_id, blob_location_id, intake_mode, root_permission_ref, permission_last_checked_at, local_worker_id, manifest_entry_id, source_revision, read_complete_at |
| TransformRecipe | 素材处理配方版本 | ingest | 16 | 有 | recipe_id, recipe_version, name, ordered_steps, parameter_schema, per_asset_overrides, seed_policy, quality_gates, input_contract, output_contract, recipe_hash, scope_policy, step_contracts, order_invariant, frame_policy, overlay_policy |
| TransformRun | 实际素材处理记录 | ingest | 21 | 有 | transform_run_id, run_id, attempt_id, recipe_ref, input_version_refs, actual_steps, realized_parameters, seed, mask_refs, tool_versions, environment_digest, raw_response_refs, output_version_refs, blob_refs, time_map_refs, checks, status, step_results, frame_decisions, step_exceptions, comparison_evidence |
| BlobLocation | 媒体对象位置 | data | 14 | 有 | blob_location_id, content_hash, storage_provider, bucket_or_volume_ref, object_key, relative_local_path, size_bytes, etag, checksum_algorithm, storage_tier, availability, verified_at, retention_policy, encryption_key_ref |
| TimeMap | 源到输出时间与贡献映射 | data | 15 | 有 | time_map_id, input_refs, output_ref, source_timebase, target_timebase, segments, removed_intervals, inserted_intervals, audio_sample_maps, spatial_map_refs, mapping_quality, algorithm_version, intent_mode, coverage_report, mapping_stage |
| ModelProfile | 模型配置版本 | harness | 23 | 有 | model_profile_id, profile_version, provider, endpoint_ref, model_id, credential_ref, modalities, context_limit, structured_output, sampling_parameters, timeout_ms, retry_policy, region_policy, budget_caps, capability_snapshot_ref, eval_result_refs, health_status, tested_at, config_hash, parameter_support, identity_evidence, role_qualification, route_health |
| AgentDeployment | 异构团队部署版本 | agents | 21 | 有 | deployment_id, deployment_version, task_contract_ref, role_bindings, model_profile_refs, tool_allowlists, independence_groups, evidence_access_policy, handoff_schema, max_rounds, max_delegation_depth, concurrency, token_budget, cost_budget, stop_conditions, deployment_hash, independence_contract, loop_contract, team_mode, handoff_evidence, release_scope |
| Hypothesis | 效果解释与实验假设 | learn | 18 | 有 | hypothesis_id, analysis_run_ref, subject_refs, claim, evidence_grade, metric_snapshot_refs, clip_use_refs, features, supporting_evidence, counter_evidence, confounders, uncertainty, proposed_change, experiment_ref, review_decision_ref, status, valid_scope, expires_at |
| ReplayManifest | 隔离重放依赖清单 | data | 16 | 有 | replay_manifest_id, root_refs, dependency_refs, blob_refs, time_map_refs, recorded_response_refs, config_versions, environment_digest, mode, expected_outputs, tolerances, missing_dependencies, side_effect_policy, replay_run_refs, comparison_refs, manifest_hash |

## 7. 状态机

| ID | 名称 | 初态 | 终态 | 状态数 | 转移数 |
|---|---|---|---|---:|---:|
| task | 可恢复任务 | 待校验 | 完成, 取消, 已跳过 | 14 | 34 |
| asset | 素材生命周期 | 待接收 | 已删除 | 10 | 19 |
| alignment | 语义对齐版本 | 草稿 | 取消 | 8 | 17 |
| edit | 编辑与冲突 | 草稿 | 已提交, 废弃 | 8 | 10 |
| render | 渲染与交付 | 排队 | 可交付, 取消 | 11 | 21 |
| account | 平台授权 | 未连接 | 已停用 | 9 | 22 |
| publish | 发布意图与草稿交接 | 草稿 | 已取消 | 14 | 31 |
| metrics | 数据快照 | 待采集 | 取消 | 8 | 16 |
| experiment | 策略实验 | 提出 | 已回滚, 终止 | 11 | 19 |
| post | 实际帖子与可见性 | 已发现 | 无，允许持续观测 | 9 | 33 |
| change | 局部变更准入与补偿 | draft | rejected, cancelled, compensated | 15 | 40 |
| rungraph | 运行图修订与停驻 | draft | completed, withdrawn | 9 | 23 |

尤其不能将已入库/已发布当执行终态；未知外部结果不能通过本地取消变成“未发生”；所有转移必须有 guard、版本并发控制和证据。

## 8. 全部用户需求

- **R01** 在已有AI剪辑思路上优化，初期TikTok海外带货，未来支持各种类型视频；多品牌/项目/语言/市场/账号。
- **R02** 素材来源：自有上传、已有素材、多个外部平台/链接授权获取、AI生成图片和视频、外部生成服务、本地模型。
- **R03** 原素材与衍生版本分开保留；可裁切、放大、旋转、变速、轻量视觉包装；视觉变换不当成原创性或低重复保证。
- **R04** 多维AI标签：主体/商品/SKU、数量、动作、人物、场景、环境、机位、景别、情绪、拍摄质量、时间区间、语言、文本、声音、商业主张与适用位置。
- **R05** 同一商品细分多人聚餐、单品特写、端盘展示等；标注开头/钩子/证明/转折/结尾等编辑作用；可扩展标签而非固定死词表。
- **R06** 镜头切分、关键帧、OCR、ASR、场景理解、标签置信度、人工修正、模型/标签版本及重新索引。
- **R07** 相似聚类与检索：精确文件重复、近似画面、同场景/同事件/衍生素材族、语义重复；文件不同但观感一样也共享使用压力。
- **R08** 全量入库与索引：原始字节、元数据、关系事实、全文/向量/图索引、版权来源/适用范围、删除/撤权及衍生传播。
- **R09** 素材使用账本：每条素材在哪个成片、哪个版本、哪个时间位置、出现多久、怎么变换、何种BGM、对应账号、发布时间、市场/项目/批次和效果。
- **R10** 动态沉淀：质量、效果、近期频率、同类曝光、账号/受众疲劳、时效和可用权分开管理；高质量可长期保留但仍冷却，低质量降权/复审，稀少新素材保留探索机会。
- **R11** Voice Map是用户定义的口播语义对齐：识别或生成音频，词/句/实体/动作/数量/价格的时间点，映射到对应画面；“两个猪蹄”对齐猪蹄及数量，“可乐”对齐可乐；不只是音乐节拍。
- **R12** 口播脚本撰写、结构/创意变化、产品事实验证、语种本地化、TTS/配音导入、修订重新对齐、词级时码与发音/停顿校正。
- **R13** BGM可识别/检索/生成，分析拍点/强拍/段落/情绪/音色/响度/版权/市场使用范围；对白优先混音与ducking，音乐卡点与语义卡点协调。
- **R14** 自动找热点、关联商品和受众，保存时间和来源，区分地区/语言/平台/真实性，不复制别人表达当原创。
- **R15** 自动素材生成：从脚本/分镜识别缺失镜头，生成任务可比较/重试/替换/验收，保留prompt、模型、seed、参考、来源和版本，合格后入库。
- **R16** 自动剪辑：语义适配、数量/主体准确、镜头衔接、空间与动作连续、节奏、时长、创意多样化、开头/钩子/结尾、字幕/动效/模板组合，多候选方案；不固定模板。
- **R17** 简单编辑器：预览、时间轴、替换/裁切/切分/移动、文本/字幕、音频、画幅、安全区、撤销/重做、版本比较、局部AI修改、锁定人工选择。
- **R18** 导出：规格/画幅/编码/多语言/多版本、渲染并行、进度/失败恢复、技检、交付包；与发布解耦。
- **R19** TikTok对接：账号授权、账号与地区/商品/受众匹配、成片分配、排程、一键发布、明确确认范围、隐私/商业披露/AI标记、状态回执、重复提交防护、失效重授权、人工备用通道。
- **R20** 真实效果回流：平台post_id与成片/账号/素材映射，播放/互动/观看质量/商品行为/订单数据分渠道和实际权限，不承诺每个接口都有全部指标；评论按权限采集分析。
- **R21** 策略优化：保留原基线，产生新版本，只在测试/小流量验证后采用，失败回滚；区分关联和因果，控制账号/时段/受众/产品/预算影响，样本不足不晋升；不让代理直接改线上基线。
- **R22** 多Agent集群、工具调用和完整编排：角色可替换/组合、任务依赖动态规划、执行确定性校验、权限/预算/超时、检查点、回放、共享记忆/冲突、审批/人工接管、观测。
- **R23** Harness工程详尽：模型/工具契约、结构化输入输出、上下文管理、沙箱、工具能力发现、执行记录、局部失败恢复、版本、评测、凭据隔离、请求幂等与副作用控制。
- **R24** NOP术语待查；不得自行声称用户指MCP。功能上涵盖协议接入、工具注册、消息/事件/任务契约，具体命名待确认。FFMAG/FFMAP可能指FFmpeg，但尚未确认；图谱可独立列FFmpeg媒体执行候选。Muse未提供具体URL，应核候选和保留绑定状态。
- **R25** 前端简单灵活：自然语言+结构化控制并存，逐层展开，编辑/素材/账号/任务/效果联动，批量操作与明确作用域，支持桌面/窄屏和键盘。
- **R26** 数据全追踪：project_id、asset_id/asset_version、shot_id、audio_id、voicemap_version、plan/version、timeline_version、render_job、artifact_id、publication/post_id、metric_snapshot、experiment_id、policy_version、agent_run、tool_call及correlation_id。
- **R27** 稳定安全可控：多租户/项目隔离、素材与账号权限、凭据库、沙箱资源限制、外部不可信文本隔离、数据保留和删除、备份恢复、审计、成本、配额、任务取消/暂停。
- **R28** 实际功能状态：设计/外部能力已核/条件接入/术语待确认，不能假装已实现或实测现有系统。
- **R29** 每个功能必须给主方案和至少两条适用的备选路径；各自说明使用条件/切换触发、对质量/成本/速度的影响、保留/废弃何种状态及如何恢复。不得全图机械复用“重试”作为唯一备用方案。
- **R30** 流程可按目的重排、跳过非必需步骤、插入新模块、改参数/模型/工具、局部重跑；只对真实数据/权限/安全前置条件设硬门槛，不做一条固定流水线。
- **R31** 无可用自动通道时可人工接管、使用已授权替代、延期/导出待发布、导入数据；不可用的API/权限不编造可用性，不把绕过平台限制作为备用方案。
- **R32** 图谱交付物必须使用真实力导向积分；支持自动避碰、拖拽牵引、固定/释放、渐进展开、缩放/平移、稀疏及全量关系切换。折叠不删除事实，主动展开的节点可辨；数据读写映射与实体字段引用可分别查看，普通重排保留固定节点。
- **R33** Agent集群按目标、证据和验收驱动；智能化、自动化、自由、安全、稳定、个性化及高质量必须对应具体行为、可改参数与验收。运行中修改采用版本化ChangeSet和安全点，不覆盖历史或跳过外部副作用核对。
- **R34** 作品追踪连接商品主张、实际音频锚点、视觉证据、clip实例、渲染Manifest、交付包、发布意图、多Post、观测和实验；修改价格/BGM/镜头或遇未知发布结果时，可查看精确保留、重算、阻塞与下一步。
- **R35** 按具体素材执行有序预处理：缩放1.011–1.022、可控去字/水印、音频最大可用电平、全量调色、保序删减、插入信息、随机图层与可追溯入库
- **R36** 电脑文件夹直接选择与批次导入；来源/路径/hash/进度/断点/失败和实际对象位置全程可核
- **R37** 素材按质量、标签、使用频率、效果和适用范围分项评价，保留上下文、样本、校准和探索
- **R38** 核心编排与效果分析采用异构多Agent；角色、模型、证据交接、质询、预算与终止条件可调
- **R39** 每角色可替换模型/提供方/参数，配置版本化并独立核能力、健康、费用；凭据只存引用
- **R40** 无限画布可组合节点/参数/子流程并保存模板；编译校验和实际执行状态分开
- **R41** 每条素材/版本/处理/成片/发布/指标有可双向查询谱系、对象落点与有范围的复刻清单
- **R42** 好坏表现由异构分析组提出证据化原因与反证，落实到可检验改版；缺数据不造因果或秒级归因

## 9. 现有集成候选与边界

| 集成 | 原文状态 |
|---|---|
| deepseek-harness | 候选运行时；developer preview，静态已核、本项目未运行 |
| dsh-superagent-prototype | 研究架构参考；不是生产视频系统 |
| graphiti | 条件候选；参考服务不具备本项目所需鉴权，未接入运行 |
| mediacrawler | 研究参考；商业生产须另获书面许可 |
| social_auto_upload | 源码参考；发布 adapter 尚需改造，未实测账号发布 |
| hyperframes_editor | 官方关键类型/源码静态已核；本项目适配未运行 |
| hyperframes_preview | 官方关键类型/源码静态已核；本项目适配未运行 |
| hyperframes_producer | 官方关键类型/源码静态已核；本项目适配未运行 |
| hyperframes_captions | 官方关键类型/源码静态已核；本项目适配未运行 |
| audio_analysis | 音频分析候选；未运行本项目曲库与计算环境 |
| whisperx_alignment | 口播时码候选；当前源码为 rc，模型与本项目环境未运行 |
| ffmpeg_media_execution | 媒体执行候选；实际构建与素材质量待运行验证 |
| semantic_voice_map | 本项目自研语义对齐合同；尚未实现运行 |
| tiktok-business | 条件接入 |
| tiktok-content-posting | 用途受限、条件接入 |
| tiktok-shop | 端点、市场与授权均有条件 |
| tiktok-social-mcp | 工具已记录；实际权限条件接入 |
| muse-binding | 名称待确认 |
| mep-adapter | 规范/格式参考；真实宿主与编辑/预览运行时未接入 |
| creative-knowledge | 逐资料研究参考；全库可用性/商用/训练授权未验证 |

MediaCrawler 的商用许可、social-auto-upload 的副作用恢复、Graphiti 的鉴权边界、WhisperX/BeatThis 的模型和实际环境、MEP 的协议/宿主范围均按各适配器真实状态处理。来源引用是原文历史证据，本任务若使用实际依赖需重新读取官方说明并运行验证。

## 10. 接入真实任务的十个原文验收门槛

### P0-01 · 文件夹与原件

重复、失败、恢复可区分；原件 hash 永不因处理而改变；所有文件有已处理/跳过/失败结果。

### P0-02 · 顺序配方与时码

每个衍生版有完整操作顺序、实际参数、seed、源到目标时间映射；插入内容有自己的来源；原片相对顺序单调。

### P0-03 · 可用素材沉淀

标签能定位证据；质量/权利不合格不进入可用池；相似族与精确重复分离；使用和效果有时间窗。

### P0-04 · 三类视频与非模板

口播、卡点、纯音乐都能完成；非模板分镜/版式可落同一时间线；无口播流程不会卡在 ASR/VoiceMap。

### P0-05 · 成片质量

字幕/画幅/商品事实/音量/同步/空帧等硬门禁逐项有结果；审美评分及人工锁保留；失败版不能发布。

### P0-06 · 异构集群

角色独立模型配置与工具范围生效；证据先独立再质询；裁决记录分歧；预算、步骤与递归上限真实阻止继续。

### P0-07 · 中断与冲突

杀掉 worker 后可从已落盘检查点恢复；旧租约无提交权；CAS 冲突不覆盖；未知平台结果先对账。

### P0-08 · 发布与数据

在实际授权范围成功映射一条真实 Post 或完成明确人工回执链；不可得指标显示缺失；不能用演示值冒充实测。

### P0-09 · 双向全链追踪

原件→衍生→镜头使用→Timeline→Artifact→Publication→Post→Metric→Experiment 可双向定位具体版本/区间；任意缺环有显式诊断。

### P0-10 · 恢复与费用

一次备份恢复后对象和引用校验通过；每条合格成片有模型/生成/渲染/人工成本明细；取消、失败、未知任务的费用也能对账。

## 11. 后续五轮回归的证据要求（工程建议，非原文新增功能）

每轮均重新遍历 23 模块与178功能的实现映射，执行所有当前可运行测试和关键真实媒体路径，保存命令、环境、开始/结束时间、测试报告、失败修复和未覆盖原因。建议各轮额外重点如下，但不能仅检查单一重点后称“全量”。

1. 需求/合同/数据库：目录与接口覆盖、同租户引用、字段验证、迁移、不可变版本及 outbox。
2. 正向闭环：文件夹/素材→配方/时间线→真实渲染→交付包及回查；能接入的模型和发布路径另列证据。
3. 反向故障：坏文件、越权、冲突 revision、过期租约、重复幂等键、预算不足、未知副作用、取消与迟到结果。
4. 跨模块回归：价格/配乐/锁/画幅/撤权/模型变更影响，参数六层继承，来源区间反查，索引与事实一致。
5. 浏览器与最终发布核验：桌面/窄屏、键盘、减少动画、明暗主题、真实后端状态与 UI 一致，清洁启动、备份恢复及最终提交。

不能把“178/178 已登记”称为“178/178 业务已实现”。独立记录 catalog_present、contract_defined、implementation_available、unit_verified、integration_verified、real_media_verified、external_verified 和 production_verified；无证据字段保持 false/unavailable。
