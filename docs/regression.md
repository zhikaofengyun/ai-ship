# 五轮正式回归与交付证据

五轮均在同一份冻结代码上重新执行完整已实现范围的回归，全部通过。它们不是原文178功能的全业务验收，不能把目录遍历、测试数量或重复运行转换为功能完成率。

共同代码 SHA-256：`7911da6e17b66c3caa312c4056cd7100dd573cc642b28093bd11bc86107c7054`。逐文件指纹包含 app/web/contracts/scripts/tests、CI和依赖/部署配置；说明文档和新产生的证据不进入代码指纹。每轮结束时再次验证代码未改变。

## 逐轮总表

| 轮次 | UTC 时间 | 全套后端测试 | 类型/边界浏览器断言 | 主浏览器断言 | 目录遍历 | 桌面 / 手机 | 截图 | 秒数 |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| 1 | 2026-11-24T03:59:39.606646+00:00 → 2026-11-24T04:01:53.756612+00:00 | 122，0失败/0跳过 | 32 | 30 | 23模块/178功能/534路线 | [1440, 960] / [390, 844] | 22 | 133.972 |
| 2 | 2026-11-24T04:01:53.867641+00:00 → 2026-11-24T04:04:12.139426+00:00 | 122，0失败/0跳过 | 32 | 30 | 23模块/178功能/534路线 | [1536, 960] / [414, 896] | 22 | 138.169 |
| 3 | 2026-11-24T04:04:12.250407+00:00 → 2026-11-24T04:06:31.677329+00:00 | 122，0失败/0跳过 | 32 | 30 | 23模块/178功能/534路线 | [1280, 900] / [375, 812] | 22 | 139.297 |
| 4 | 2026-11-24T04:06:31.778675+00:00 → 2026-11-24T04:08:55.989763+00:00 | 122，0失败/0跳过 | 32 | 30 | 23模块/178功能/534路线 | [1600, 1000] / [430, 932] | 22 | 144.058 |
| 5 | 2026-11-24T04:08:56.084620+00:00 → 2026-11-24T04:11:18.924336+00:00 | 122，0失败/0跳过 | 32 | 30 | 23模块/178功能/534路线 | [1366, 900] / [360, 800] | 22 | 142.696 |

源742控件包括178个路线选择器。主浏览器逐项验证其余564个控件键存在，并逐一切换534个路线选项；这不表示742个控件的所有输入组合均已穷举。类型控件专项另外执行版本保真、金额/权重、时基、恶意标识符、布局和真实数据消费等正反向断言。

## 每轮都执行的内容

- 原文HTML字节hash与提取对象往返相等、全部目录引用、64类Schema和实现分级/场景集合。源文档24条路线缺少preserves/recovery，明确列出而不修改源文档。
- 所有自动发现的后端测试：身份/租户、CAS/事务/不可变历史、幂等、字段锁/参数、SSE/会话、预算、请求体大小、工作流检查点/租约/fencing、故障恢复、真实FFmpeg、当前授权、发布人工回执、指标和实验、备份/恢复。不同轮次打乱测试顺序。
- 全部浏览器JS语法/本地import、Python与Web格式、运行时OpenAPI快照无漂移。
- 真实Chromium启动独立服务：新建空项目→文件输入上传→时间线分割/撤销/重做/保存→确认锁版→实际FFmpeg编码→下载并全片解码；另运行真实素材处理→提音的工作流。
- 全23模块、178功能和534路线目录交互，配置保存后刷新、主要桌面页面、深浅外观、降低动态/透明度、键盘命令面板、不同手机尺寸、页面溢出及导航关闭。
- 类型控件与导入标识符负测：明确零与未填写、引用不悄悄升级、业务时间与媒体时基区分、事件不重复注册、恶意ID不生成HTML节点、布局不改业务版本，以及脚本/衍生媒体在真实API上的消费链。

## 每轮证据与未检查范围

### 第 1 轮

已检查：上节全部检查均重新执行，测试顺序种子为 `1`；没有复用上轮数据库或测试通过结果。

证据：[总报告](../evidence/round-1/round-report.json)、[每项后端结果](../evidence/round-1/unit-report.json)、[浏览器逐功能/路线与截图清单](../evidence/round-1/browser/browser-report.json)、[类型与负向交互](../evidence/round-1/typed-controls/report.json)、[实际下载视频](../evidence/round-1/browser/browser-render.mp4)、[代码逐文件指纹](../evidence/round-1/source-fingerprint.json)。总报告还记录各步骤命令、退出码、耗时、日志和所有产物SHA-256。

未检查/不能据此认定完成：真实模型供应商、真实平台OAuth/自动发布/回执/指标；尚未实现的专用路线与外部适配器；Safari/WebKit/Firefox与物理iPhone；原生Liquid Glass逐像素一致；分布式多主机与生产负载。另外未穷举所有控件输入排列、全部辅助技术/离线模式、长片/全部编码/VFR色彩场景和原文每条语义质量门槛。

### 第 2 轮

已检查：上节全部检查均重新执行，测试顺序种子为 `2`；没有复用上轮数据库或测试通过结果。

证据：[总报告](../evidence/round-2/round-report.json)、[每项后端结果](../evidence/round-2/unit-report.json)、[浏览器逐功能/路线与截图清单](../evidence/round-2/browser/browser-report.json)、[类型与负向交互](../evidence/round-2/typed-controls/report.json)、[实际下载视频](../evidence/round-2/browser/browser-render.mp4)、[代码逐文件指纹](../evidence/round-2/source-fingerprint.json)。总报告还记录各步骤命令、退出码、耗时、日志和所有产物SHA-256。

