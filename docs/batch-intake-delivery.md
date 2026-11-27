# 一张产品图、多个版本，一直到交付

进入项目后，默认打开「批量制作」。单条制作和时间线仍可用来审片、制作样片和修订一条异常作品。每个批次与每条作品序号都可以重新打开。

| 操作者要做的事 | 从哪里开始 | 如何核对结果 |
| --- | --- | --- |
| 汇集图片、视频和手机拍摄素材 | 批量制作的「导入素材」「本地文件夹」「采集目录」。可将网盘同步软件的下载目录登记为采集目录 | 看每个文件的入库状态、重复识别、原件和版本。手机 MOV 保留原件与旋转信息，渲染时编码交付 MP4。服务器无法读取的设备需要上传 |
| 让导演找到素材中的某段画面 | 后台按素材版本采样，记录源时码、画幅、颜色和可用 OCR。部署固定版本 CLIP 后可按图像语义检索和生成候选标签 | 点击结果预览命中时码。人工注明标签和区间后，导演再次选片时可以使用该区间。有争议或没有达到分类阈值的画面留给人工复核 |
| 让一张图产生不同候选 | 配置已登记的生成服务，添加生成镜头并选择参考图，分别输入提示词变体；需要混剪时将实拍区间加入同一配方 | 先核算调用数、设置并发和次数上限，再提交。逐项查看实际提示、种子、模型回执、原素材或降级凭据以及输出版本。需达到商业画质时要另行检查图像、文字、动作和一致性 |
| 复用一个人手剪出的样片 | 在单条制作中编辑、保存，然后在批量制作选「从手剪时间线开始」 | 指定可替换镜头，预览每项时间线。受锁定的人工字段必须保持，无法保留的组合会被拒绝。样片的版本和每条衍生作品可追溯 |
| 让团队给出可审阅决定 | 单条制作的模型导演或模型团队工作区；后台通过实际注册的提供方和角色执行 | 看各角色独立回执。模型引用无效或照抄前序理由会阻止采纳。中断后核对原请求，恢复时重新检查角色、预算和权限 |
| 从已出片批次创建交付 | 在批次队列选择「准备批量发布」，选账号、方式和作品序号范围 | 预检绑定具体成片版本、摘要、账号和审批。未完成序号单列，重复操作复用原意图。更多作品可翻页勾选。在「查看交付」核对每条待办或前往审批 |
| 把成片送到 TikTok 创作者收件箱 | 配置下述授权，完成内容与权利复核，由负责人明确勾选并上传 | 收件箱送达后，创作者在 TikTok 确认文案和可见性，最后发表。系统只会在平台报告公开帖子编号后建立该帖关联。网络中断时核对原交付编号，避免重复上传 |

### 可选的 TikTok 配置

为部署注入专用服务端环境变量。账号需已登记在本项目，平台授权需包含 `user.info.basic` 和 `video.upload`。连接断开、授权主体不一致、成片版本变化、审阅被拒或交付尚未到期时会拦住上传。签名上传地址和访问令牌不得写入用户可见记录。

```json
[
  {
    "tenant_id": "<租户>",
    "project_id": "<项目 ID>",
    "account_id": "<项目 Account 记录 ID>",
    "open_id": "<TikTok 用户 open_id>",
    "token_env": "AI_SHIP_TIKTOK_UPLOAD_TOKEN_CREATOR"
  }
]
```

将上述数组作为 `AI_SHIP_TIKTOK_UPLOAD_ACCOUNTS`，将有效令牌通过部署密钥设置 `AI_SHIP_TIKTOK_UPLOAD_TOKEN_CREATOR`。默认绑定数组为空。回归向隔离的协议服务发送实际编码后的 MP4 字节，检查分块、身份、重复发送、状态和帖子关联。真实账号与正式平台限额需单独联调。公开直发、跨平台自动排程与撤回尚待验收。[TikTok 收件箱和权限](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video) · [媒体分块](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) · [平台状态定义](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status)

### 剪辑能力、界面和编排依据

| 对照项 | 已有或本轮可复核的部分 | 仍需单独开发或验收的部分 |
| --- | --- | --- |
| 剪映的素材、剪辑与合成 | 区间裁剪、分割重排、声音分离、字幕、转场，位置和透明度关键帧，矩形与椭圆遮罩。椭圆遮罩在实际导出画面上检查中心与边角像素 | 曲线变速、跟踪或手绘蒙版、多机位同步、完整 HSL 和色轮工作流，以及与剪映工程实机往返 |
| Apple 界面规范 | 参考公开的导航、工具栏和分栏示例。批量制作为项目主入口，单条制作独立可达，交付操作在批次上下文中完成 | Web 浏览器操作验证与 Apple 平台原生视觉验收需要分开记录。尚无逐像素、VoiceOver 和真机 Safari 验收 |
| DeepSeek Harness、Codex、Claude Code | 对照持久化会话、子角色启动和团队分工的公开实现或文档。在本系统以角色检查点、回执复用和独立判断检查承载这些要求 | 全功能等价性、动态跨机调度、复杂真实广告素材上的长期导演质量 |
| 图谱与运营迭代 | [原始图谱](AI_Video_Agent_Atlas.html)、[178 项操作对照](feature-workflows.md)、[69 个场景](scenario-coverage.md)保持分项核对。素材家族、人工修订、保留率与源区间、审核后的选片策略可分别追溯 | 真实账号数据驱动的长期效果、标签召回率和分类精度、跨品类聚类质量、覆盖全部功能的端到端业务验收 |

