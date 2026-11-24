# 原文 69 个验收场景的独立测试覆盖目录

初始审计时间：2026-11-24T03:10:54.024680+00:00。来源为 `contracts/atlas.json → v6.scenarioMatrix`。

**69/69 均已逐项映射；没有一个场景在本目录中被宣布端到端全量通过。** 当前为部分覆盖 49 项、未覆盖 20 项。

| 原文方向 | 有部分直接证据 | 未覆盖 | 完整场景通过 |
| --- | ---: | ---: | ---: |
| 正向 N | 23 | 0 | 0 |
| 反向 O | 16 | 7 | 0 |
| 跨模块 X | 10 | 13 | 0 |

“部分”只表示下表明确列出的子约束有测试定义或直接执行证据，仍必须阅读未覆盖列。几个分开测试不能自动证明跨模块场景贯通。“未覆盖”表示没有该业务情境的直接证据；相邻的通用测试、注册了实体或页面上出现功能名不算场景完成。
基础证据使用合成媒体、SQLite 隔离数据库或注入的 HTTP transport。本轮另以真实本地模型、浏览器、项目原件、导出视频和逐帧测量核对特定环节，详见文末六组补充证据。供应商计费、商品主张、真实账号及投放指标仍需要独立验收。测试定义存在不自动等于在最新提交通过。最终逐轮执行结果以总体回归日志为准。

初始复核运行过 18 项媒体测试（18/18，9.086 秒）和真实媒体跨业务测试（2/2，1.303 秒）。两个专项不被重复计作用户要求的五轮全量回归。浏览器映射引用脚本中的具体断言名，完整/quick 轮次须查看实际报告；quick 模式不会执行全目录遍历。

## 分类与定位方法

- 原文正常/反向/跨模块方向分别为 N/O/X，每模块 3 项，共 23×3=69。
- 原文某些 module 值使用中文；下表按每项 featureIds 的真实模块 ID 归组，同时保留原始 case/expected 文本。
- T 编号在文末一一对应到具体测试文件、类/方法或浏览器断言名。测试文件缺失、方法不存在会使生成此目录的自检失败。
- 本表列出的未覆盖项目属于待开发/待验收清单，不会因为重复运行已有测试而自动变成已覆盖。

## 项目与目标（project）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-01-N / 正向 | 同一商品制作横竖多语言作品；预期：目标与事实一致，版式和读法按变体调整 | **部分**；T001、T002 | 商品事实 CSV 和实际尺寸渲染分别有验证。 | 没有同一商品横竖多语言全链；读法、事实一致与版式语义未验。 |
| R1-01-O / 反向 | 价格证据过期且预算不足；预期：暂停依赖该主张的新作品，保留合法非价格内容，不用降质掩盖 | **部分**；T003、T004 | 预算超限阻塞、无法验证的商品有效期为 blocker。 | 未演练价格证据到期只暂停相关内容并保留非价格作品。 |
| R1-01-X / 跨模块 | 发布排队时商品价格变化；预期：定位脚本字幕口播与待发意图，旧帖子保持原事实 | **部分**；T005 | 实际 Product.price 依赖区间只命中引用价格的 Script，保留颜色脚本。 | 没有脚本→口播→字幕→排队发布自动迁移和旧 Post 事实保留的整链。 |

## 热点与创意（trend）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-02-N / 正向 | 合法新热点与商品适配；预期：多角色提出实质不同简报并核适用范围 | **部分**；T006 | 人工趋势来源、观察/到期窗和错误原子性。 | 未接实时合法趋势、商品适配和多角色实质不同简报。 |
| R1-02-O / 反向 | 只有同源转载且热点已过期；预期：不算多个证据，不声称实时事实，可转常青创意 | **部分**；T006 | 过期趋势保持 expired，不伪称新鲜事实。 | 同源转载去独立计数和常青创意降级没有直接测试。 |
| R1-02-X / 跨模块 | 热点撤回时已有剪辑在途；预期：仅检查消费该事实的片段与发布，不全图清空 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有 Trend 撤回→实际消费片段/发布精确失效的业务链测试。 |

## 素材获取（acquire）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-03-N / 正向 | 本地文件夹批量导入并续传；预期：单项状态完整，真实原件和落点可查 | **部分**；T007、T008 | 真实字节分块、重复块、重开继续、全量 hash、单项状态与原件落点。 | 完整多文件文件夹父批次、各项并发与真实磁盘扫描未验。 |
| R1-03-O / 反向 | 同路径文件仍写入且另有同名新内容；预期：延后不稳定文件，字节变化建立新身份，不覆盖原件 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 目前浏览器选择已完成文件；没有 still-writing 稳定性探测，也未演练同路径新字节的文件监视场景。 |
| R1-03-X / 跨模块 | 下载一半撤回来源用途；预期：阻断对应处理，保留已发生下载记录及不受影响文件 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有下载中途来源用途撤回测试；渲染/抽音期间撤权测试不替代下载/多文件批次场景。 |

