# 第一版制作台、剪辑室与原文分区的视觉复核

## 实际素材与成片

制作目标是先看到月面地平线和地球的全景，再切到地球近景。第一次本地 Qwen 2.5 0.5B 的选择恰好相反，模型给出的中文理由也没有指出这一处错误。审阅页的镜头名称、源区间、时间线位置和实际播放器使这个差异可以核对。人工在剪辑室把第二个镜头向前移动、保存，回到原项目后，“当前时间线”显示新顺序，“原始方案”仍保留模型的选择。独立检查读取了浏览器下载的 6 秒成片并完成整片解码。[浏览器实录、模型回执、原始与修订后的界面](../evidence/ai-production/demonstration/README.md)

| 输出取样时间 | 与全景原片的平均绝对 RGB 差 | 与近景原片的平均绝对 RGB 差 | 对应画面 |
| --- | ---: | ---: | --- |
| 第 1 秒 | 0.126 | 45.629 | 地平线与地球全景 |
| 第 4 秒 | 45.672 | 0.073 | 地球近景 |

对两段源文件按各自入点再向后 1 秒抽帧，将输出和参考帧缩至 128 × 72 的 RGB24 后比较，色差取值范围 0–255。定量结果与手工接管后的顺序一致。模型仍可能给出自洽但错误的语义理由，实际观看及版本关联对此很重要。[原始取样数据](../evidence/ai-production/demonstration/final-order-pixels.json)

另核对了文字证据测试的两段原片和两个不同产物。第一段原片黑字白底显示 FRESH LIME，第二段原片黑字黄底显示 SAMPLE。离线画面分析导出与三模型受阻后的人工修复导出，抽帧均先见 FRESH LIME，随后出现 SAMPLE。两段源片都是为测试识别、排序和交接生成的文字卡。[原片与识别数据](../evidence/ai-production/ocr/) · [三模型回执与实际导出](../evidence/ai-production/ensemble/README.md)

## 桌面和窄屏的分区入口

在 1440 × 900 的深色桌面布局和 390 × 844 的浅色窄屏布局中，实际点击八个分区及全部 23 个模块。每个视口都逐一选择 178 个功能，检查实际标题与路线选项数，统计 101 个显示专门业务动作的功能。其余功能也可打开所对应的配置界面，其业务执行深度需按功能逐项核对。两个视口均未检测到未经处理的长哈希或 UUID、页面级横向溢出或 JavaScript 页面异常。[逐功能记录、布局与截图索引](../evidence/ai-production/navigation/navigation.json)

逐张查看 16 个分区抽屉截图。桌面上选中的模块以背景高亮，与新展开的分区标题可分别辨认，分栏边界清楚。窄屏下导航覆盖主操作区，背景随抽屉降低对比度，模块选择后关闭抽屉。依次展开多个分区时，先前分区保留展开状态，靠后的分区需要在抽屉内纵向滚动，滚动后顶端项目和“制作台”可能离开视口。全组截图保留了这项实际使用成本。[分区截图](../evidence/ai-production/navigation/)

首次模型绑定的实机截图中，空图标与留白把已登记模型选择推到了面板下方。已压缩为空状态行，并突出可用模型的绑定操作，手动配置仍可选。参考图的视频任务中也检查到了同一生成关键帧重复占用屏幕。最终界面只显示一个选中的参考预览，原图的定格和版本操作仍然可用。

在真实月面素材演示的窄屏审阅页，监视器排在方案和采用按钮之前，用户可先确认画面。较长任务需要继续向下滚动才能批准。桌面端允许调整监视器与方案的宽度，相邻功能不需要重复在侧栏列出。界面区分镜头选择、时间线采用、技术导出、内容判断和真实发布状态。

## 生成候选与提示词

独立查看了首个本地 SD1.5 静帧，以及 AnimateDiff Lightning 动画解码后的全部八帧。输入要求蓝色陶瓷杯、浅木桌、晨光与上升的蒸汽。256 × 256 的静帧可辨认蓝色背景和木质台面，画面内看不到明确的杯子；同尺寸视频出现鲜艳的交叉网格，杯子、蒸汽和向前推进的镜头运动均无法确认。这一候选未通过内容验收。产物可解码、帧摘要发生变化、来源可追溯属于已实测的技术结果。真实发布仍有单独的内容与权利判断。[实际静帧](../evidence/ai-production/live-generation/generated-still.png) · [实际八帧视频](../evidence/ai-production/live-generation/generated-shot.webm) · [实际工作流回执](../evidence/ai-production/live-generation/report.json)

