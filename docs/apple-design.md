# Apple 官方设计基线与 AI Ship 实现规范

本文说明采用了哪些官方设计原则、如何对应到当前代码，以及哪些原生能力不能由浏览器等同实现。研究于本次 2026 年 11 月开发会话核对；依据 Apple Developer 正文与官方逐字稿，不以社交平台仿制截图作为设计标准。

## 1. 基线与设计目标

[WWDC26 Platforms State of the Union](https://developer.apple.com/videos/play/wwdc2026/102/?id=206) 的设计段介绍了 27 releases 的 Liquid Glass 更新，包括复杂背景的扩散改进、边缘与高光的清晰度、透明至着色的可调外观，以及 Mac/iPad 侧栏、窗口圆角和浮动工具栏的优化。本次阅读了官方逐字稿核对该段信息，没有将文字核对描述成逐帧观看。

AI Ship 是媒体制作工作台。主内容应让人准确判断原始媒体、时间线、来源和实际结果；材质与动效负责表达导航层级和交互反馈。设计采用 Apple 最新公开指导的内容/控制分层、简洁对齐、适度圆角、清晰响应与用户偏好支持。各个 CSS 数值属于本项目的工程实现，不是 Apple 未公开的精确渲染常数。

## 2. 官方原则与代码映射

| 原则 | 一手依据 | 当前实现 |
| --- | --- | --- |
| 材质用于导航与控制 | [HIG Materials](https://developer.apple.com/design/human-interface-guidelines/materials) | `styles.css` 的 sidebar、mobile-bar、浮动控制及通知使用透明材质；视频、素材图、事实表和主要内容卡片使用稳定内容背景。 |
| 文字承载与清晰层级 | [HIG Layout](https://developer.apple.com/design/human-interface-guidelines/layout)、Materials | 侧栏采用较高遮盖度与细边缘；标题、操作、参数分层。复杂配置渐进披露，不把整个工作台铺成透明玻璃。 |
| 动效表达实际操作 | [HIG Motion](https://developer.apple.com/design/human-interface-guidelines/motion) | 按钮按下、导航状态、抽屉、页面进入使用短时过渡；滑块、播放头、拖动直接响应，不以动画结束代表服务端完成。 |
| 可辨认的交互目标 | [HIG Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons) | 原生 button/input/select；触控设备的主要按钮和图标操作目标至少 44 CSS px。44 CSS px 是本项目的 Web 换算选择，不声称等同所有设备上的 44 pt。 |
| 支持用户偏好 | [HIG Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) | 跟随系统明暗外观；应用内减少动态和减少透明度；兼容媒体查询；高对比度样式；键盘焦点与可访问名称。 |
| 使用平台能力并保持边界清晰 | [Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass) | 使用系统字体栈、浏览器原生 dialog 和表单。没有分发 Apple 系统字体，没有把自绘图标标为 SF Symbols。 |

### 外观 token

`web/styles.css` 是视觉 token 的唯一来源。浅色内容背景为 `#f5f5f7`，内容表面为白色，主文字 `#1d1d1f`；深色对应 `#161617`、`#202022`、`#f5f5f7`。强调色使用蓝色，成功、需要补齐、错误使用独立语义色，并同时显示文字和图标。

侧栏桌面宽度为 226 px，窄桌面为 205 px。桌面 shell 明确声明 sidebar/content 网格区域；移动遮罩默认不参与布局，仅在小屏菜单打开时固定覆盖。主要内容圆角为 20 px，按钮为胶囊形，编辑器保持紧凑的矩形工具结构。玻璃材质使用约 28 px 背景模糊、1.5 倍饱和度、细边缘与柔和阴影；内容本身没有被模糊。

系统字体栈包括 `-apple-system`、`BlinkMacSystemFont`、`Segoe UI`、`PingFang SC` 等已安装字体。Linux 截图会使用环境实际可用的字体，因此不能凭这些截图推定 Apple 设备上的系统字形像素相同。

## 3. 界面结构与真实状态

| 界面 | 主要操作与信息 | 代码 |
| --- | --- | --- |
| 工作台 | 空项目起点、创建项目、真实项目目标和制作路径入口；没有种子统计 | `app.js` |
| 素材库 | 文件/文件夹/拖放导入、分块续传、筛选、实际媒体预览、原件与衍生版本、处理与提音 | `app.js`、`media.js`、`upload.js` |
| 剪辑室 | 素材面板、真实媒体播放器、分轨时间线、剪切/分割/裁切、属性滑块、构图、字幕、有限关键帧、撤销与重做 | `editor.js`、`media.js` |
| 工作流 | 节点与端口、数据连接、方案选择、原文控制、服务端公开的执行参数与输入版本绑定 | `workflow.js`、`execution-controls.js` |
| Agent 团队 | 模型、职责、部署、预算、独立分组、真实调用与实际回执；保存配置不会声称已接通模型 | `app.js` |
| 交付与发布 | 实际文件、技术结果、内容/权益分别审阅、发布前检查、人工回执与来源 | `app.js` |
| 数据与实验 | 有来源的指标、缺失与成熟度、真实描述统计、实验假设和停止规则 | `app.js` |
| 全部模块 | 23 个模块、178 个功能的原文目标、三条路线、检查和失败边界；按源控件类型配置 | `app.js`、`controls.js` |
| 来源与审计 | 真实记录、不可变历史、引用链与导出；执行事实只读 | `app.js` |
| 设置 | 明暗外观、减少动态/透明度、团队会话连接、功能边界 | `app.js` |

网络等待、空内容、可编辑草稿、权限错误、缺失能力、运行中和完成是不同状态。页面加载失败显示错误及重试路径；每个业务操作通过 API 返回的事实更新界面。文件上传进度按服务端确认的字节计算；没有实际百分比的任务不制造进度数字。真实事件流只提示记录有更新，不擅自覆盖未保存的编辑。

## 4. 控件的保真与自由度

原文 `operation.controls` 决定参数键、标签、选项、单位、范围、锁定说明与时间域。前端保留 `data-control` / `data-control-type`，便于逐功能回归核对。没有提供的默认值保持未指定，不把未填写的金额、权重或指标默认为零。

- 枚举使用选择器；集合和排序使用可增删的标签与上移/下移按钮。
- 表格提供原文声明或用户明确创建的列、可编辑行、增删；权重必须有唯一名称和明确非负数值，空值与重复名称报错。
- 比例提供滑块与精确数值；金额有独立币种；时长包含明确单位；数值读取原文 `range.min/max`。
- 引用固定记录 ID、类型与版本。已有引用不会因为重新打开表单而自动升级到最新版本；版本变化会显示并要求显式选择。
- 媒体时间区间保留整数 ticks、时基分子/分母与时钟域。音乐单点具有独立音轨时基。
- 商品有效期、热点日历、指标窗口、事实有效时间和授权/审批期限使用对应业务时间域、明确时区和端点边界。它们不套用媒体半开区间。业务经过时长不允许选择帧。
- 复杂遮罩、对象、分组和表达式通过单独的高级编辑弹层处理。原始 JSON 默认折叠，保留自由表达能力，同时避免把所有参数都变成代码文本框。

配置持久化在原文已有 `View.operation_scope.feature_configs`，没有为了界面方便创造新的功能配置实体。工作流的原文控制放在 `parameters`；当前支持执行器的真实参数放在 `operation_args`，版本输入放在 `input_bindings`。这三者有独立用途，界面明确展示，不把设计配置假称为已执行。

## 5. 剪辑、锁版和工作流的交互语义

### 时间线

编辑器使用与实际 FFmpeg 渲染相同的 `Timeline.data`：`clips`、`captions`、`audio_tracks`、`format`。不另外维护一套只供展示的时间线模型。拖动调整位置；吸附和波纹联动可独立开关；人工锁定片段会阻止非法联动。S 分割、空格播放、Ctrl/Command Z 撤销，并且都有界面按钮。

素材库与剪辑室共享 `media.js` 的消费模型：原始 `Asset`、非 original 的 `AssetVersion` 与独立 `Audio` 都可选择，原始版本不重复展示。衍生画面固定 `asset_id` 与所选 `asset_version_id`，预览、裁切边界和入出点均使用该版本的实际输出秒；保存保留确切来源引用。独立 Audio 进入 `audio_tracks`，按自身的实际时长预览和渲染。缺少实际文件或原始版本时明确阻止加入，不生成占位素材。

关键帧当前支持片段局部秒的 x/y 位置和不透明度，线性插值、首尾保持；分割会重算各段局部时间。复杂遮罩或未支持的效果不能默默丢弃。浏览器预览与 FFmpeg 的实际编码结果分别验收，播放器音量上限等差异必须在能力说明中解释，不能把预览当最终渲染证明。

保存先产生草稿。锁版通过用户确认精确版本后调用 `LockTimelineRevision`，由服务端返回锁定版本。渲染使用该返回记录的 ID 与 revision。对已有工程的普通修改会生成未锁定的新版本；没有将本地 `locked=true` 冒充服务端准入。真实产物的技术通过与内容/权益审阅保持分离。

### 工作流

节点位置和缩放保存在独立 `View.layout`；只改位置不应改写 Workflow 业务版本。参数、节点、连接与执行边界才改变 Workflow。固定输入保存明确记录版本；上游选择只列出真实依赖路径内的类型相容产物。服务端 `/execution/capabilities` 决定哪条路线具有本地处理器，未支持路线明确显示缺失能力。

执行按有界预算、最大步骤和重试次数运行。重试未知 HTTP 结果时保留幂等键，不仅因为页面按钮恢复可点就重复发起副作用。自动流程锁版需要明确审阅过的版本指纹，不能把刚生成且未经查看的动态上游自行批准。

## 6. 可访问性与设备适应

- 主要页面保留清晰标题，图标按钮提供可访问名称；原生输入和按钮可用 Tab、Enter、Space 操作。
- `:focus-visible` 使用可见轮廓；原生 dialog 提供焦点限制，关闭回到触发点或主内容；Escape 与关闭按钮有明确取消语义。
- 手机导航改为抽屉，模块功能列表横向可滚动；编辑器改为上下排列；较宽事实表在自己的区域滚动，避免整个页面横向溢出。
- 主触控按钮采用至少 44 CSS px 命中目标；精细拖拽操作仍提供数值字段或按钮替代。
- 系统 `prefers-reduced-motion` 或应用设置关闭空间位移、过渡与装饰动画；仍显示实际等待状态。
- `prefers-reduced-transparency`、应用设置和不支持 `backdrop-filter` 的环境都有实色回退；深浅外观分别定义文字、背景和语义颜色。
- `prefers-contrast: more` 提升文字/分隔线对比并去除关键透明背景。图标与文字共同表达错误，避免只看颜色。

`prefers-reduced-transparency` 的浏览器支持范围仍需核对，因此不能只依赖该媒体查询。[MDN 对此媒体查询的说明](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-transparency)与 [backdrop-filter 说明](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter)是兼容回退的直接依据。

## 7. 开源基础的选择

核对过 [Radix Primitives 的无障碍说明](https://www.radix-ui.com/primitives/docs/overview/accessibility)与[发布记录](https://www.radix-ui.com/primitives/docs/overview/releases)，以及 [Lucide React 接入](https://lucide.dev/guide/react/getting-started)和 [Version 1 说明](https://lucide.dev/guide/version-1)。Radix 解决 React 基础控件的键盘、焦点等问题；Lucide 提供图标体系。两者都不等于 Apple Liquid Glass 原生渲染。

当前实现为了保持无构建、无 CDN 的运行约束，使用原生 ES Modules、浏览器原生表单/dialog 和项目内 SVG 图标，没有安装或捆绑上述库，也没有声称使用 SF Symbols。以后引入组件库必须锁定实际版本、保留许可，并继续验收现有 API 与控件语义。

## 8. 验证要求与原生差异

正式验收按各轮证据报告记录以下检查，不以本文列出的代码实现替代运行证据：桌面/窄屏/手机布局，长标题与长列表，明暗外观，减少动态/透明度，键盘和焦点，空/等待/错误/成功状态，素材实际文件，时间线编辑和实际渲染，工作流参数和真实运行，以及全 23 模块/178 功能/534 路线的目录与控件核对。

开发检查已经通过实际截图发现并修复桌面遮罩参与网格、选择片段后工具条未更新、前端锁状态与服务端锁版不一致等问题。早期错误截图属于缺陷发现证据，不能登记成视觉通过。运行环境与官方浏览器包见 [browser-runtime.md](browser-runtime.md)；正式五轮范围和结果以回归报告为准。

CSS 模糊、透明、边缘高光和 transform 能实现相近的视觉原则，但不提供 SwiftUI/UIKit/AppKit 同一套实时光学折射、系统合成、触觉或设备输入适配。Linux Chromium 的截图不能证明 Safari、iPhone 硬件或所有 Apple 原生组件逐像素一致。当前准确表述是“依据最新官方公开原则实现的 Web 工作台”，不能承诺“所有苹果组件 100% 完美复刻”。

## 9. 本次重做的实际界面

从原始 HTML 的 `GROUPS` 读取并对照了八个业务分区、各分区模块成员和模块内功能顺序。桌面侧栏按原分区展开，紧凑模式保留专项操作入口。手机把同一组导航收起，打开分区后仍在当前项目中操作。全局搜索支持分区、模块、功能、参数和原始功能编号。结果以功能名与模块名显示，选定后可复制链接直达该功能。

首次使用时展示空项目列表和创建操作。选择或创建项目后进入剪辑室。素材库、工作流和交付记录继续作为可独立使用的工具。剪辑室在同一项目内提供媒体、监看器、检查器与时间线。独立分区的专项入口可进入具体剪辑工具，例如字幕、调色、输出规格或工程交换。编辑区保留来源版本引用，主操作界面使用文件名、片段名、时间码和版本号；源文件指纹及完整机器记录可以在有需要的技术详情和导出物中核查。

项目列表只列真实创建的项目。功能面板展示所选路线、原文控件、相关记录、可以执行的动作和保存操作。结构化记录编辑器读取服务端实体模式，普通文本、数字、枚举和布尔字段可直接编辑。高级字段按需展开。实际本地执行器返回成功、受阻和对应记录。未接通的选择在面板反映为可配置，具体可用范围见逐项台账。

此次重点对照的 Apple 官方资料：

- [WWDC26 Modernize your AppKit app，Design updates](https://developer.apple.com/videos/play/wwdc2026/289/?time=849) — 侧栏延伸至窗口边缘，使用选择字重、边缘辨识和控件响应表达层级。
- [Apple HIG Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars) — 可收起的前导导航，窄视口让出媒体空间。
- [Apple HIG Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars) — 与当前任务相关的操作成组，低频操作放入分区或披露。
- [Apple HIG Materials](https://developer.apple.com/design/human-interface-guidelines/materials) — 将浮动工具与内容分层，降低透明度、提高对比度时确保可读。
- [Adopting Liquid Glass](https://developer.apple.com/documentation/TechnologyOverviews/adopting-liquid-glass) — 对整组控件控制材质使用，按系统偏好调整。

`web/workspace.css`、`web/editor-room.css` 及相应工作区组件实现以上 Web 行为。桌面和手机宽度的 Chromium 截图、真实媒体操作及版本写入检查见本次各轮回归证据。Safari 与 Apple 原生渲染仍需分别实机确认。
