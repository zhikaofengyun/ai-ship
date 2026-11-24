# 苹果官方设计研究与 Web 实现边界

核对日期：2026-11-23（用户时区）。本笔记为开发中间资料。资料均来自 Apple Developer、Radix、Lucide、MDN 官方站点；读取了官方正文或官方 Markdown，不是只读搜索摘要。未把第三方仿制库当作 Apple 标准。

## 最新基线

Apple WWDC26「Platforms State of the Union」在设计段明确介绍 27 releases 对 Liquid Glass 的改进：复杂背景扩散增强，深色边缘与高光更清晰，透明至着色的可调外观，Mac/iPad 侧栏扩展至边缘，统一更紧的 Mac 窗口圆角，以及更明确的浮动工具栏滚动边缘效果。因此不能只依据 2025 初始版截图宣称已复刻最新版本。

来源：[WWDC26 官方视频与逐字稿](https://developer.apple.com/videos/play/wwdc2026/102/?id=206)，官方逐字稿的设计段，设计片段约 21:44–24 分钟。此处使用逐字稿核实，没有声称已逐帧观看视频。

## 官方规则与项目实现映射

| 项目 | 官方依据 | 建议实际落地 |
|---|---|---|
| 内容与控制分层 | HIG Materials：玻璃用于导航和控制，内容层避免使用 | 视频、时间线、证据列表采用干净稳定的底层；侧栏、浮动工具条、popover 使用玻璃 |
| 文字承载 | regular 材质处理背景亮度并扩散复杂内容；clear 适合视觉丰富背景的有限控件 | 主侧栏用较高遮盖度，播放器浮动控件可更透；不在所有卡片上加 blur |
| 层级 | HIG Layout：对齐、留白、分组、渐进披露；内容延展到控制层下方 | 工作台主画面优先；高级参数可展开；导航与检查器可收起；窄屏转为抽屉/底部 sheet |
| 动效 | HIG Motion：短促准确，符合手势方向，可取消；直接触摸反馈可比触控板更明显 | 按压、滑块拖动、选择滑块、抽屉开合有连续响应；避免把所有数字变化都做长动画 |
| 按钮 | HIG Buttons：必须有按下态；一般 44×44 pt 命中区域；同组主要通过样式区分主次 | Web 触控命中区采用至少 44 CSS px 是本项目转换值；图标可 18–20 px，但按钮区域不缩至图标大小 |
| 可访问性 | HIG Accessibility：放大文本、多通道状态、对比度、支持用户偏好 | 保留 focus-visible；图标按钮有名称；错误文本与状态不只依赖颜色；可操作到全部关键路径 |
| 降低动态 | 官方要求能调整或关闭不必要动效 | `prefers-reduced-motion` 下去掉空间运动与循环装饰动画，只保留必要状态反馈 |
| 降低透明度 | Apple 要求透明度/对比度设置下仍可用；MDN 标明媒体查询兼容性仍有限 | 同时支持媒体查询与应用内“减少透明度”设置；不支持 backdrop-filter 时使用实色备用 |
| 深浅外观 | 材质应在外观与背景改变后仍清晰 | 明暗两套语义 token；避免统一 opacity 让子元素文字也变淡 |

HIG 来源：

- [Materials](https://developer.apple.com/design/human-interface-guidelines/materials.md)。
- [Layout](https://developer.apple.com/design/human-interface-guidelines/layout.md)。
- [Motion](https://developer.apple.com/design/human-interface-guidelines/motion.md)。
- [Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility.md)。
- [Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons)。
- [Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass)。
- [MDN prefers-reduced-transparency](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40media/prefers-reduced-transparency)。
- [MDN backdrop-filter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter)。

## 可直接采用的组件实现策略

以下数值是本项目建议值，不是苹果公布的精确渲染参数：

- 字体 `-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif`；不额外分发系统字体。
- 中性色内容底、单一蓝色或青色强调色。主内容保留足够留白与稳定对齐。
- 悬浮控制容器使用 `backdrop-filter: blur(28px) saturate(1.5)`，加低透明度边缘、内高光、外部柔和阴影；避免 blur 数值动态动画导致重绘。
- 圆角按层级采用约 12/18/24 px，胶囊按钮使用 999 px。嵌套容器内外圆角应协调，但不应把整个密集编辑器做成大量圆形玻璃卡片。
- 点击状态约 120–160 ms；侧栏/检查器约 220–320 ms；数字和拖动要立即响应。动画可重入、可中断，不能用 transition 延迟真实数据提交。
- 将网络 `loading / ready / empty / error / offline` 与业务 `draft / running / blocked / completed` 分开呈现，不把动画结束当作任务完成。
- 滑块在拖动时即时显示数值；支持键盘和直接输入；松手可提交保存。设置项要能重置、恢复默认值。
- 弹层从触发点出发，关闭回到同一触发点；Escape、外部点击和键盘 focus 恢复一致。

## 官方开源基础核对

### Radix Primitives

[官网](https://www.radix-ui.com/primitives)与[无障碍说明](https://www.radix-ui.com/primitives/docs/overview/accessibility)确认其定位为无样式 React 基础组件，处理 WAI-ARIA、键盘、焦点管理。适合 Dialog / Popover / Slider / Tabs / Switch，不提供 Liquid Glass 光学效果。图标标签与应用上下文仍由开发者负责。

[发布记录](https://www.radix-ui.com/primitives/docs/overview/releases)目前可见最新条目 2026-07-20，包含 Dialog ARIA 引用、Slider 提交、Toast Escape、Tooltip 重复 mount 修复；2026-07-06 含 React 19.2 相关旧事件处理修复。采用时应锁定安装的实际版本和 lockfile，不能只写“latest”。

### Lucide

[React 接入说明](https://lucide.dev/guide/react/getting-started)确认树摇优化、按图标导入、大小和线宽控制。[Version 1 说明](https://lucide.dev/guide/version-1)显示 V1 已发布，默认 `aria-hidden`，因此图标按钮必须在按钮上提供可访问名称。适合清晰、一致的线性图标；它不是 SF Symbols，也不能宣称完全一致。

## 应进入 UI 验证清单的实际检查

1. 桌面 1440、窄桌面 1024、手机 390 宽，导航、时间线与侧栏没有重叠遮挡；长名称不推坏布局。
2. 明暗外观、减少动态、减少透明度、200% 文字/页面缩放下关键路径可完成。
3. 键盘可完成导入/选择/参数调整/保存/运行/取消/错误恢复；弹层 focus 进出正确。
4. 空列表、长列表、错误、等待、离线、禁用、成功态全部有可区分的信息。
5. 玻璃区域承载文字时用实际视频高亮/黑暗/高频纹理背景检查可读性。
6. 拖动和缩放过程中不触发昂贵网络保存；动画不阻塞操作；滚动无多层全屏 blur。

## 如实说明边界

浏览器 CSS 的 blur、透明度、边缘高光和 transform 可以实现相近材质与交互原则，但不能等同 Apple 原生 SwiftUI/UIKit/AppKit 的光学折射、系统级渲染和输入设备适配。未经过原生同屏基准与逐组件视觉比较，不得称“所有组件逐像素完美复刻”。应表述为“按苹果最新官方规范实现，并通过已记录的 Web 视觉与交互验收”。