相同提示词和种子 48171 的独立 512 × 512、18 步 SD1.5 静帧实测耗时 310.8 秒。成图能辨认蓝色陶瓷杯、杯中深色液体和木质背景，杯把的重叠形状仍显得不自然，蒸汽没有清晰呈现。该次静帧验证覆盖杯子与画面构成，视频还需要核对镜头运动。[512 像素静帧](../evidence/ai-production/live-generation/512-still/candidate.png) · [请求、权重和实例回执](../evidence/ai-production/live-generation/512-still/report.json)

## 画面审阅方法

每轮分别在 Chromium 与 WebKit 中运行制作台、可调布局、剪辑室与转场操作。制作台的取证覆盖首次模型绑定、缺少模型时的受阻任务、镜头审阅、明确采用、人工接管与保存、回到同一项目、导出与内容退回。被选素材和成片都实际播放、暂停、改变显示倍率、全屏并退出；手机全屏的横向视频在适应窗口模式保留上下余量，片段进度、音量与退出仍可操作。转场用同一对红蓝素材分别捕获重叠中点，黑场、白场、紫色叠化与两种方向相反的划像均有预期颜色。浏览器中的彩条和文字卡用于定位剪辑、字幕、筛选与时间码；真实影视素材与生成模型的画质另外从原片和成片核对。

每个正式轮次的 PNG 与 JPEG 均按实物解码、复核尺寸与 SHA-256，并在缩略拼图中逐图审看，需要辨认细部时放大原图。对各轮保留的视频各取最多八个均匀时点，将界面、生成原始候选与最终送出的 MP4 分开核对。八帧的生成镜头另按全部源帧逐一核对。全片解码和时间线上精确帧、像素、音轨检查见每轮机器回执。缩略图和有限抽样无法代替真机色彩标定、VoiceOver、长片观看与真实平台接收测试。

| 核对对象 | 如何从证据复查 |
| --- | --- |
| 导演模型绑定与审阅 | [服务端登记模型](../evidence/ai-production/previous-build/round-1/director-chromium/available-models.png) · [桌面选段](../evidence/ai-production/previous-build/round-1/director-webkit/review-desktop.png) · [手机审阅](../evidence/ai-production/previous-build/round-1/director-chromium/review-mobile.png) |
| 监视器、轨道与工作区 | [项目源片全屏](../evidence/ai-production/previous-build/round-1/director-webkit/source-fullscreen-narrow.png) · [调节后的剪辑布局](../evidence/ai-production/previous-build/round-1/layout-chromium/arranged-workspace.png) · [窄屏检查器](../evidence/ai-production/previous-build/round-1/layout-webkit/phone-viewer.png) |
| 版本关联、导出和补镜头 | [生成界面里的实际参考素材](../evidence/ai-production/previous-build/round-1/director-chromium/image-reference.png) · [成片与内容状态](../evidence/ai-production/previous-build/round-1/director-webkit/delivered-desktop.png) · [17 组制作台交互结果](../evidence/ai-production/previous-build/round-1/director-chromium/report.json) |
| 镜头间转场 | [叠化中点](../evidence/ai-production/previous-build/round-1/joins-webkit/dissolve.png) · [向左](../evidence/ai-production/previous-build/round-1/joins-webkit/wipe_left.png) · [向右](../evidence/ai-production/previous-build/round-1/joins-webkit/wipe_right.png) · [检查器修改、撤销和拒绝的回执](../evidence/ai-production/previous-build/round-1/joins-chromium/report.json) |

窄屏监视器的播放、静音、缩放及全屏按钮使用 36 CSS 像素的控件高度。苹果现行 Accessibility 表格列出 iOS/iPadOS 的默认控件尺寸为 44 × 44 pt、较紧凑的最小尺寸为 28 × 28 pt，Buttons 建议一般按钮提供至少 44 × 44 pt 的命中区域。浏览器里的紧凑工具栏已实际操作，真实 iPhone 上的物理点数和单手触达仍缺少设备证据。[Apple Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) · [Apple Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons)

## 修正前的控件尺寸与文字对比度

专项在 Chromium 和 WebKit 启动真实项目，从已上传视频生成本地剪辑提案。每个浏览器都在 1440 × 960 桌面和 390 × 844 模拟触屏视口切换系统浅色、深色，测量制作表单与审阅界面。16 个界面状态共取得 104 条元素记录，包括浏览器水平方向与垂直方向的 CSS 像素尺寸、中心命中测试、截图和颜色。手机真机的物理点数、圆角区域全部边缘命中以及辅助触控仍需另测。[104 条原始记录和 16 张截图索引](../evidence/ai-production/interface-measurements/report.json) · [实测脚本](../evidence/ai-production/interface-measurements/measure_controls.py)

