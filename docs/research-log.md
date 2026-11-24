# 一手资料核对与实现取舍

核对日期：2026-11-23（America/Los_Angeles）。这份记录区分“读取公开资料”“采用其原则”“实际安装集成”“实测通过”；列出一个项目不表示已经安装或接通。原 HTML 的 195 条来源完整保留，未声称逐条重新验证了其中每个链接，也未把社交平台仿制效果作为标准。

## 设计与交互

| 一手资料 | 核对内容 | 本项目决定 |
| --- | --- | --- |
| [Apple WWDC26 Design guide](https://developer.apple.com/wwdc26/guides/design/) | 当前公开设计指导入口 | 以 WWDC26 与当前 HIG 为基线，不仅参照最初发布截图 |
| [WWDC26 Platforms State of the Union](https://developer.apple.com/videos/play/wwdc2026/102/) / [Keynote](https://developer.apple.com/videos/play/wwdc2026/101/) | 官方逐字稿中的 Liquid Glass 可读性、扩散和控件层级改进 | 稳定内容层、透明导航/控制层、用户降低透明度选项；未声称逐帧观看或原生光学复刻 |
| [HIG Materials](https://developer.apple.com/design/human-interface-guidelines/materials.md) | 材质的用途与层级 | 玻璃限定在导航与控制，不把视频和事实表全做透明 |
| [HIG Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility.md) / [Motion](https://developer.apple.com/design/human-interface-guidelines/motion.md) | 文字、焦点、替代操作与用户偏好 | 原生表单/dialog、焦点与名称、减少动态/透明度、深浅外观 |
| [WAI-ARIA APG](https://www.w3.org/WAI/ARIA/apg/) | 控件的键盘、dialog、slider 交互约定 | 采用原生语义并保留按钮/数字替代操作；未完成全部辅助技术矩阵验收 |
| [Radix 无障碍指导](https://www.radix-ui.com/primitives/docs/overview/accessibility) / [Lucide](https://lucide.dev/guide/version-1) | 高质量开源基础控件与图标的边界 | 已研究，没有安装；当前保持无构建原生模块，未分发 SF Symbols 或 Apple 字体 |

详细视觉映射与不可等同的原生能力见 `apple-design.md`；官方浏览器包与运行环境见 `browser-runtime.md`。

## 数据、合同与执行

| 一手资料 | 核对内容 | 已采用与未采用 |
| --- | --- | --- |
| [SQLite WAL](https://www.sqlite.org/wal.html)、[事务](https://www.sqlite.org/lang_transaction.html)、[外键](https://www.sqlite.org/foreignkeys.html) | 本地持久性、写事务、外键启用 | 实际使用 WAL、FULL、短事务、CAS 与 SQLite backup；没有将其当作分布式数据库 |
| [JSON Schema 2020-12](https://json-schema.org/draft/2020-12) | 可执行实体结构合同 | 实际生成 64 类 Schema，区分源枚举和工程结构推断 |
| [OpenAPI 官方规范](https://spec.openapis.org/oas/) | 请求/响应/媒体类型的正式描述 | 当前 FastAPI 导出 3.1.0；不因为最新规范另有版本就虚称已采用 |
| [FastAPI 官方文档](https://fastapi.tiangolo.com/) | 请求校验、响应、静态文件、安全依赖 | 实际使用当前锁定版本；部分开放响应尚未全字段类型化 |
| [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution) / [persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | 检查点与副作用恢复边界 | 参考持久执行原则，当前使用自有有界本地执行器，未安装 LangGraph |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | 官方 preview、插件与 session checkpoint 约定 | 不把预览项目视为已审计生产沙箱；当前没有把完整 Harness 集成到执行器 |
| [Graphiti](https://github.com/getzep/graphiti) | 时态知识图谱的事实/时间建模与运行依赖 | 未引入额外图数据库；当前是显式版本引用与有界上下游遍历，不能声称 Graphiti 已上线 |

## 媒体与跨模块语义

| 一手资料 | 核对内容 | 本次实际范围 |
| --- | --- | --- |
| [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html) / [完整手册](https://ffmpeg.org/ffmpeg-all.html) | trim、scale、overlay、音频、字幕与时间基准 | 真实调用 FFmpeg；所有参数受白名单/数值边界约束；技术检测与语义验收分开 |
| [OpenTimelineIO 时间文档](https://opentimelineio.readthedocs.io/en/latest/api/python/opentimelineio.opentime.html) | 有理时基、区间和时间映射 | 使用显式时基/整数 ticks；未声称已做 OTIO 或专业编辑器往返集成 |
| [WhisperX 官方仓库](https://github.com/m-bain/whisperX) | 逐词对齐、语言模型、数字等不可对齐词和重叠声音限制 | 自动 ASR/TTS/逐词对齐仍未接通；当前手工锚点明确标记来源和真实精度 |
| [C2PA Specifications](https://spec.c2pa.org/) | 来源声明与内容真实性不是同一结论 | 当前保留自己的 hash/引用清单，未接 C2PA 签名或认证 |

## 模型与平台

| 一手资料 | 核对内容 | 当前实现边界 |
| --- | --- | --- |
| [DeepSeek Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion) / [JSON output](https://api-docs.deepseek.com/guides/json_mode) | 服务端模型请求、参数、用量和结构输出 | 实际适配兼容 Chat Completions 文本/JSON；已用注入 transport 验证错误/费用语义，未调用真实供应商验收模型质量 |
| [TikTok Direct Post 入门](https://developers.tiktok.com/docs/en/content-posting-api-get-started) | 应用、scope、账号授权与审核 | 自动发布保持未实现，人工账号记录不获得自动发布能力 |
| [TikTok Direct Post 接口](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post) | creator info、用户明确同意、初始化与上传 | 设计保留精确对象审阅和能力门禁；未替用户申请应用或发布内容 |
| [TikTok Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines) | 最新页标注 2026-08-04，用户可编辑元数据、发布处理状态、凭据保密 | 当前人工回执与真实平台事实分开；没有用手工记录冒充 webhook 或 API 回执 |

平台权限、模型价格和可用型号会变化，真实联调前仍须核对当前官方资料和对应账号的实际授权。本文不提供猜测的固定价格，也不把上述 TikTok 资料当作抖音、小红书或其他平台的通用 API 合同。

## 工程工具与验证来源

Python/Node 库使用锁定版本、当前Python依赖闭包constraints.txt与npm lockfile；开发检查实际安装 Black 26.10.1、Prettier 3.10.1、Playwright 1.62.0。GitHub Actions 使用官方 `actions/checkout`、`actions/setup-python`、`actions/upload-artifact`，通过官方仓库 v7 refs 核对具体 commit 后固定 SHA；权限仅 `contents: read`，不自动部署、不发布镜像、不读取模型或平台密钥。

研究记录用于解释选择。真正的实现、浏览器结果、媒体输出、故障注入与未验证项以代码及 `evidence/round-*` 为准。
