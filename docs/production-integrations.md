# 素材、热点、导演迭代与上线证据

一条作品从哪里来、为什么选这个镜头、投放后怎样修正，可以通过项目中固定的版本复核。批次将素材切片、生成槽或人工模板组合成变体。每条记录分别保存源和区间、时间线及可选的工作流运行。渲染产生 Artifact 与 TimeMap。将平台帖子确认到实际成片后，可以按时间区间回查原片。使用作品时长、帖子或版本不一致的证据会影响解释，平台二次加工的成片需要另行核对。

## 获取及连接

| 工作 | 使用方式 | 结果和边界 |
| --- | --- | --- |
| 热点 | 项目热点面板，或 POST /api/v1/production/trends/sync；Google Trends RSS 为公共来源。YouTube Data 热门视频须在服务端设置 AI_SHIP_YOUTUBE_DATA_KEY | 保存来源、地域和观察窗口。榜单间热度的定义不同。同一观察重复拉取不会新增重复事实 |
| 公共视频 | 素材面板检索 Wikimedia Commons，经来源设置启用后导入；可单独暂停 | 校验远端文件修订和字节摘要，记录来源、署名、许可和本地探针。用途批准另有独立状态，不会因命中搜索自动放行 |
| 素材入库 | 素材准入界面核对、隔离、批准并登记保留期 | 实际读取与交付重查来源状态。到期核销保留仍被其他有效资产引用的相同字节 |
| TikTok 只读数据 | 在服务端配置账号绑定，在账号面板验证、暂停，再为已登记帖子同步 | 核对实际用户身份后读取单账号每次最多 20 个视频。保存累计指标、缺失项和采集时间。仅本地协议测试已通过，真实账号需要相应授权 |
| 逐时留存 | 以实际帖子、导出和来源曲线提交留存映射 | 按成片时间映射到原素材区间。保留共同出现的素材及时间，作为待验证的贡献假设。TikTok 基本视频读数接口本身不含逐秒留存 |
| 导演修订 | 固定原方案、热点或表现证据、已发布项目偏好，设置轮数和预算 | 分轮保存输入、失败依据、修订提案和人工选择。当前支持素材评分策略通过固定样本训练、独立留出集评测、审阅上线及回滚 |

服务端读取名为 `AI_SHIP_TIKTOK_ACCOUNTS` 的 JSON 数组。每项包含 `tenant_id`、`project_id`、`account_id`、`open_id` 和 `token_env`，最后一个字段填服务端令牌环境变量的名称，例如 `AI_SHIP_TIKTOK_TOKEN_BRAND_A`。保持访问令牌在服务端。身份核验、视频读取权限及平台限流均会影响同步。使用自有 TikTok 登录产品取得相应读取授权。本仓库未附带凭据。

## 素材如何用于下一条片

在「帖子表现复盘」打开「比较素材位置表现」，指定同一账号、流量类型、发布后观察窗口以及成片起止秒数。例如只看自然流量、发布后首 24 小时、开头 0 至 3 秒。每个帖子使用该窗口最新的已核曲线，计算末点人数除以起点人数，再按帖子等权汇总至素材版本和角色。页面可查帖子数、使用处数、同段先后或重叠出现其他来源的帖子数和原始依据。缺少端点、起点无人、付费与自然混在一起都会影响比较，因此分开记录。把较优候选送回导演前，可以用等长、同账号的替换镜头做对照实验。

## 本地跨语言素材检索

默认可直接使用名称和标签的文字索引。需要语义召回时安装可选依赖，准备同一模型的 ONNX 图（命名为 model.onnx）和 tokenizer.json，将目录设为 AI_SHIP_TEXT_MODEL_DIR，再启动同一服务。项目中的“建立语义索引”或 POST /api/v1/production/asset-index/embed 可按批写入。模型与分词器的两个 SHA-256 共同标识向量空间，更换模型后需在新的空间建立索引。证据文件中有本次使用的两个字节指纹。

```bash
python -m pip install -r requirements-semantic.txt
export AI_SHIP_TEXT_MODEL_DIR=/absolute/path/to/model-directory
python scripts/run.py
```

本次使用多语言 MiniLM 的量化 ONNX 版本，在 CPU 上对素材名称和已审核片段标签推理。中文检索三次，排名首位分别为对应的英文咖啡制作、自行车换胎、雪山徒步描述，三条均无字面匹配；撤权后的素材从结果中消失。该验证集仅有四条资产。视觉和声音的内容识别以及面向生产规模的向量检索质量需要独立数据集。[模型文件](https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2) · [实测结果](../evidence/batch-production/semantic-retrieval.json)

## 开源实现和设计依据

| 一手资料 | 在本项目中的落点 |
| --- | --- |
| [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution) | 在外部生成请求前持久化身份和输入；用原请求恢复等待，保留已完成节点。变更未开始节点时冻结图修订及运行前提 |
| [OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/) | 运行、节点、模型调用、具体工具输入、产物与上游证据分层记录；可核对单次失败、预算和恢复位置 |
| [GEPA](https://github.com/gepa-ai/gepa) | 参考利用执行反馈改进候选的做法。产品支持有界编辑修订和评分参数搜索，训练与留出样本分开，保留旧策略和人工发布环节 |
| [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | 可把人工剪辑导出的 OTIO 作为图节点输入，校验可解析的来源时间范围和支持的效果 |
| [Sentence Transformers](https://github.com/huggingface/sentence-transformers) | 名称和已核验片段标签编码为向量，与字面检索合并；命中后重新核对当前权限 |
| [Apple HIG · Split views](https://developer.apple.com/design/human-interface-guidelines/split-views) 和 [Progress indicators](https://developer.apple.com/design/human-interface-guidelines/progress-indicators) | 调节检查器与制作区域的尺寸，按任务展开信息，窄屏将检查器放入抽屉；同时显示批次进度和独立失败原因 |
| [MediaWiki imageinfo](https://www.mediawiki.org/wiki/API:Imageinfo)、[YouTube videos.list](https://developers.google.com/youtube/v3/docs/videos/list)、[TikTok video query](https://developers.tiktok.com/doc/tiktok-api-v2-video-query/) | 使用官方数据口径、来源身份和字节元数据，保留缺失值并对外部结果做版本核对 |

这些是架构参考。工程以本仓库的 SQLite 状态、FastAPI 请求、素材权限和 FFmpeg 输出作为运行依据。开源项目的测试结果没有计入本仓库的验收。

## 验证入口

[批次说明](batch-production.md) · [各项功能与剩余门槛](source-coverage-review.md) · [实测容量](../evidence/batch-production/capacity.json) · [真实网络观察](../evidence/batch-production/network-observations.json) · [桌面、移动端和浏览器断言](../evidence/batch-production/browser)

复核时分开计算队列完成、生成回执、已生成时间线、实际编码产物、下载解码和真实平台表现。10,000 条时间线与 120 条测试短片是两组独立实测。当前环境没有多主机、长视频万条编码、真实广告账户回传或生产级投放收益证据。
