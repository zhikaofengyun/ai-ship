# 本次工作区重做的依据与对应实现

| 用户动作 | 设计或协议来源 | 当前项目的实现与核验位置 |
| --- | --- | --- |
| 在同一个工程从素材看到时间线，再从时间线导出 | [Apple · Arrange the main window in Final Cut Pro for Mac](https://support.apple.com/en-in/guide/final-cut-pro/ver2a27194eb/mac) | 剪辑室常驻素材栏、源/时间线预览、片段检查器和时间线。小屏切换面板保留工程和播放头。`scripts/browser_edit_session.py` 从原始文件入手实际下载并解码成片。 |
| 选段、移动、相邻片段联动 | [Apple · Extend or shorten clips](https://support.apple.com/guide/final-cut-pro/extend-or-shorten-clips-ver9847ec25/12.4/mac/26.6) · [Apple · Arrange clips](https://support.apple.com/guide/final-cut-pro/arrange-clips-in-the-timeline-verc147f195/mac) | 胶片带用实际素材采样，支持拖端点、逐帧、分割、波纹联动和显式撤销重做。本地轨道模型覆盖其中一部分剪辑语义。 |
| 在素材上方操作，控制项目维持可读 | [Apple HIG · Materials](https://developer.apple.com/design/human-interface-guidelines/materials) · [Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars) · [WWDC26 · Modernize your AppKit app](https://developer.apple.com/videos/play/wwdc2026/289/) | 在导航和浮动工具上使用半透明层，媒体和轨道使用独立内容底色。键盘、焦点、点击反馈、深浅色、降动态与降透明度均有相应路径。Linux 浏览器不产生 AppKit 的原生 Liquid Glass 合成。 |
| 保持原业务入口和跨模块引用 | [随项目附带的原始 Atlas HTML](AI_Video_Agent_Atlas.html) | 8 个顶层分区映射 23 模块。逐项对齐 178 项功能、534 条可选路线、742 个控件与输入输出。机器核对入口为 `scripts/feature_workflow_audit.py --check`。 |
| 携带字幕与工程继续编辑 | [W3C · WebVTT](https://www.w3.org/TR/webvtt1/) · [OpenTimelineIO 0.18.1](https://opentimelineio.readthedocs.io/en/v0.18.1/) | SRT/WebVTT 采用逐句纯文本子集。OTIO 使用固定版本的官方 serializer，媒体链接需要指向已核对的当前项目来源。不能保留的编辑操作会阻止导入。原工程和已经导出的成片仍可独立读取。 |
| 核对真实图片、视频、音轨和成片 | [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html) · [FFprobe](https://ffmpeg.org/ffprobe.html) | 缩略图取自素材，独立音轨可以听、剪和混音。服务端用真实媒体探测、编码和解码。人工节拍存储关联音轨、采样率和样点，由用户绑定到剪辑工程。 |

## 屏幕实录中的素材

演示项目使用 NASA 阿波罗 8 号地出照片。两个测试镜头分别用同一张照片的全景与近景构图制作，另加入合成音调，以便验证音轨分离、静音及导出。展示的是本地编辑器的真实屏幕录制。初始项目库没有展示用的作品或回执。照片档案见 [NASA Johnson Space Center · AS08-14-2383](https://eol.jsc.nasa.gov/scripts/sseop/photo.pl?frame=2383&mission=AS08&roll=14)。

## 工程交换限制

打开「工程」可交换 `.otio`。同一工程可以保留直切、轨道、源入出点与空隙。AI Ship 在命名空间元数据中保存字幕、支持的画面参数和来源版本。其他剪辑软件可能忽略这些扩展；导出文件本身不携带源媒体。回导遇到离线片段时，请在当前项目中关联实际媒体。真实 SHA 与版本信息保留在技术证据和数据库中。界面用文件名、片段、版本号和可读状态标识对象。