## AI素材生成（generate）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-04-N / 正向 | 产品镜头缺口局部生成；预期：实际文件逐段验商品外观数量与连续性 | **部分**；补01 | 填写镜头位与提示词后，本地 ComfyUI 使用真实 AnimateDiff-Lightning + SD1.5 权重生成 8 帧动态镜头，应用按真实队列结果查收并验收原件，随后将生成素材用于提案、批准并实际导出。 | 未逐段核验真实商品外观、数量与连续性；技术通过不足以批准语义用途。 |
| R1-04-O / 反向 | 供应商超时但已收费结果未知；预期：先核任务与费用，不重复生成清零占额 | **部分**；T009 | 模型调用适配器的模拟 HTTP 超时保留未知占额、阻止换键重复。 | 付费图像/视频供应商未联调，未核真实供应商任务/账单和已收费结果。 |
| R1-04-X / 跨模块 | 换模型修复一段口播产品镜头；预期：保留其他已验段，重新核该段来源事实时间与接缝 | **部分**；补02 | 单镜头替换会创建新的 Plan，检查实际绑定的来源、时长和区间并保留其他已选镜头与父方案。 | 测试未完成换模型重绘口播商品镜头后的嘴型、事实及相邻转场接缝验证。 |

## 入库与来源（ingest）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-05-N / 正向 | 按指定顺序逐素材处理；预期：参数蒙版随机结果实际落点与有序帧记录可回查 | **部分**；T010、T011 | 显式固定顺序裁切/变速/缩放/调色/响度、真实衍生 hash 和线性时间映射。 | 没有蒙版跟踪、随机结果、有序抽帧和任意配方顺序的全验收。 |
| R1-05-O / 反向 | 水印与产品标签重叠且跟踪失效；预期：不自动误删，保留原件及待审修复方案 | **部分**；T012 | inpaint/去水印等未支持效果明确拒绝，不篡改原件。 | 没有真实水印与商品标签重叠检测、跟踪失效和修复方案候选审阅。 |
| R1-05-X / 跨模块 | 抽帧后口播时钟改变；预期：重建音频与画面映射，复验词锚点字幕混音和实际使用 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 抽帧/速度坡道没有实现；没有变换后的词锚点、字幕、混音联动重建。线性裁切 TimeMap 不等于本场景。 |

## 多模态标注（label）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-06-N / 正向 | 多帧商品与声音打标；预期：证据范围真实，数量口径明确 | **部分**；T013、T014、T015 | 实际帧/声音导航、人工标签、真实媒体范围与版本证据。 | 多帧商品数量/口径与声音语义没有自动识别或独立复核。 |
| R1-06-O / 反向 | 遮挡期间把同一物体重复计数；预期：标不确定并独立复核，不让错误数量进硬匹配 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有遮挡对象身份跟踪、重复计数检测与硬匹配前的不确定性守卫测试。 |
| R1-06-X / 跨模块 | 素材旋转裁切后复用原标签；预期：坐标可变换才复用，受遮挡主体与文字重新验 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有旋转/裁切空间映射后的标签坐标复用、遮挡主体/文字重验。 |

## 索引与聚类（search）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-07-N / 正向 | 按款式用途和镜头偏好召回；预期：硬过滤后重排并保留可播放证据 | **部分**；T014、T015、T016 | 真实标签文本命中、时长筛选、Range 可播放文件。 | 款式硬事实匹配、用途/镜头偏好排序和学习重排未验。 |
| R1-07-O / 反向 | 高相似画面对应错误款式；预期：拒绝候选，给具体缺口，不能用高分补硬事实 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有语义高相似错误 SKU 候选拒绝与具体缺口报告；文本包含 SKU 不能代替视觉事实判定。 |
| R1-07-X / 跨模块 | 旧向量索引仍返回已撤权素材；预期：当前权威用途检查阻断使用，后台修索引不影响即时保护 | **部分**；T017、T018 | 检索当前 Source 撤回过滤；实际使用沿当前深层来源/Grant 复核。 | 没有真实旧向量索引及其后台修复，完整用途范围政策未实现。 |

## 素材沉淀（assetops）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-08-N / 正向 | 素材多次用于不同账号作品；预期：每次使用独立实例，质量适用与效果分开 | **部分**；T002、T019 | 每次渲染的独立 Usage、不同 Plan 的预留/释放历史。 | 没有真实账号多次发布/曝光的独立使用实例全链和质量效果分层归因。 |
| R1-08-O / 反向 | 新素材曝光很少且无成熟指标；预期：效果未知，不能判低质或过早淘汰 | **部分**；T020、T021 | 缺失效果保持 null，已观测分数不把缺失填零。 | 未演练低曝光/成熟窗口与长期淘汰策略；这些策略尚未自动执行。 |
| R1-08-X / 跨模块 | 高权威素材被撤回但冷存副本存在；预期：停止新用，逐落点核销，历史表现保留最小事实 | **部分**；T022、T018 | 实际派生/音轨/成片在当前撤权后停止新读取或制作，历史记录保留。 | 没有多个冷存落点的删除核销、保留期墓碑及真实外部副本。 |

## 脚本与Voice Map（voice）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-09-N / 正向 | 商品数字口播与画面定点对应；预期：事实声音时码视觉分别通过 | **部分**；T023、T015 | 脚本词句人工锚点、实际音频 hash/时长边界。 | 没有商品数字读法验证、真实词对齐与视觉商品定点证据的联合验收。 |
| R1-09-O / 反向 | 句级字幕被请求逐词卡点；预期：先取真实词时码或明确精度不足，不按字符数伪造 | **部分**；T023、T024 | untimed 的时码保持空；真实提音不捏造 ASR/词级时间。 | 没有从句级字幕触发真实 ASR/forced alignment 并拒绝伪精度的完整请求流程。 |
| R1-09-X / 跨模块 | 仅价格显示样式改变；预期：重排文字及相关画面验收，不无故重配声音 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有显示样式变化分类→保留音轨→重验字幕/画面业务测试；通用依赖影响查询不能代替。 |

