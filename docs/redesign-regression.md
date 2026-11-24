# 剪辑工作区重做 · 五轮验收

每轮分别启动实际 FastAPI、SQLite、Chromium 与 Linux WebKit，重复运行同一组代码和测试。时间线操作从界面提交，MP4 和工程文件下载后另行读取。源 HTML 的设计要求和当前可执行能力在 [逐项核对](feature-workflows.md) 中并列。

| 轮次 | 桌面 / 手机 | 全部测试用例 | 完整检查组 | 主工作区检查 | 专项检查 | PNG 证据 | 耗时 | 原始证据 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 1440×960 / 390×844 | 141 | 14 | 30 | 80 | 42 | 74.5 秒 | [执行记录](../evidence/redesign/round-1/round-report.json) |
| 2 | 1536×960 / 414×896 | 141 | 14 | 30 | 80 | 42 | 72.4 秒 | [执行记录](../evidence/redesign/round-2/round-report.json) |
| 3 | 1280×900 / 375×812 | 141 | 14 | 30 | 80 | 42 | 73.0 秒 | [执行记录](../evidence/redesign/round-3/round-report.json) |
| 4 | 1600×1000 / 430×932 | 141 | 14 | 30 | 80 | 42 | 73.4 秒 | [执行记录](../evidence/redesign/round-4/round-report.json) |
| 5 | 1366×900 / 360×800 | 141 | 14 | 30 | 80 | 42 | 73.9 秒 | [执行记录](../evidence/redesign/round-5/round-report.json) |

每轮的 42 个 PNG 包含 41 张浏览器截图和 1 张 FFmpeg 抽取的媒体帧。五轮合计 205 张界面截图与 5 张媒体帧。每轮的 80 组专项检查都包含 Chromium 和 WebKit 各 11 组同窗剪辑操作。手机端使用触控模拟，设备实测范围见下面各轮的说明。

[独立复核原始结果](../evidence/redesign/five-round-independent-audit.json)记录了 395 个文件的校验、十份输出 MP4 的音视频解码和五轮后端用例集合核对。[逐轮目视复核](redesign-visual-review.md)列出截图的画面、缺陷、复查方式与浏览器差异。较早的两次候选运行保存在 `evidence/redesign/before-final-group-index` 和 `evidence/redesign/before-webkit-frame-fix`。正式五轮在分组核对和 WebKit 视频取帧修复后重新开始。

## 每轮覆盖与未验项目

### 第 1 轮

用独立数据库和测试顺序 1 运行了全部 141 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 79 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/redesign/round-1/unit-report.json)、[主界面与全目录](../evidence/redesign/round-1/browser/browser-report.json)、[数据控件与旧工作流 32 组](../evidence/redesign/round-1/typed-controls/report.json)、[模块表单 7 组](../evidence/redesign/round-1/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/redesign/round-1/selection/report.json)、[单屏剪辑 11 组](../evidence/redesign/round-1/studio/report.json)、[工作区 13 组](../evidence/redesign/round-1/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/redesign/round-1/webkit/report.json)。

本轮未检验真实模型供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 2 轮

用独立数据库和测试顺序 2 运行了全部 141 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 79 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/redesign/round-2/unit-report.json)、[主界面与全目录](../evidence/redesign/round-2/browser/browser-report.json)、[数据控件与旧工作流 32 组](../evidence/redesign/round-2/typed-controls/report.json)、[模块表单 7 组](../evidence/redesign/round-2/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/redesign/round-2/selection/report.json)、[单屏剪辑 11 组](../evidence/redesign/round-2/studio/report.json)、[工作区 13 组](../evidence/redesign/round-2/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/redesign/round-2/webkit/report.json)。

本轮未检验真实模型供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 3 轮

用独立数据库和测试顺序 3 运行了全部 141 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 79 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/redesign/round-3/unit-report.json)、[主界面与全目录](../evidence/redesign/round-3/browser/browser-report.json)、[数据控件与旧工作流 32 组](../evidence/redesign/round-3/typed-controls/report.json)、[模块表单 7 组](../evidence/redesign/round-3/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/redesign/round-3/selection/report.json)、[单屏剪辑 11 组](../evidence/redesign/round-3/studio/report.json)、[工作区 13 组](../evidence/redesign/round-3/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/redesign/round-3/webkit/report.json)。

本轮未检验真实模型供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 4 轮

用独立数据库和测试顺序 4 运行了全部 141 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 79 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/redesign/round-4/unit-report.json)、[主界面与全目录](../evidence/redesign/round-4/browser/browser-report.json)、[数据控件与旧工作流 32 组](../evidence/redesign/round-4/typed-controls/report.json)、[模块表单 7 组](../evidence/redesign/round-4/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/redesign/round-4/selection/report.json)、[单屏剪辑 11 组](../evidence/redesign/round-4/studio/report.json)、[工作区 13 组](../evidence/redesign/round-4/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/redesign/round-4/webkit/report.json)。

本轮未检验真实模型供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 5 轮

用独立数据库和测试顺序 5 运行了全部 141 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 79 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/redesign/round-5/unit-report.json)、[主界面与全目录](../evidence/redesign/round-5/browser/browser-report.json)、[数据控件与旧工作流 32 组](../evidence/redesign/round-5/typed-controls/report.json)、[模块表单 7 组](../evidence/redesign/round-5/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/redesign/round-5/selection/report.json)、[单屏剪辑 11 组](../evidence/redesign/round-5/studio/report.json)、[工作区 13 组](../evidence/redesign/round-5/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/redesign/round-5/webkit/report.json)。

本轮未检验真实模型供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。