| 可操作元素 | 桌面 CSS 像素 | 390 像素宽模拟触屏 CSS 像素 |
| --- | --- | --- |
| 生成剪辑方案 | 123 × 34 | 123 × 34 |
| 采用方案并建立时间线 | 175 × 34 | 175 × 34 |
| 模型配置、导入视频 | 85 × 30 | 85 × 30 |
| 缩小、放大、全屏 | 25 × 28 | 36 × 36 |
| 比例菜单 | Chromium 72 × 28，WebKit 78 × 28 | Chromium 72 × 36，WebKit 78 × 36 |
| 播放、静音 | 28 × 28 | 36 × 36 |

表内将按钮宽度四舍五入至整数，原始记录保留子像素精度。两种引擎的窄屏主动作和工具按钮高度均低于苹果通常推荐的 44 pt 命中尺寸。两个次动作的高度是 30，距离这个默认目标差 14；主动作差 10，监视器差 8。窄屏审阅标题在两种引擎里均显示为两行。桌面同一目标在 Chromium 需换为两行，在 WebKit 一行可以容纳。[Chromium 窄屏深色](../evidence/ai-production/interface-measurements/chromium-phone-review-dark.png) · [WebKit 桌面浅色](../evidence/ai-production/interface-measurements/webkit-desktop-review-light.png)

| 文本 | 浅色对比度 | 深色对比度 |
| --- | ---: | ---: |
| 已输入的制作目标 | 14.524664∶1 | 12.082330∶1 |
| 审阅中的目标标题 | 桌面 16.124289∶1，手机取样未达均匀要求 | 15.794175∶1 |
| 生成、采用两个主按钮 | 4.696661∶1 | 6.364621∶1 |
| 模型配置、导入视频 | 4.499730∶1 | 6.808051∶1 |
| 监视器时间码 | 13.875649∶1 | 13.875649∶1 |

每个非原生文字元素分别获取实际截图和同位置临时隐藏字形的截图，再在变化的字形像素位置取底色。只有前景完全不透明、至少 99% 取样位置底色相同时报告 WCAG sRGB 相对亮度比。两项 11 CSS 像素的浅色次动作使用前景 `#0071E3`、底色 `#FAFAFA`，精确比值 4.499730∶1 略低于普通文本 4.5∶1 门槛。只看两位小数会掩盖这个边界。窄屏浅色标题的底色一致率分别为 89.24% 和 76.56%，未报比值。原生下拉控件与纯图标只登记几何尺寸，本专项不评价其文字比值，也没有据此认证整个产品的可访问性。[W3C 对比度判定与计算方式](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)

## 第一版冻结代码的五轮完整产物

以下五轮记录固定源代码指纹为 `210f4a777e7748b396588747bf7e00b33988d487e029268e9939ffd3b0b23f67` 的版本。随后的触控和颜色修正有单独的实测记录。每轮使用一个新建测试项目。下列桌面和手机尺寸为主浏览器路径的视口，部分专项用例使用固定视口。目视核对同时查看两种浏览器的制作台、剪辑室和转场。真实模型使用下一轮种子重做素材，每轮的过程文件可在对应目录复查。