## BGM与声音（music）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-10-N / 正向 | 纯音乐产品卡点视频；预期：音乐网格与动作主轴成立，无需虚构Voice Map | **部分**；T013 | 机器起音候选来自真实音频。新增手工试听路径可分别保存拍点、强拍和段落，转剪辑并精确定位，见下方补充验收。 | 自动节拍网格、动作主轴与纯音乐卡点成片策略尚未整体验收。 |
| R1-10-O / 反向 | 只有平台声音引用没有合法本地音频；预期：不能离线抽拍或混音，走真实支持附曲或换授权音源 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有平台声音引用专用分支、合法本地音源核验及平台附曲降级实测。 |
| R1-10-X / 跨模块 | 更换BGM使ducking与尾音改变；预期：重验拍点混音接缝及音乐用途，局部缓存按状态范围失效 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有自动 ducking、有状态尾音范围、接缝/许可联动和局部缓存失效测试。 |

## 分镜与选材（plan）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-11-N / 正向 | 自由叙事与模板分别产出可编辑方案；预期：共同满足事实时长和素材证据 | **部分**；补03 | 真实本地画面分析和本地导演模型的有界编排均能输出有实际源区间的可编辑提案，经人批准可形成时间线和实际成片。 | 模板与自由叙事分别在同一商品真实语料上的硬主张、镜头和时长联合验收尚缺。 |
| R1-11-O / 反向 | 硬约束无解且用户锁定关键镜头；预期：列最小冲突，提出补材改稿方案，不自动解除人工锁 | **部分**；T025、T026 | 人工锁和祖先锁保持，不以写入覆盖冲突。 | 没有分镜约束求解、最小冲突集、补材/改稿建议和独立合法部分继续执行。 |
| R1-11-X / 跨模块 | 竖版裁切隐藏商品数量；预期：该规格单独重排或换镜，横版已验结果可保留 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有竖版裁切后的商品数量视觉验收及横竖变体独立重排。 |

## 编辑与审阅（edit）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-12-N / 正向 | 关键帧蒙版跟踪多轨混合编辑；预期：作用域继承、时空映射和最终效果可查 | **部分**；T027、T002、T028 | 真实线性 opacity/x/y、多轨合成、像素变化；六层解析器独立逻辑测试。 | 蒙版/跟踪/非线性曲线未实现，参数继承与实际渲染尚未形成全链。 |
| R1-12-O / 反向 | 同层重叠参数与人工锁冲突；预期：拒绝冲突提交并保留提案，合法独立改动可继续 | **部分**；T025、T026、T029 | 锁保护、提案/冲突、CAS 和不相关字段保留。 | 同层重叠参数、全部时间/空间作用域和独立并发改动没有完整情景验收。 |
| R1-12-X / 跨模块 | 倒放定格坡道叠加后请求反查素材；预期：返回所有实际源贡献或区间，不强造一对一映射 | **部分**；T030、T031 | 人工反向区间映射的数学反查、实际简单渲染的多贡献/半开区间。 | 实际媒体不支持倒放/定格/速度坡道；组合后的实际帧映射仍未覆盖。 |

## 渲染与交付（render）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-13-N / 正向 | 多尺寸最终渲染与工程交付；预期：实际文件逐规格质检，清单记录多源贡献 | **部分**；T002、T031 | 实际指定尺寸和有理 fps 出片、全片解码、ZIP 字节 hash、多源贡献。 | 未自动跑完同一项目的全部平台规格；专有工程回导与内容审阅缺失。 |
| R1-13-O / 反向 | 必需字体效果缺失但编码成功；预期：成片不得通过，换支持后端或明确重新制作 | **部分**；T012、T032、T033 | 不支持效果/关键帧和注入技术 QC 失败均不生成可交付假状态。 | 缺必需字体的实际环境注入、编码成功但语义/字体不符的验收尚未全覆盖。 |
| R1-13-X / 跨模块 | 恢复缓存段跨有状态转场音频尾音；预期：扩上下文或全片重算并验接缝，不把局部成功当全片合格 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 当前整体重渲染，无分段缓存实现；未演练跨有状态转场/尾音的缓存恢复与边界扩大。 |

## 账号与排程（account）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-14-N / 正向 | 授权账号按当地时间排期；预期：真实身份当前能力与唯一发送权可核 | **部分**；T034、T035 | 明确时区排期与人工账号/成片/审批绑定。 | 没有平台真实身份/能力探测、OAuth 和唯一发送权。 |
| R1-14-O / 反向 | 夏令时重叠或令牌刚被撤销；预期：明确选择时间；停止新写，不沿旧权限继续 | **部分**；T034、T036 | DST 重叠/不存在时刻明确拒绝或选择；本地身份撤回阻止新产物提交。 | 未实测平台令牌撤销/刷新竞争；本地 API key 身份不替代 OAuth 账号授权。 |
| R1-14-X / 跨模块 | 账号换市场后复用同一成片；预期：重核商品音乐披露与目标能力，原意图先收敛 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有账号市场变更→商品/音乐/披露/目标能力重核→原意图收敛完整测试。 |