未检查/不能据此认定完成：真实模型供应商、真实平台OAuth/自动发布/回执/指标；尚未实现的专用路线与外部适配器；Safari/WebKit/Firefox与物理iPhone；原生Liquid Glass逐像素一致；分布式多主机与生产负载。另外未穷举所有控件输入排列、全部辅助技术/离线模式、长片/全部编码/VFR色彩场景和原文每条语义质量门槛。

### 第 3 轮

已检查：上节全部检查均重新执行，测试顺序种子为 `3`；没有复用上轮数据库或测试通过结果。

证据：[总报告](../evidence/round-3/round-report.json)、[每项后端结果](../evidence/round-3/unit-report.json)、[浏览器逐功能/路线与截图清单](../evidence/round-3/browser/browser-report.json)、[类型与负向交互](../evidence/round-3/typed-controls/report.json)、[实际下载视频](../evidence/round-3/browser/browser-render.mp4)、[代码逐文件指纹](../evidence/round-3/source-fingerprint.json)。总报告还记录各步骤命令、退出码、耗时、日志和所有产物SHA-256。

未检查/不能据此认定完成：真实模型供应商、真实平台OAuth/自动发布/回执/指标；尚未实现的专用路线与外部适配器；Safari/WebKit/Firefox与物理iPhone；原生Liquid Glass逐像素一致；分布式多主机与生产负载。另外未穷举所有控件输入排列、全部辅助技术/离线模式、长片/全部编码/VFR色彩场景和原文每条语义质量门槛。

### 第 4 轮

已检查：上节全部检查均重新执行，测试顺序种子为 `4`；没有复用上轮数据库或测试通过结果。

证据：[总报告](../evidence/round-4/round-report.json)、[每项后端结果](../evidence/round-4/unit-report.json)、[浏览器逐功能/路线与截图清单](../evidence/round-4/browser/browser-report.json)、[类型与负向交互](../evidence/round-4/typed-controls/report.json)、[实际下载视频](../evidence/round-4/browser/browser-render.mp4)、[代码逐文件指纹](../evidence/round-4/source-fingerprint.json)。总报告还记录各步骤命令、退出码、耗时、日志和所有产物SHA-256。

未检查/不能据此认定完成：真实模型供应商、真实平台OAuth/自动发布/回执/指标；尚未实现的专用路线与外部适配器；Safari/WebKit/Firefox与物理iPhone；原生Liquid Glass逐像素一致；分布式多主机与生产负载。另外未穷举所有控件输入排列、全部辅助技术/离线模式、长片/全部编码/VFR色彩场景和原文每条语义质量门槛。

### 第 5 轮

已检查：上节全部检查均重新执行，测试顺序种子为 `5`；没有复用上轮数据库或测试通过结果。

证据：[总报告](../evidence/round-5/round-report.json)、[每项后端结果](../evidence/round-5/unit-report.json)、[浏览器逐功能/路线与截图清单](../evidence/round-5/browser/browser-report.json)、[类型与负向交互](../evidence/round-5/typed-controls/report.json)、[实际下载视频](../evidence/round-5/browser/browser-render.mp4)、[代码逐文件指纹](../evidence/round-5/source-fingerprint.json)。总报告还记录各步骤命令、退出码、耗时、日志和所有产物SHA-256。

未检查/不能据此认定完成：真实模型供应商、真实平台OAuth/自动发布/回执/指标；尚未实现的专用路线与外部适配器；Safari/WebKit/Firefox与物理iPhone；原生Liquid Glass逐像素一致；分布式多主机与生产负载。另外未穷举所有控件输入排列、全部辅助技术/离线模式、长片/全部编码/VFR色彩场景和原文每条语义质量门槛。

## 关键缺陷发现与修复证据

正式通过轮次之前的检查发现并修复：桌面导航遮罩参与网格、片段选择后工具条禁用、前端锁状态与服务端锁版脱节、真实probe时长与标注/音频锚点字段不一致、TimeMap整数ticks与反查格式不一致、当前Grant/撤权漏查、已计量费用可被释放、未知模型请求可改预算换键重发、损坏交付ZIP可错误恢复完成、导入ID被解析成HTML，衍生媒体无法直接进入剪辑，以及弹窗延迟自动聚焦抢走输入。对应测试方法保存在各轮unit-report或typed-controls报告。

`evidence/development-*`保留部分开发检查及失败记录。quick浏览器报告未遍历完整目录，不能算正式五轮。`evidence/failed-round-1-attempt-1`保留未通过的第一次正式尝试：弹窗焦点竞争与CSS格式检查失败；修复后从第1轮重新完整运行，不计入通过轮数。另有superseded-round-1/2曾通过当时的检查，逐图审阅发现交付规格未读取probe及移动画布/首帧证据不足，修复后不再计入最终通过轮。正式轮的通过结论仅由独立round-report及summary脚本校验生成，旧失败截图没有被改名冒充通过。

## 产品验收仍未完成

独立功能分级为105真实局部路径、21通用框架、7适配器未实联、45仅合同配置，整项原文业务验收0。原文69场景为46局部覆盖、23未覆盖、0完整验收。参见[source-coverage-review.md](source-coverage-review.md)与[scenario-coverage.md](scenario-coverage.md)。所有真实模型测试采用明确注入transport，没有真实付费供应商或平台账号联调。

浏览器为Linux实际Chromium、沙箱开启。截图证明本次页面和布局的实际表现，不证明苹果原生光学效果逐像素一致；独立视觉审阅见[visual-review.md](visual-review.md)。当前工作区没有Docker运行时；容器配方的实际构建结果应看GitHub Actions单独记录，不以本地Python验证代替。

复验：先按README安装依赖，再运行`python scripts/run_regression.py --round N --output evidence/新目录`。五个标准目录全部通过且代码未变化后，运行`python scripts/summarize_regression.py`可重新校验每个证据hash并生成本报告。