| 轮次与主视口 | 成图、运动与创作要求 | 当轮界面与输出检查 |
| --- | --- | --- |
| [第 1 轮](../evidence/ai-production/previous-build/round-1/) · 1440 × 960 / 390 × 844 | 种子 48171。静帧的蓝杯、深色液体与斜向木面可辨，杯把重叠。纯文字动画是鲜艳网格。参考图动画全八帧保留杯子，后两帧杯沿和背景扭曲，末帧偏色明显，蒸汽及有意的向前推进无法确认。 | 71 张 PNG，13 份视频。选段源片、修订后成片及窄屏全屏都有明确的播放区。手机审阅时采用按钮可见，任务较长时需要滚动。0.5B 单导演成片为 SAMPLE → FRESH LIME，复核恢复后的成片为 FRESH LIME → SAMPLE。生成视频的 8 个源帧在 24 帧导出中依序各对应 3 帧。 |
| [第 2 轮](../evidence/ai-production/previous-build/round-2/) · 1536 × 960 / 414 × 896 | 种子 48172。蓝底白纹杯和碟子靠右，杯把被画幅裁去一部分。文字视频里能辨到容器轮廓，颜色在多种高饱和颜色间跳变，与实拍风格相差明显。参考图视频保持蓝杯及杯碟，原图平整的墙面从第一帧起出现斑驳纹理，末段加剧。蒸汽和稳定推进未获确认。 | 71 张 PNG，13 份视频。414 像素素材库变为双列，文字和缩略图均留在列内。Chromium 与 WebKit 调整后素材区同为 299 像素，检查器同为 302 像素。单导演和人工修订两份成片均先出现 FRESH LIME，再出现 SAMPLE。 |
| [第 3 轮](../evidence/ai-production/previous-build/round-3/) · 1280 × 900 / 375 × 812 | 种子 48173。静帧清楚显示蓝杯、完整杯把、杯碟、杯中液体及向上延伸的白色蒸汽，左侧入光，桌面偏深色。纯文字镜头为高饱和交错纹理。参考图视频保留杯子与蒸汽形态，背景和杯面到末两帧出现杂斑，稳定的向前摄影运动仍难以辨认。 | 71 张 PNG，13 份视频。375 像素工作流画布只看得见前一节点的一部分，检查前后连线需要移动画布；上方校验与运行按钮保持可见。单导演实际以 SAMPLE 开场，团队恢复产物以 FRESH LIME 开场。两引擎的左右划像中点仍保持相反方向。 |
| [第 4 轮](../evidence/ai-production/previous-build/round-4/) · 1600 × 1000 / 430 × 932 | 种子 48174。静帧具备浅色木桌、暖侧光和可辨蒸汽。蓝杯在右侧，额外生成的白壶占据左半幅。文字动画为抽象彩色块。有参考图的镜头保留壶、杯与光影，后两帧背景和杯壶轮廓附近的杂纹明显增加，缺少可信的摄影机推进。 | 71 张 PNG，13 份视频。大屏成片页可同时核对当前时间线 v3、原始方案、入出点，以及播放窗口和输出记录。0.5B 单导演先出 SAMPLE；本轮异构小队的原始提案获得审阅资格，未经过局部替换，其 2 秒成片先出 FRESH LIME、后出 SAMPLE。 |
| [第 5 轮](../evidence/ai-production/previous-build/round-5/) · 1366 × 900 / 360 × 800 | 种子 48175。单只钴蓝杯、杯把、向左上方升起的蒸汽、浅木桌和窗边暖光都能辨认，背景细节比前四轮密集。文字动画仍是碎片状色斑。参考图视频保持杯子和蒸汽的轮廓，但最后两帧在窗框、桌面和杯缘出现密集杂纹，缺少可信的推进。 | 71 张 PNG，13 份视频。360 像素视口的编辑器和监视器以纵向滚动切换上下操作区，源片全屏保留原画幅。单导演输出 SAMPLE → FRESH LIME，团队受阻后人工修复的成片为 FRESH LIME → SAMPLE。8 个条件生成帧按顺序分别对应导出文件中的连续 3 帧。 |

五轮共核对 355 张 PNG 和 65 份视频。五个模型样本中，0.5B 单导演有一次正确选取了要求的首镜；其余四次即使在理由中提及 FRESH LIME，输出仍以 SAMPLE 开场。团队成片五次均以 FRESH LIME 开场，其中四次经过显式人工修订。这个差异记录的是模型在特定双素材任务中的表现，不构成通用成功率。各轮的 24 帧导出均能按顺序对应各自的 8 个生成帧，缩至 64 × 64 RGB 后，最相似源帧的平均绝对误差最大为 1.772，取值范围 0–255。黑场、白场和整幅纯紫叠化中点作为预期纯色检查。全量文件名、尺寸、SHA-256、抽帧文字与逐帧误差均可复核。[逐轮视觉数据索引](../evidence/ai-production/previous-build-visual-inspection.json)

具体可见的触控尺寸、颜色偏差和首镜判错已进入后续修订。第二版的点击范围和对比度专项覆盖 375 至 414 像素竖屏、844 像素横屏、1024 像素平板和 1440 像素桌面，跨两个引擎、明暗主题和制作及审阅状态；全部 200 个被测触控目标满足宽、高均不少于 44 CSS 像素，模型配置和导入的浅色文字实测提升至 5.905445∶1。[修正后的截图、测量数据与复现方法](../evidence/ai-production/accessible-interface-measurements/README.md)