## 发布与回执（publish）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-15-N / 正向 | 合格意图提交并取得真实帖子；预期：上传接纳公开实际挂商品分别有证据 | **部分**；T035 | 人工交付预检、审核和回填 Post；回填明确 independently_verified=false。 | 没有真实上传接纳、公开可见性及挂商品证据；本场景不算真实发布通过。 |
| R1-15-O / 反向 | 上传超时且用户拖到另一时间；预期：保留未知先对账，不新键重发 | **部分**；T037、T009 | 通用未知结果拒绝取消成已取消，模型适配器模拟超时不能换键重发。 | 没有视频平台上传超时与用户改排期的实际意图迁移；这里只覆盖共享安全不变量。 |
| R1-15-X / 跨模块 | 取消本地队列后收到迟到成功回执；预期：归回原意图保留真实帖子，撤下走独立权限和证据 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 未演练取消后的迟到平台成功回执，真实帖子保留和独立撤下权限尚无外部适配。 |

## 数据与评论（metrics）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-16-N / 正向 | 多角色用同口径成熟指标复盘；预期：证据反证混杂和片段假设可追溯 | **部分**；T021、T038、T039 | 来源/口径分组、真实时间比较与描述性实验边界。 | 没有异构多角色复盘、反证/混杂或片段贡献假设评审流程。 |
| R1-16-O / 反向 | 只有总播放量却要求逐镜头因果评分；预期：明确只作帖子级描述或待验证假设，不造曲线与贡献 | **部分**；T039、T020 | 结果保持 causal_claim=false，缺失维度不填零。 | 没有专门“只给总播放→逐镜头请求”的接口级对抗测试；片段因果引擎未实现。 |
| R1-16-X / 跨模块 | 平台再剪辑且退款数据迟到；预期：时间映射未知降粒度，新快照触发关联分析待重验 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有平台再编辑原生事件/时轴映射和迟到退款触发再分析的联动测试。 |

## 分析与实验（learn）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-17-N / 正向 | 候选经过离线与适用小范围验证；预期：证据满足后按范围原子晋升并保留回退 | **部分**；T039 | 冻结单位和窗口、可复现分组、真实保存的描述结果。 | 无真实小流量投放、完整离线评测、原子策略晋升及回退。 |
| R1-17-O / 反向 | 反复看结果直到显著且删负样本；预期：拒绝作为晋升依据，保留无结论与违规评测证据 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有可执行的序贯试验/停止规则违规检测、删负样本检测和禁止晋升场景。 |
| R1-17-X / 跨模块 | 回退时旧模型接口已退役；预期：不能称恢复成功，选择当前已验候选或停驻 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 没有真实模型退役/策略回滚后可用性复验；不可把记录版本恢复当恢复成功。 |

## Agent协作（agents）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-18-N / 正向 | 异构规划视听执行质疑者协作；预期：每角色合同证据模型费用可查且有界收束 | **部分**；T040、补05 | 三套真实本地权重依次导演、复核和裁决。记录各角色用量、模型身份、原始结论、停止状态和可采用的新修订。 | 仍缺完整视听执行职责及语义产品质量、真实供应商计费及共享多机运行。 |
| R1-18-O / 反向 | 多个模型重复同源答案且耗尽预算；预期：不当独立证明，输出未决或接管，不退单Agent自评 | **部分**；T041、T009、补05 | 真实裁决复制上一角色理由，自动标记重复并阻断原案；测试编辑接管后修正局部。独立预算测试还核对未知占额和预算超限拒发。 | 尚未联合演练同源转载、耗尽预算和全角色接管，改写后的语义重复仍需独立核实。 |
| R1-18-X / 跨模块 | 用户在运行中锁定片段并要求改图；预期：安全点重验锁和真实依赖，旧尝试与外部事实保留 | **部分**；T042、T043、T025 | 检查点安全停止、恢复时输入变化阻塞、通用人工锁保护。 | 没有运行中锁片段并原子安全修改 RunGraph/拓扑、旧外部尝试收敛的完整业务执行。 |

## Harness与工具（harness）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-19-N / 正向 | 工具契约模型路由与检查点恢复；预期：结构业务语义都验，当前准入后新尝试接续 | **部分**；T011、T044、T045 | 实际媒体 DAG、暂停恢复、完整真实输出/来源与未知核对守卫。 | 没有所有模型路由/工具，结构正确后商品数量/含义的业务验收仍不足。 |
| R1-19-O / 反向 | 模型返回合法JSON却错商品数量；预期：业务验收拒绝，有限换路仍保留同一硬目标 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 只验证 JSON 语法/类型不够；当前没有合法 JSON 但错误商品数量的专用语义拒绝测试和有界换路。 |
| R1-19-X / 跨模块 | 沙盒回放含真实发布工具；预期：执行边界阻断或模拟生产写，记录响应回放与重调用分开 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 回放当前 execution_enabled=false；没有运行真实沙盒分支并注入发布工具来验证生产写隔离。 |

