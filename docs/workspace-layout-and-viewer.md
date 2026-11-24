# 制作台、人工接管与工程交换

项目制作台的目标和分镜在左，素材证据和画面在右。用户可拖动两栏之间的分隔线，把空间留给当前任务。减小窗口或扩大导航导致剩余宽度不足时，两个区域改为上下排列。导航栏在桌面和手机抽屉里分别记住宽度。数字都是用户的本地显示偏好，调整窗口不会修改剪辑版本或发起新的 AI 调用。

| 使用位置 | 可实际操作 | 原 HTML 对应 |
| --- | --- | --- |
| 制作台 | 拖动导航与制作方案分隔线，键盘微调、恢复默认，关闭后重新进入仍保留比例 | W01 结构化控制，W04 审阅与接管，W07 多端工作区 |
| 桌面剪辑工作区 | 拖动相邻面板边界、预览与时间线的边界。拖动标题交换面板位置，布局菜单可选择全部六种顺序、时间线在上方或下方并恢复默认 | W03 素材与编辑联动，W07 多端工作区，W08 用户显式偏好 |
| 手机剪辑工作区 | 调整抽屉宽度，拖动监视器下方的横向把手改变预览高度。切换素材、时间线、检查器时保留播放位置 | E03 实时预览与定位，W07 多端工作区 |
| 源片与时间线监视器 | 单击或按空格播放、暂停，切换适应窗口、填满窗口、原始像素及缩放比例，拖动画面查看超出视口的区域。预览获得焦点后可按 F 切换全屏、0 复位、加减键缩放。原片有独立的播放位置 | E03 实时预览与定位，W03 素材与编辑联动 |
| 人工接管 | 按指定时间线 ID 打开。用户在普通工作区、已选 AI 时间线和其他工程中未保存的修改分别恢复。切换已保存工程时按各自工程读取 | W03、W04、E07 历史与协作冲突 |
| 项目交换 | OTIO 交叠按名义切点、入偏移、出偏移展开或还原。标准叠化使用 SMPTE_Dissolve，两种擦除与两种经黑白场使用 Custom_Transition 和类型扩展。回导按新片段 ID 重新连接相邻镜头 | E05 画面音效与版式、Y06 成片与工程导出、Y09 外部编辑回导 |

分隔线支持方向键、Shift 加方向键、Home、End、Enter 和双击恢复。桌面显示偏好保存在当前浏览器，手机抽屉宽度单独保留。窄屏采用面板切换，桌面排列在宽度恢复后生效。当前提供三列停靠和上下布局，不支持拖出成为独立操作系统窗口。真机 Safari、系统级缩放和读屏组合仍需实机验收。

## 校验依据

在每组改动前后重新读取完整原 HTML，解析内嵌 JSON，与工程内合同作全对象相等比较，再定位该组工作的功能及实现步骤。[原文核对记录](../evidence/ai-production/source-checks/layout-and-exchange.jsonl)

窗口参考 Apple 的 [Split Views](https://developer.apple.com/design/human-interface-guidelines/split-views)、[Final Cut Pro 主窗口布局](https://support.apple.com/guide/final-cut-pro/ver2a27194eb/mac)与[监视器缩放](https://support.apple.com/guide/final-cut-pro/ver199c39112/mac)。键盘与指针调节参考 [React Resizable Panels](https://github.com/bvaughn/react-resizable-panels)。实现沿用原生浏览器模块，无额外 UI 框架依赖。预览以真实媒体或输出帧的宽高计算，用户缩放时字幕与安全框随同画面变化。[Fullscreen API](https://developer.mozilla.org/en-US/docs/Web/API/Fullscreen_API/Guide)

真实浏览器专项会生成带运动的 H.264 视频，经应用导入、时间线保存和回读。测试量取真实元素宽度和可见像素，检查拖动、键盘、停靠位置、全屏、浏览器拒绝原生全屏时的窗口内展开、窄屏布局、播放进度和数据版本。开发中曾在 WebKit 发现，浏览器原生标题拖放以后下一次拖动画面可能缺少 pointerdown，导致无法平移。现在面板换位使用指针捕获并独立于素材文件拖放，在同一会话里先交换面板再拖动画面做回归。[专项脚本](../scripts/browser_workspace_layout.py)

工程交换基于 [OpenTimelineIO 的时间结构](https://opentimelineio.readthedocs.io/en/v0.18.1/tutorials/time-ranges.html)与[转场对象规范](https://opentimelineio.readthedocs.io/en/v0.18.1/tutorials/otio-serialized-schema.html)。导入检查左右素材的时间余量、轨道、类型、身份与当前权限。缺失媒体需要显式重连，未知自定义转场、来源不足以及禁用转场会被阻止。第三方软件能否显示四种扩展效果取决于该软件，当前仅通过官方 OTIO 序列化器和本项目的往返测试。[交换测试](../tests/test_timeline_exchange.py)
