# AI 制作台、可调工作区与生成链路 · 五轮验收

每轮分别启动真实 FastAPI、SQLite、Chromium、WebKit、llama.cpp 与 ComfyUI，重复运行同一组代码和测试。时间线操作从界面提交，MP4 和工程文件下载后另行读取。源 HTML 的设计要求和当前可执行能力在 [逐项核对](feature-workflows.md) 中并列。

| 轮次 | 桌面 / 手机 | 全部测试用例 | 完整检查组 | 主工作区检查 | 专项检查 | PNG 证据 | 耗时 | 原始证据 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 1440×960 / 390×844 | 171 | 26 | 30 | 175 | 81 | 789.8 秒 | [执行记录](../evidence/ai-production/round-1/round-report.json) |
| 2 | 1536×960 / 414×896 | 171 | 26 | 30 | 175 | 81 | 806.1 秒 | [执行记录](../evidence/ai-production/round-2/round-report.json) |
| 3 | 1280×900 / 375×812 | 171 | 26 | 30 | 175 | 81 | 787.6 秒 | [执行记录](../evidence/ai-production/round-3/round-report.json) |
| 4 | 1600×1000 / 430×932 | 171 | 26 | 30 | 175 | 81 | 760.3 秒 | [执行记录](../evidence/ai-production/round-4/round-report.json) |
| 5 | 1366×900 / 360×800 | 171 | 26 | 30 | 175 | 81 | 793.6 秒 | [执行记录](../evidence/ai-production/round-5/round-report.json) |

## 每轮覆盖与未验项目

### 第 1 轮

用独立数据库和测试顺序 1 运行了全部 171 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 169 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/ai-production/round-1/unit-report.json)、[主界面与全目录](../evidence/ai-production/round-1/browser/browser-report.json)、[Chromium 制作台 17 组](../evidence/ai-production/round-1/director-chromium/report.json)、[WebKit 制作台 17 组](../evidence/ai-production/round-1/director-webkit/report.json)、[Chromium 自由布局 10 组](../evidence/ai-production/round-1/layout-chromium/report.json)、[WebKit 自由布局 10 组](../evidence/ai-production/round-1/layout-webkit/report.json)、[Chromium 转场 7 组](../evidence/ai-production/round-1/joins-chromium/report.json)、[WebKit 转场 7 组](../evidence/ai-production/round-1/joins-webkit/report.json)、[Chromium 首镜实帧 5 组](../evidence/ai-production/round-1/opening-chromium/report.json)、[WebKit 首镜实帧 5 组](../evidence/ai-production/round-1/opening-webkit/report.json)、[本地语言模型实推 4 组](../evidence/ai-production/round-1/language-model/report.json)、[真实语言模型首镜核验 3 组](../evidence/ai-production/round-1/opening-model/report.json)、[真实三模型复核与裁决 6 组](../evidence/ai-production/round-1/model-team/report.json)、[本地扩散模型实推 4 组](../evidence/ai-production/round-1/diffusion-model/report.json)、[数据控件与旧工作流 32 组](../evidence/ai-production/round-1/typed-controls/report.json)、[模块表单 7 组](../evidence/ai-production/round-1/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/ai-production/round-1/selection/report.json)、[单屏剪辑 11 组](../evidence/ai-production/round-1/studio/report.json)、[工作区 13 组](../evidence/ai-production/round-1/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/ai-production/round-1/webkit/report.json)。

本轮未检验付费云端供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 2 轮

用独立数据库和测试顺序 2 运行了全部 171 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 169 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/ai-production/round-2/unit-report.json)、[主界面与全目录](../evidence/ai-production/round-2/browser/browser-report.json)、[Chromium 制作台 17 组](../evidence/ai-production/round-2/director-chromium/report.json)、[WebKit 制作台 17 组](../evidence/ai-production/round-2/director-webkit/report.json)、[Chromium 自由布局 10 组](../evidence/ai-production/round-2/layout-chromium/report.json)、[WebKit 自由布局 10 组](../evidence/ai-production/round-2/layout-webkit/report.json)、[Chromium 转场 7 组](../evidence/ai-production/round-2/joins-chromium/report.json)、[WebKit 转场 7 组](../evidence/ai-production/round-2/joins-webkit/report.json)、[Chromium 首镜实帧 5 组](../evidence/ai-production/round-2/opening-chromium/report.json)、[WebKit 首镜实帧 5 组](../evidence/ai-production/round-2/opening-webkit/report.json)、[本地语言模型实推 4 组](../evidence/ai-production/round-2/language-model/report.json)、[真实语言模型首镜核验 3 组](../evidence/ai-production/round-2/opening-model/report.json)、[真实三模型复核与裁决 6 组](../evidence/ai-production/round-2/model-team/report.json)、[本地扩散模型实推 4 组](../evidence/ai-production/round-2/diffusion-model/report.json)、[数据控件与旧工作流 32 组](../evidence/ai-production/round-2/typed-controls/report.json)、[模块表单 7 组](../evidence/ai-production/round-2/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/ai-production/round-2/selection/report.json)、[单屏剪辑 11 组](../evidence/ai-production/round-2/studio/report.json)、[工作区 13 组](../evidence/ai-production/round-2/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/ai-production/round-2/webkit/report.json)。

本轮未检验付费云端供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 3 轮