## 数据与记忆（data）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-20-N / 正向 | 事实提交事件索引与轨迹一致；预期：事务水位、对象位置和实际依赖可核 | **部分**；T046、T047、T031 | 事实/事件事务一致，分页事件恢复，真实对象/hash/谱系清单。 | 没有所有派生全文/向量索引消费者及跨服务水位一致性。 |
| R1-20-O / 反向 | 重复乱序事件加上缺失对象字节；预期：幂等停车并标缺口，不推进完成水位或伪命中缓存 | **部分**；T048、T049、T047 | 坏对象 hash 显示缺口，命令幂等，事件顺序分页不跳过。 | 未联合注入重复乱序事件→消费者停车→禁止推进完成水位的完整场景。 |
| R1-20-X / 跨模块 | 备份恢复含已撤权原件与旧租约；预期：先应用当前墓碑重新准入，旧凭据写权不得复活 | **部分**；T050、T051、T052 | 实际备份/恢复字节校验、坏包不提交、旧租约拒绝产物。 | 未把已撤权原件/当前外部 tombstone/旧凭据一起恢复后演练重新准入。 |

## 权限与内容边界（security）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-21-N / 正向 | 范围内自动操作无需重复确认；预期：当前真实授权作用于具体对象与动作 | **部分**；T053、T054、T055 | 服务端租户/角色与会话身份、只读拒写、身份撤回。 | 完整对象/动作/项目资源授权范围与持续授权不重复确认尚未系统验收。 |
| R1-21-O / 反向 | 素材字幕含伪系统指令并诱导扩权；预期：作为内容隔离，模型与工具边界均不扩大 | **未覆盖**；无直接测试 | 没有该业务情境的直接自动化验收证据。 | 无字幕/素材伪系统指令到模型与工具的端到端攻击测试；不执行 shell 和提示标记只是基础保护。 |
| R1-21-X / 跨模块 | 已给持续授权后作品改事实和账号；预期：只重核受变更影响的范围，未知已发送请求独立对账 | **部分**；T005、T035、T037 | 精确字段影响、当前权限预检、未知外部结果独立核对不抹除。 | 持续授权下换事实/账号只重验受影响范围的完整授权迁移未覆盖。 |

## 运行与观测（ops）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-22-N / 正向 | 真实任务基准与费用资源调度；预期：声明实测环境样本和尾部风险 | **部分**；T002、T056、T057 | 实际本地媒体工具/产物、配置估价不冒充账单、超时产物不提交。 | 没有生产负载基准、真实供应商账单、吞吐/尾延迟/资源公平调度实测。 |
| R1-22-O / 反向 | 采集故障导致看不到指标；预期：报告数据缺口，不以零值自动判内容失败 | **部分**；T058、T021 | 缺失指标和真实零值区分，不把 null 当效果零。 | 没有实际采集器故障注入和内容策略自动判定链；自动淘汰策略尚未运行。 |
| R1-22-X / 跨模块 | 模型API变更在途旧worker迟到提交；预期：旧租约拒写，已有费用和外部回执核对后再迁移 | **部分**；T052、T036、T009 | 迟到旧租约/撤销身份拒写，未知调用费用占额保留。 | 真实模型 API 变更/在途请求/回执与费用迁移没有集成测试。 |

## 工作台与扩展（workspace）

| 场景 / 方向 | 原文输入与预期 | 覆盖与证据 | 实际证明的部分 | 未覆盖或不可宣称的部分 |
|---|---|---|---|---|
| R1-23-N / 正向 | 用户从简洁图谱进入参数工作流；预期：可编辑业务控件，原始技术合同折叠 | **部分**；T059、T060、T061 | 浏览器脚本定义了全目录/路线遍历、配置重载、真实本地工作流；存在真实 UI/API 路径。 | 最终执行结果看各轮 browser-report.json；目录遍历不等于所有业务功能，尚无全图谱所有交互的证明。 |
| R1-23-O / 反向 | 离线或窄屏不能运行复杂画布；预期：提供等价列表保留选区，草稿不冒充执行 | **部分**；T062、T063、T064 | 浏览器窄屏导航/溢出检查；配置任务留 blocked，不假装业务完成。 | 没有完整离线缓存/等价业务列表、离线恢复选区和全部复杂画布操作。 |
| R1-23-X / 跨模块 | 拖动图谱后切执行画布和历史视图；预期：布局不改实际依赖，恢复视图不覆盖服务端事实 | **部分**；T065、T066 | 布局/导航边不能充作可执行依赖，绑定只能来自上游。 | 前端 layout-only 保存修复仍需最终浏览器证据；历史视图恢复不覆盖事实全场景未验。 |

## 测试证据索引

下表索引是可复核测试定义，不是虚构运行记录。FFmpeg 项检查真实合成媒体字节；普通业务测试除特别说明外使用隔离 SQLite 的手工合成记录；ProviderLedgerTests 注入模拟 HTTP，不访问模型服务。

