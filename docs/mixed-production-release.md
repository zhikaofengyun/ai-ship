# 从混合镜头批次到下一轮选材

从项目的「批量制作」编排镜头，进入队列打开任一作品。完成审片后，在「帖子表现复盘」比较选材。需要改变项目素材评分时，到「评分策略晋升」审阅限量试用的数据。

| 操作 | 可核验的结果 |
| --- | --- |
| 一个镜头位既放录像区间，又配置带提示词的生成候选 | 每件作品独立选择镜头来源；批次在提交前按实际组合计算生成调用。生成时可输入文字、拍摄视频的指定帧，或固定版本原片的连续区间。连续参考按生成配置的帧率从原片取样，保存原片显示时码和实际上传的视频。可沿用人工时间线的字幕、音轨和效果，也可接已登记的后续工作流。 |
| 生成器仍在返回结果时查看队列 | 仅需要推理的作品受生成窗口约束。模型返回未知状态时保留原请求供核对；使用原片的其他作品仍可完成。可切换全宽队列，定位作品序号，保留已展开的回执，再返回之前的编排位置。 |
| 模型给出越界镜头、重复错误或与原片不符的引用 | 每轮保存角色、调用、错误位置、输入摘要和输出预算。可修正的完成态答案允许进入剩余轮次；同一个被否决的剪法和理由重复出现时停止。修复后的提案仍需审批，才能变为可渲染时间线。 |
| 在制作台或作品队列打开「对照审片」 | 可调时码，对照实际传给模型的原图和成品的真实显示帧。审阅者选择通过、退回修改或拒绝并记录理由。退回的原件和依赖它的新剪辑受限，已入队但尚未渲染的条目也会重新检查。批次验收可以要求所选参考生成镜头先有通过记录；纯实拍作品可照常完成。 |
| 比较同位置的两个素材 | 固定账号、流量、观察窗口、轨道角色及成片时间区间，先按帖子计算区间留存，再在同一 UTC 发布日内比较，最后各日等权。可展开原始人数、共现帖子和剔除原因。比较假设保存反例和停止条件，再次使用时重算。 |
| 用新权重给小部分素材评分 | 用独立留出组验收候选、审批，再按 1% 至 99% 分流。相同素材身份的不同版本留在同组。每次评分记录实际使用的策略和桶号，重新提交同一请求可取回原结果。面板区分素材数、评分次数和有效观测。进入试用的候选要取得两组观测、结束试用并明确建议推广，才能再执行全量生效。 |

## 实测与适用范围

| 运行 | 结果 | 原始记录 |
| --- | --- | --- |
| 十万作品混合批 | 80,007 件纯剪、19,993 件需要模型，入队前预估与实际落库数量一致。该负载测试关闭编码工作线程。 | [批次与双浏览器回归](../evidence/batch-production/mixed-routing/verification.json) |
| 四帧视频的真实权重推理与内容复核 | 同一镜头位一次真实推理。实拍直剪在 0.906 秒完成，30.225 秒两件均完成。单独纯文字推理用时 16.761 秒。首次参考帧生成严重变形。两种更保守的候选保留了更多桌面布局，但杯形仍漂移，三款均未达到商品写实要求。 | [环境、逐帧对照和可下载的媒体](../evidence/batch-production/mixed-routing/README.md) |
| 连续实拍区间指导视频重生成 | 咖啡原片的第 1.0 至 1.5 秒作为视频条件，按 8 fps 取四帧，原片实际显示时码为 1.00、1.12、1.24、1.36 秒。独立比对 1,769,472 个 RGB 通道值与上传视频完全一致。模型用 20.338 秒生成四帧 VP9。杯盘轮廓仍有形变，已退回。两个浏览器用同一文件播放和逐时码比对；批次界面也实测了同槽直剪与生成。 | [原片、生成结果、像素核对与浏览器回执](../evidence/batch-production/video-segment/README.md) |
| 三个真实语言模型 | Qwen2.5 1.5B、Qwen2.5 0.5B、SmolLM2 360M 分别担任导演、复核、裁决。两轮共六次调用，裁决重复了上一位复核者的理由，未独立证实目标文字。团队在预留的第三轮之前停止。人工在 0.8 秒处辨认 FRESH LIME，局部订正并渲染出 1.6 秒、25 fps 的成片。 | [模型摘要、实测用量、具体裁决](../evidence/mixed-workbench/grounded-team.json) · [人工纠正后的成片](../evidence/mixed-workbench/human-corrected-cut.mp4) |
| 单机实编码与下载 | 3 个工作线程把 1,000 条 160×90、每条 0.6 秒的合成短片编码完成，用时 208.505 秒。逐件下载和全片解码通过，文件摘要均互异。同环境另建 10,000 条互异时间线，用时 105.499 秒。 | [容量原始计数](../evidence/mixed-workbench/capacity.json) |
| 同一段有声实拍的 100 种剪法 | 从咖啡烘焙素材的 10 个区间中各取两段，得到 100 条 432×768、25 fps、3.04 秒的竖片。双工作线程在 117.150 秒完成编码，随后用 40.018 秒下载、探测及解码。成片字节、解码画面和解码原声各有 100 种摘要。原作者 Keria T，许可 CC BY-SA 4.0。 | [实测条件与结果](../evidence/mixed-workbench/real-footage-batch.json) · [逐件摘要](../evidence/mixed-workbench/real-footage-hashes.json) |
| 素材位置比较 | 同位置合成样本中，基准为 80/100，候选为 55/100；两个浏览器均显示候选少 25 个百分点。原媒体实际编码。留存人数为测试夹具。 | [Chromium](../evidence/matched-placements/chromium.json) · [WebKit](../evidence/matched-placements/webkit.json) |
| 评分试用 | 两组共 4 份素材、5 次评分、4 次有效读数。在主动结束并推荐推广后切换活动策略，随后回退。同一写入即使丢失应答也取回原记录。 | [Chromium](../evidence/mixed-workbench/scoring-chromium.json) · [WebKit](../evidence/mixed-workbench/scoring-webkit.json) |
| 来源观察 | 从实际生产同步入口读取 Google Trends 美国榜，以及 Framatube、TILvids 两个 PeerTube 实例，每个各取 5 条。为每条记录来源、观察时间和原始响应摘要。 | [公开来源与观察记录](../evidence/mixed-workbench/public-feeds.json) |

