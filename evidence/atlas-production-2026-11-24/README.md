# 2026-11-24 图谱开发与验证记录

源文件为仓库中的 [AI_Video_Agent_Atlas.html](../../docs/AI_Video_Agent_Atlas.html)，SHA-256 为 `65a0e6e7f19b0e087be4ff0adcd1166c7073211caa5988f739df05b47971dee4`。本次从 `6757d074` 开始，功能与验收状态逐项记录在[实现矩阵](../../docs/implementation-matrix.md)和[原文质量门槛核对](../../docs/atlas-increment-2026-11-24.md)。

## 分批冻结代码检查

第一批打通素材、创意、声音、剪辑及复盘。第二批加入 Y04、J05、F03 和 H04，同时核对它们与既有交接、渲染及恢复流程的组合行为。专项回归期间捕获了 WebKit 的一次尺寸通知错误，最终版将制作双栏的布局写入移到下一动画帧。两批共用的测试在各自环境中重跑。每轮采用独立项目、数据库、媒体和测试顺序，程序核对开始与结束时的源码指纹。

| 冻结提交 | 五轮执行结果 | 证据 |
| --- | --- | --- |
| `2f093969` | 每轮 33 组检查、227 项后端与媒体用例 | [第一批逐轮记录](phase-one.json) |
| `ac632881` | 每轮 39 组检查、254 项后端与媒体用例 | [最终逐轮记录](release.json) · [第一轮的 254 个具名用例](unit-cases.json) |

[修复前失败轮与单项复跑](verification-attempts.json)单独保留。[双栏布局诊断](layout-diagnostics.json)记录修复前后回调内的几何变化，以及固定 600 px 容器跨视口测试的实测结果。原始单次浏览器通知缺少堆栈。

浏览器全目录遍历核对 23 个模块、178 项功能和 534 个路线入口。专项操作包括 Chromium 与 Linux WebKit 下的实际文件写入、转场、短片渲染、商品交接、账号占位及进程中断后的恢复。[三个新增流程的双浏览器操作明细](browser-actions.json)、[恢复证据界面](context-pack.png)和[接管后的渲染界面](recovered-render.png)来自最终第一轮。原文的 69 个整体验收场景另有[单独统计](../../docs/scenario-coverage.md)。

逐轮 JSON 保留每组结果、用例数、源码指纹和可核验的文件摘要。仓库附上表所列样本与截图，复跑可生成全套逐轮文件。

在项目根目录安装项目依赖及两个浏览器后可执行下列命令。每轮使用新的输出目录。

```bash
python scripts/run_regression.py --webkit --round 1 --output /tmp/ai-ship-check-1
```

## 实际生成、导出与改错

| 文件 | 数据来源与核验 |
| --- | --- |
| [声画绑定后的成片](voice-cut.mp4) | 含人工锁定的口播和画面，实际解码 51 帧视频；[绑定、时钟和导出哈希](voice-report.json)。 |
| [分镜变体成片](storyboard-variant.mp4) | 从独立候选版本采用后导出，实际解码 50 帧视频；[候选与原始方案](variant-report.json)。 |
| [参考图生成的短视频](reference-motion.webm) · [生成素材剪入后的成片](generated-cut.mp4) | 本机运行 SD1.5 与 AnimateDiff，核对条件图输入、8 帧视频，再将生成的素材剪入、批准并导出含声音的 MP4；[推理与逐帧报告](local-diffusion.json)。 |
| [三个模型与人工复核后的成片](team-cut.mp4) | Qwen2.5 1.5B、Qwen2.5 0.5B 与 SmolLM2 360M 使用三套实际权重。模型错引时程序记录阻断，人工按原片 OCR 改正并交付；[模型身份、输出与人工修改](local-team.json)。 |

[下载文件校验清单](media-verification.json)记录上述样本的 SHA-256、文件大小、流类型和实际解码数量。语言和扩散模型实测是另行执行的专项。浏览器与本地数据库里的平台账号、商品、帖子及读数使用测试数据；平台 OAuth、实际发帖、第三方商业资格与生产容量另待验收。