| 编号 | 文件 / 精确测试定义 | 证据类型与边界 |
|---|---|---|
| T001 | `tests/test_production.py` / `ProductionIntegrationTests.test_product_csv_money_exact_and_bad_rows_isolated` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T002 | `tests/test_media.py` / `MediaIntegrationTests.test_actual_multitrack_render_manifest_package_and_qc_boundary` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T003 | `tests/test_core.py` / `CoreStoreTests.test_budget_reservation_and_actual_cost_not_released` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T004 | `tests/test_production.py` / `ProductionIntegrationTests.test_unstructured_validity_is_a_reported_blocker_not_a_server_error` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T005 | `tests/test_core.py` / `CoreStoreTests.test_dependency_invalidation_uses_fields_and_half_open_ranges` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T006 | `tests/test_production.py` / `ProductionIntegrationTests.test_trend_source_window_and_atomicity` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T007 | `tests/test_media.py` / `MediaIntegrationTests.test_chunk_upload_resume_duplicate_chunk_and_full_hash` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T008 | `tests/test_media.py` / `MediaIntegrationTests.test_import_probe_original_dedup_and_relative_path` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T009 | `tests/test_production.py` / `ProviderLedgerTests.test_timeout_preserves_reservation_and_cannot_change_key` | 明确模拟供应商 HTTP；真实本地账本/错误状态，不是外部实联。 |
| T010 | `tests/test_media.py` / `MediaIntegrationTests.test_transform_real_bytes_trim_scale_color_loudness` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T011 | `tests/test_execution.py` / `ExecutionTests.test_real_transform_then_extract_audio_and_idempotent_replay` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T012 | `tests/test_media.py` / `MediaIntegrationTests.test_transform_rejects_unknown_effect_and_bad_parameters` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T013 | `tests/test_media.py` / `MediaIntegrationTests.test_real_scene_candidates_frame_and_onsets_are_not_semantic_claims` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T014 | `tests/test_production.py` / `ProductionIntegrationTests.test_tag_range_and_versioned_manual_evidence` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T015 | `tests/test_production.py` / `RealMediaCrossDomainTests.test_real_probe_bounds_tags_search_and_audio_anchors` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T016 | `tests/test_media.py` / `MediaIntegrationTests.test_http_range_bytes_and_filename_security` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T017 | `tests/test_production.py` / `ProductionIntegrationTests.test_search_excludes_current_revoked_source` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T018 | `tests/test_media.py` / `MediaIntegrationTests.test_artifact_traces_current_grant_through_frozen_deep_inputs` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T019 | `tests/test_production.py` / `ProductionIntegrationTests.test_usage_reservation_conflict_release_and_history` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T020 | `tests/test_production.py` / `ProductionIntegrationTests.test_score_missing_dimensions_not_zero` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T021 | `tests/test_production.py` / `ProductionIntegrationTests.test_metric_zero_null_duplicates_and_scope_groups` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T022 | `tests/test_media.py` / `MediaIntegrationTests.test_derivative_chain_keeps_original_identity_and_revocation_blocks_all` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T023 | `tests/test_production.py` / `ProductionIntegrationTests.test_voice_alignment_untimed_range_and_script_versions` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T024 | `tests/test_media.py` / `MediaIntegrationTests.test_real_waveform_and_audio_extract` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T025 | `tests/test_core.py` / `CoreStoreTests.test_manual_field_lock_blocks_changes_and_removal` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T026 | `tests/test_core.py` / `GraphAndParameterTests.test_ancestor_lock_yields_proposal_and_keeps_captured_value` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T027 | `tests/test_media.py` / `MediaIntegrationTests.test_keyframe_render_changes_actual_frame_pixels` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T028 | `tests/test_core.py` / `GraphAndParameterTests.test_six_layers_orientation_and_explicit_null` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T029 | `tests/test_core.py` / `CoreStoreTests.test_patch_preserves_unrelated_fields_and_rejects_stale_revision` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T030 | `tests/test_production.py` / `ProductionIntegrationTests.test_half_open_multi_contributor_reverse_time_map` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T031 | `tests/test_production.py` / `RealMediaCrossDomainTests.test_actual_render_timemap_and_replay_resolve_immutable_bytes` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T032 | `tests/test_media.py` / `MediaIntegrationTests.test_render_rejects_unlocked_stale_version_and_unsupported_keyframes` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T033 | `tests/test_media.py` / `MediaIntegrationTests.test_injected_qc_failure_does_not_create_deliverable` | 真实 FFmpeg 合成媒体、实际字节/文件/技术边界。 |
| T034 | `tests/test_production.py` / `ProductionIntegrationTests.test_schedule_dst_gap_ambiguity_and_utc` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T035 | `tests/test_production.py` / `ProductionIntegrationTests.test_publication_reviews_current_rights_and_manual_receipt` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T036 | `tests/test_execution.py` / `ExecutionTests.test_revoked_worker_credential_cannot_commit_new_output` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T037 | `tests/test_core.py` / `CoreStoreTests.test_unknown_side_effect_is_not_cancelled_or_retried` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T038 | `tests/test_production.py` / `ProductionIntegrationTests.test_metric_timezones_choose_actual_latest_and_canonical_windows` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T039 | `tests/test_production.py` / `ProductionIntegrationTests.test_experiment_unit_data_and_honest_effect_boundary` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T040 | `tests/test_production.py` / `ProviderLedgerTests.test_three_role_team_persists_checkpoints_and_independent_proposals` | 明确模拟供应商 HTTP；真实本地账本/错误状态，不是外部实联。 |
| T041 | `tests/test_production.py` / `ProviderLedgerTests.test_budget_and_homogeneous_team_fail_before_network` | 明确模拟供应商 HTTP；真实本地账本/错误状态，不是外部实联。 |
| T042 | `tests/test_execution.py` / `ExecutionTests.test_cancel_finishes_current_node_and_preserves_checkpoint` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T043 | `tests/test_execution.py` / `ExecutionTests.test_resume_rejects_changed_input_version_and_keeps_completed_nodes` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T044 | `tests/test_execution.py` / `ExecutionTests.test_paused_run_resumes_without_replaying_completed_node` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T045 | `tests/test_execution.py` / `ExecutionTests.test_empty_array_reconciliation_and_forged_provenance_are_rejected` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T046 | `tests/test_core.py` / `CoreStoreTests.test_batch_failure_rolls_back_all_records_and_events` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T047 | `tests/test_core.py` / `CoreStoreTests.test_event_recovery_reads_ascending_page_without_skipping` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T048 | `tests/test_production.py` / `ProductionIntegrationTests.test_replay_checks_bytes_and_current_rights_and_bidirectional_lineage` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T049 | `tests/test_core.py` / `CoreStoreTests.test_command_idempotent_replay_without_duplicate_writes` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T050 | `tests/test_maintenance.py` / `MaintenanceTests.test_backup_restore_checks_database_and_real_object_bytes` | 临时目录内真实备份/还原/字节损坏；不是生产灾备演练。 |
| T051 | `tests/test_maintenance.py` / `MaintenanceTests.test_corrupt_and_path_traversal_archives_do_not_commit` | 临时目录内真实备份/还原/字节损坏；不是生产灾备演练。 |
| T052 | `tests/test_execution.py` / `ExecutionTests.test_expired_fence_rejects_output_and_orphan_attempt_requires_reconciliation` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T053 | `tests/test_core.py` / `CoreApiTests.test_team_identity_isolation_and_read_only_role` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T054 | `tests/test_core.py` / `CoreApiTests.test_http_only_session_supports_media_identity_and_revocation` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T055 | `tests/test_execution.py` / `ExecutionTests.test_team_viewer_cannot_start_or_forge_owner_role` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T056 | `tests/test_production.py` / `ProviderLedgerTests.test_model_success_idempotency_and_config_price_not_bill` | 明确模拟供应商 HTTP；真实本地账本/错误状态，不是外部实联。 |
| T057 | `tests/test_execution.py` / `ExecutionTests.test_single_node_cannot_commit_after_deadline` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |
| T058 | `tests/test_core.py` / `CoreStoreTests.test_missing_metric_differs_from_zero` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T059 | `scripts/browser_regression.py` / `all_23_modules_178_features_534_route_choices` | 实际 Chromium+本机 API 的脚本断言；结果须以执行轮次的 browser-report.json 为准。 |
| T060 | `scripts/browser_regression.py` / `feature_configuration_persists_across_reload` | 实际 Chromium+本机 API 的脚本断言；结果须以执行轮次的 browser-report.json 为准。 |
| T061 | `scripts/browser_regression.py` / `workflow_ui_runs_real_transform_then_extract_audio` | 实际 Chromium+本机 API 的脚本断言；结果须以执行轮次的 browser-report.json 为准。 |
| T062 | `scripts/browser_regression.py` / `mobile_navigation_opens_and_closes` | 实际 Chromium+本机 API 的脚本断言；结果须以执行轮次的 browser-report.json 为准。 |
| T063 | `scripts/browser_regression.py` / `mobile_no_page_overflow_` | 实际 Chromium+本机 API 的脚本断言；结果须以执行轮次的 browser-report.json 为准。 |
| T064 | `tests/test_core.py` / `CoreStoreTests.test_configure_feature_never_fakes_domain_completion` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T065 | `tests/test_core.py` / `GraphAndParameterTests.test_navigation_edges_and_unbounded_loops_not_execution` | 隔离数据库/逻辑/HTTP 验证；领域记录是明确合成测试夹具。 |
| T066 | `tests/test_execution.py` / `ExecutionTests.test_node_binding_cannot_read_nonancestor_layout_node` | 真实本地工作流/SQLite；部分使用实际 FFmpeg，故障通过显式注入。 |