公开资料：[剪映专业版](https://www.jianying.com/) · [Apple 人机界面规范](https://developer.apple.com/design/human-interface-guidelines/) · [Apple SwiftUI 公开设计示例](https://developer.apple.com/videos/play/wwdc2025/323/) · [Apple WWDC26 平台设计](https://developer.apple.com/videos/play/wwdc2026/102/) · [DeepSeek Harness 架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md) · [Codex 子角色实现](https://github.com/openai/codex/blob/main/codex-rs/core/src/agent/control/spawn.rs) · [Claude Code 团队](https://code.claude.com/docs/en/agent-teams)

实际运行输出：[自动入库和实帧检索](../evidence/material-intake/result.json) · [三模型两轮、人工纠正与出片](../evidence/team-recovery/two-round-edit.json) · [同图多提示与画质检查](../evidence/batch-production/directed-still/README.md)。

## 本轮改动对应哪些原始功能

下表使用原始 HTML 的功能编号。可按编号进入[机器操作对照](../contracts/feature-workflows.json)，查看原文输入、可选路线、质量条件和剩余工作；部分实测还以 SHA-256 固定在[运行证据索引](../contracts/feature-evidence.json)。

| 操作与图谱编号 | 本轮能复核的结果 | 完整验收仍缺什么 |
| --- | --- | --- |
| 目录与媒体入库 · A07 本地文件夹批次与断点导入、I01 隔离与媒体探测、I04 技术归一与代理 | 下载目录中未写完的空文件会让位给稳定文件。手机 MOV 测例保存旋转信息，重启后复用原件分析并复核授权。[入库回归](../tests/test_material_observations.py) | 云盘服务端直连、真实多设备目录权限、HEVC/HDR/VFR 等手机素材的机型和色彩矩阵 |
| 素材标签到导演选片 · L01 镜头切分与采样、L03 场景动作与连续性、L06 标签体系与纠错、S02 语义与混合检索 | 两张图和一个视频完成后台分析。宇航员图虽没有候选标签，仍能按画面召回；花卉视频保留多个时码的关键词、分数和帧摘要。人工区间能进入下一次选片。[实际模型与素材](../evidence/material-intake/result.json) · [覆盖与回退回归](../tests/test_material_observations.py) | 生产词表上的召回率、数量和遮挡判断、跨镜动作连续性，及完整 VFR 坐标迁移 |
| 按画面建议素材族 · S04 语义聚类与素材族 | 同模型、同素材版本的采样帧向量可以计算双向匹配覆盖，并用完全链接聚类给出候选。实测从四份素材中建议咖啡原图与其运镜片成族（0.94954），宇航员、花卉各自未入组。人可核对帧时码后建族或退役，模型空间或原证据改变会阻止旧结论复用。[真实 CLIP 报告](../evidence/material-intake/semantic-families/result.json) · [聚类实现](../app/semantic_families.py) · [边界与复核回归](../tests/test_asset_families.py) | 大库检索与分群调优、真实营销品类上的误合并和漏分率、跨人与跨批次的长期复核 |
| 同图多方向与实拍混剪 · G02 图片生成与参考、G03 视频生成与重绘、N04 变体与重复控制 | 同一实拍参考图、两条不同提示和种子进入同一批次，两次本地推理各产出四个不同解码帧，加一个实拍选项一共交付三条 MP4。两个模型候选均呈噪点，未通过商品和动作目检。[输入、成片和逐帧检查](../evidence/batch-production/directed-still/README.md) | 用生产权重验证商品外观、文字和运镜；固定种子单独比较提示效果；跨类目重复率、失败率及成本 |
| 手剪样片复用 · E05 画面音效与版式、E06 AI 局部修改与人工锁 | 原照片沿两个锁定关键帧模板导出，各有 48 个不同解码帧。人工设定的位移、时间点及来源可追溯。椭圆遮罩另以中心、边缘及角落的实际像素核对。[样片与位移回执](../evidence/batch-production/controlled-photo/report.json) · [遮罩回归](../tests/test_media.py) | 非线性速度曲线、跟踪与复杂路径遮罩，多层继承和多人并发下的综合验收 |
| 模型团队与恢复 · C07 共享上下文与独立复核、C09 证据化分歧裁决、C11 异构团队部署与有界质询、H06 执行日志与耐久检查点、H07 幂等与外部副作用 | 通用团队故障后复用原回执，拦住照抄裁决。选剪团队用三种权重作两轮决定，重复错误后停止，人工按 0.8 秒处原片文字换镜并渲染。[角色恢复](../evidence/team-recovery/cpu-role-receipts.json) · [选剪与人工接管](../evidence/team-recovery/two-round-edit.json) | 跨供应商真实账单、改写式复述、动态跨机编队、商业视频上的长期导演质量 |
| 批量交付 · F01 发布通道选择、F02 发布前校验与披露、F04 一键确认与任务提交、F05 异步上传与结果核对、F06 审核可见性与最终映射、F09 版本漂移与在途发布迁移 | 批量分页准备交付，按作品版本和账号审阅。隔离的 TikTok 协议服务接收真实编码字节并返回状态；丢失初始化回执会阻止重复提交。改标题、排程或多次换账号后，只在当前账号列出后继意图。[协议与预检回归](../tests/test_batch_publications.py) · [账号迁移与权限回归](../tests/test_batch_publication_migration.py) | 正式 TikTok 账号从收件箱到公开可见的独立回执、生产限额、多平台直发和在途撤回 |