用独立数据库和测试顺序 3 运行了全部 171 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 169 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/ai-production/round-3/unit-report.json)、[主界面与全目录](../evidence/ai-production/round-3/browser/browser-report.json)、[Chromium 制作台 17 组](../evidence/ai-production/round-3/director-chromium/report.json)、[WebKit 制作台 17 组](../evidence/ai-production/round-3/director-webkit/report.json)、[Chromium 自由布局 10 组](../evidence/ai-production/round-3/layout-chromium/report.json)、[WebKit 自由布局 10 组](../evidence/ai-production/round-3/layout-webkit/report.json)、[Chromium 转场 7 组](../evidence/ai-production/round-3/joins-chromium/report.json)、[WebKit 转场 7 组](../evidence/ai-production/round-3/joins-webkit/report.json)、[Chromium 首镜实帧 5 组](../evidence/ai-production/round-3/opening-chromium/report.json)、[WebKit 首镜实帧 5 组](../evidence/ai-production/round-3/opening-webkit/report.json)、[本地语言模型实推 4 组](../evidence/ai-production/round-3/language-model/report.json)、[真实语言模型首镜核验 3 组](../evidence/ai-production/round-3/opening-model/report.json)、[真实三模型复核与裁决 6 组](../evidence/ai-production/round-3/model-team/report.json)、[本地扩散模型实推 4 组](../evidence/ai-production/round-3/diffusion-model/report.json)、[数据控件与旧工作流 32 组](../evidence/ai-production/round-3/typed-controls/report.json)、[模块表单 7 组](../evidence/ai-production/round-3/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/ai-production/round-3/selection/report.json)、[单屏剪辑 11 组](../evidence/ai-production/round-3/studio/report.json)、[工作区 13 组](../evidence/ai-production/round-3/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/ai-production/round-3/webkit/report.json)。

本轮未检验付费云端供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 4 轮

用独立数据库和测试顺序 4 运行了全部 171 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 169 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/ai-production/round-4/unit-report.json)、[主界面与全目录](../evidence/ai-production/round-4/browser/browser-report.json)、[Chromium 制作台 17 组](../evidence/ai-production/round-4/director-chromium/report.json)、[WebKit 制作台 17 组](../evidence/ai-production/round-4/director-webkit/report.json)、[Chromium 自由布局 10 组](../evidence/ai-production/round-4/layout-chromium/report.json)、[WebKit 自由布局 10 组](../evidence/ai-production/round-4/layout-webkit/report.json)、[Chromium 转场 7 组](../evidence/ai-production/round-4/joins-chromium/report.json)、[WebKit 转场 7 组](../evidence/ai-production/round-4/joins-webkit/report.json)、[Chromium 首镜实帧 5 组](../evidence/ai-production/round-4/opening-chromium/report.json)、[WebKit 首镜实帧 5 组](../evidence/ai-production/round-4/opening-webkit/report.json)、[本地语言模型实推 4 组](../evidence/ai-production/round-4/language-model/report.json)、[真实语言模型首镜核验 3 组](../evidence/ai-production/round-4/opening-model/report.json)、[真实三模型复核与裁决 6 组](../evidence/ai-production/round-4/model-team/report.json)、[本地扩散模型实推 4 组](../evidence/ai-production/round-4/diffusion-model/report.json)、[数据控件与旧工作流 32 组](../evidence/ai-production/round-4/typed-controls/report.json)、[模块表单 7 组](../evidence/ai-production/round-4/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/ai-production/round-4/selection/report.json)、[单屏剪辑 11 组](../evidence/ai-production/round-4/studio/report.json)、[工作区 13 组](../evidence/ai-production/round-4/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/ai-production/round-4/webkit/report.json)。

本轮未检验付费云端供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。

### 第 5 轮

用独立数据库和测试顺序 5 运行了全部 171 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 169 个证据文件的内容校验值。

逐项证据见[后端用例](../evidence/ai-production/round-5/unit-report.json)、[主界面与全目录](../evidence/ai-production/round-5/browser/browser-report.json)、[Chromium 制作台 17 组](../evidence/ai-production/round-5/director-chromium/report.json)、[WebKit 制作台 17 组](../evidence/ai-production/round-5/director-webkit/report.json)、[Chromium 自由布局 10 组](../evidence/ai-production/round-5/layout-chromium/report.json)、[WebKit 自由布局 10 组](../evidence/ai-production/round-5/layout-webkit/report.json)、[Chromium 转场 7 组](../evidence/ai-production/round-5/joins-chromium/report.json)、[WebKit 转场 7 组](../evidence/ai-production/round-5/joins-webkit/report.json)、[Chromium 首镜实帧 5 组](../evidence/ai-production/round-5/opening-chromium/report.json)、[WebKit 首镜实帧 5 组](../evidence/ai-production/round-5/opening-webkit/report.json)、[本地语言模型实推 4 组](../evidence/ai-production/round-5/language-model/report.json)、[真实语言模型首镜核验 3 组](../evidence/ai-production/round-5/opening-model/report.json)、[真实三模型复核与裁决 6 组](../evidence/ai-production/round-5/model-team/report.json)、[本地扩散模型实推 4 组](../evidence/ai-production/round-5/diffusion-model/report.json)、[数据控件与旧工作流 32 组](../evidence/ai-production/round-5/typed-controls/report.json)、[模块表单 7 组](../evidence/ai-production/round-5/module-forms/report.json)、[分镜交接与创意选优 6 组](../evidence/ai-production/round-5/selection/report.json)、[单屏剪辑 11 组](../evidence/ai-production/round-5/studio/report.json)、[工作区 13 组](../evidence/ai-production/round-5/workspace/workspace-actions.json)、[WebKit 剪辑 11 组](../evidence/ai-production/round-5/webkit/report.json)。

本轮未检验付费云端供应商、真实平台OAuth/自动发布/回执/指标、尚未实现的专用路线与外部适配器、macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合、原生Liquid Glass逐像素一致、分布式多主机与生产负载、全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返。逐项功能状态以源需求核对表为准。