## 目录完整性

场景总数：69；唯一场景 ID：69；三个方向各 23；部分 49；未覆盖 20；完整通过 0。初始索引引用 66 项测试/浏览器断言，本轮新增证据另列。

没有省略任何原文场景。没有把媒体的线性映射测试算成倒放/定格/坡道已经实现，没有把手工回填算成真实平台发布，也没有把模型 JSON 合法算成商品语义正确。

### 本轮新增的局部验收

| 原文场景 | 新增验证 | 仍需证据 |
| --- | --- | --- |
| R1-02-N | 浏览器创建两个真实创意候选、记录备选理由并批准具体版本。见 `scripts/browser_selection.py` 和 `tests/test_selection.py`。 | 真实授权热点与多角色创意生成、适用范围审查。 |
| R1-10-N | 播放真实音频、人工标拍并分别保存强拍/段落时钟，独立音轨映射回时间线。见 `scripts/browser_selection.py` 和 `tests/test_rhythm.py`。 | 自动网格与动作主轴的整片验收。 |
| R1-11-O | 人工锁定已有真实片段时拒绝覆盖，确认解锁后可登记缺口并保留历史版本。见 `tests/test_selection.py`。 | 自动最小冲突求解及补材方案。 |
| R1-09-O | 导入 SRT/WebVTT 后逐句保留毫秒起止和 `timing_precision=cue`，相互导出再导入仍保留正文与时间。见 `tests/test_captions.py`。 | 请求词级卡点时，真实 ASR 与强制对齐的完整补证流程。 |
| R1-12-N | 手工选中的源区间跨分镜与编辑保持一致，独立音轨的标记映射到编辑时钟并可点选。见 `scripts/browser_selection.py`。 | 完整关键帧/蒙版跟踪、多轨混合效果和六层继承。 |
| R1-13-N | 工程交付包含 OTIO 标准交换，含剪切、间隔、本地素材绑定及不可表达效果的拒绝。见 `tests/test_timeline_exchange.py`。 | 实际目标 NLE 的多规格往返和多源成片质检。 |