[桌面队列](../evidence/mixed-workbench/queue-desktop.png) · [390 像素队列](../evidence/mixed-workbench/queue-mobile.png) · [原帧和生成候选的审片实拍](../evidence/batch-production/mixed-routing/visual-review/desktop.jpg)

## 工程依据

- LangGraph 的持久化执行和 Temporal 的有界并行模式用于对照检查点、失败恢复和单批在途作业数量。AI Ship 继续使用自己已有的项目账本、租约和队列。[长任务 Harness 与端到端验证](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) · [LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence) · [Temporal](https://github.com/temporalio/documentation/blob/main/docs/design-patterns/sliding-window.mdx)
- 工作台把提示词、可引用观测和角色自己的校验错误分别保存。上下文超出窗口时优先裁去可回查的 OCR 坐标和内部抽样帧，保留制作目标、原片范围、审批和上一轮失败依据。模型仍报告自己的实际 token 用量。[Context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents) · [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
- 将基线、候选、训练样本、留出样本和实际试用观测分开，并给无进展的修订设置停止条件。当前可调参数为素材评分权重，角色能修订镜头提案。[DSPy](https://github.com/stanfordnlp/dspy) · [GEPA](https://github.com/gepa-ai/gepa)
- 按可用宽度决定分栏或单栏，允许直接进入满宽队列，返回时恢复编排位置。键盘可定位作品并操作队列。[Apple Layout](https://developer.apple.com/design/human-interface-guidelines/layout) · [Apple Split views](https://developer.apple.com/design/human-interface-guidelines/split-views) · [Kdenlive 工作区](https://docs.kdenlive.org/en/user_interface/customizing_interface.html)

## 图谱验收

附件与仓库内原文的 SHA-256 均为 `65a0e6e7f19b0e087be4ff0adcd1166c7073211caa5988f739df05b47971dee4`。现有源码对应 23 个模块、178 项功能、534 条候选路线、742 个控件、1,104 个质量门槛和 574 个失败分支。各项剩余条件记录在[逐项核对表](source-coverage-review.md)。176 项有本地可运行业务子集，J01 账号授权和 J07 账号能力有待真实目标平台验证。逐项完整业务验收为 0。[机器清单](../contracts/implementation.json) · [69 个正反场景](scenario-coverage.md)

仍待验证的范围包括生产账号 OAuth 和发帖回执、实际广告或自然流量下的归因、所有候选路线与工具之间的数据交换、多机高分辨率长视频产能、物理设备和辅助技术。容量测试的素材、编码规格及是否提交真实模型请求均在各自记录中说明。