逐行比对原 HTML 的 69 个用例及预期，与本页同 ID 的场景描述一致。此次逐项复阅了正向、反向和跨模块各 23 条，尤其检查了自动音乐分析、平台声音引用、自由叙事与模板生成、局部 AI 生成、源权限撤回、编辑蒙版和跨缓存接缝。现有人工打点、手选来源或格式往返均不足以关闭这些完整条件。以上为既有部分覆盖场景新增的局部证据。此段为前版验收；本次 AI 制作更新见下文，未把专门化接口单测记作整个场景通过。

### 初始审读输入快照

| 输入 | SHA-256 |
|---|---|
| contracts/atlas.json | `37740087c289d8b2e90f2bc4b5176565cbd688b50f9f9674f625cbefa612c695` |
| tests/test_core.py | `3fd35ea1ee68733c5a9a10d550d9e0e36cd2aaea6ea8860665d5bb4341f8d5bb` |
| tests/test_media.py | `7ebd914fcc6276c8eb29547c49003a2050778eaa2217c469eb585a247efb0dcd` |
| tests/test_production.py | `8ec4faa170ec1391dd5a2789c0eb3a756a670c8039e78034d1460033d70c4838` |
| tests/test_execution.py | `9d89ca6c31300a87a289c64fd2733e640db2af376aeaa1c5b23022161925218b` |
| tests/test_maintenance.py | `69bddc97957d73049f00f1be35a935b97c9d389fa48bcb9e75308dd712fe7067` |
| scripts/browser_regression.py | `1a5505eeddcd43215f2a77337de9fdea5b9d1a6c9f5f6c2d6002d0d74c311e54` |

## AI 制作与工程交换的复核

核查 69 个原文验收场景，三个方向各 23 个。三条由“未覆盖”更新为“部分”，目前 49 个部分、20 个无直接证据。上一版计数为 46、23。本次有三条独立可运行的制作链：已选择的本地原片经画面分析得到提案、审批、渲染；本地语言模型根据实际来源选择候选后审批并出片；人工接管指定草稿，经转场、工程交换到实际导出。每条链的适用范围小于原文完整场景。真实付费供应商、平台发布和语义完整验收仍需各自证据。

| 补充证据 | 直接验证 | 保留边界 |
| --- | --- | --- |
| 补01 · R1-04-N/O | `tests/test_generation.py` 验证生成队列、丢 ACK 查原任务、可解码文件及项目内再选用。静帧经过实际解码与帧数检查，可由用户指定时长、帧率转为有来源记录的视频衍生版。 | scripts/verify_generation_worker.py 使用真实 SD1.5 生成单张 PNG 并另制 24 帧定格，以真实 AnimateDiff-Lightning 生成八帧动态视频后入库、批准与再编码。两条链有各自的文件和回执。付费供应商对账与商品语义仍须另验。 |
| 补02 · R1-04-X、R1-11-O | `AutoEditTests.test_single_shot_fork_retains_unchanged_intervals_and_replays` 验原片版本、单个替换和其他段保留。 | 人工指定的替换未覆盖换模型口播产品镜头的语义接缝。 |
| 补03 · R1-11-N | `tests/test_auto_edits.py` 和 `scripts/verify_local_director.py` 覆盖实际媒体提案、来源/时长约束、真实 GGUF 推理及成片解码。 | 单个本地模型在有限菜单中作出选择；演示中模型实际选反全景和近景，人工纠正后用两个输出帧核验镜头顺序。目标叙事质量及所有模板仍未完成验收。 |
| 补04 · R1-12-N、R1-13-N | `tests/test_clip_transitions.py` 校验五种效果的实际解码像素、音量及双源贡献，`tests/test_timeline_exchange.py` 将标准或自定义 OTIO 转场双向交换并拒绝无余量输入。 | 未验证目标商业 NLE 的渲染、全片蒙版和专业调色。 |
| 补05 · R1-18-N/O、R1-19-N | [实名模型、调用回执与成片](../evidence/ai-production/ensemble/README.md)记录三个独立鉴权进程的真实决策、互相复制、错误引证被拦截和测试编辑另建候选后的成片。 | 涵盖两个模型系列，三套权重。所有角色共享原片，未独立证明商品数量、跨模态语义或跨供应商账单。 |
| 补06 · R1-23-N/O/X | `scripts/browser_workspace_layout.py` 验制作工作台与监视器联动、两条未保存时间线、拖动/键盘调整、窄屏及窗口内展开，读取服务端修订确认视图变更未改事实。 | 不含离线等价清单、设备间同步、真机读屏及所有图视图的历史恢复。 |

其余场景保持前面逐项表列出的真实覆盖与缺口；每条的来源条件与未覆盖边界均按原 HTML 保留。
