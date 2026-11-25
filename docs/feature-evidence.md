# 功能实测凭据和独立验收

逐功能清单中的 `verification_runs` 指向已经运行的报告。每条记录给出仓库相对路径、原始文件 SHA-256、适用的图谱条件和观察范围。可从[运行索引](../contracts/feature-evidence.json)核对文件，从[逐项实现清单](implementation-matrix.md)查看当前整项验收数。这里的检查用于维护证据清单。正常剪辑、保存、入库、渲染及发布工作流使用自己的业务条件。

| 实际报告 | 图谱位置 | 可验证的结果 |
|---|---|---|
| [本地语音识别和字幕审核](../evidence/local-speech/report.json) | L04/Q01、Q02；V04/Q01、Q03 | 英文和普通话音源的词级时码、人工校对与 SRT 回读；同一中文音轨推迟 1.5 秒，可在保存的字幕中核验相同的时间差。 |
| [视频采样帧检索](../evidence/batch-production/visual-search/result.json) | S02/Q02、Q03、Q04 | 固定索引模型、跨模型查询隔离、字面回退及双浏览器源时码。 |
| [本地三模型两轮评议](../evidence/batch-production/agent-loop.json) | C09/Q03、Q06 | 两轮模型共识都因原片证据不匹配被拦截。通过解码的是另行人工修订的方案。 |
| [已录回执对照](../evidence/recorded-response/result.json) | D08/Q02；H10/Q02 | 回环夹具产生两份供对照的回执，回放期间模型请求数为零。该观察仅覆盖产品只读回放。 |
| [拍摄目录导入](../evidence/batch-production/browser/directory-import.json) | A05/Q05、Q06 | 两个路径的相同字节产生一个素材和两份来源记录，重新打开可读取导入状态。 |
| 同日素材对照 [Chromium](../evidence/matched-placements/chromium.json) · [WebKit](../evidence/matched-placements/webkit.json) | M09/Q02、Q03、Q06（局部） | 两条本地 FFmpeg 成片分别核对后，两个浏览器从帖子观察窗选择 0 至 0.5 秒。自然流量的基准留存 80/100、候选 55/100，同一发布日相差 −25 个百分点，付费流量无样本。编辑固定回执、反证、改法和停止条件。帖子与曲线使用合成数据。 |
| 模型生成杯具失真 [Chromium](../evidence/batch-production/mixed-routing/visual-review/chromium.json) · [WebKit](../evidence/batch-production/mixed-routing/visual-review/webkit.json) | G05/Q01、Q05；G06/Q04（局部） | 审阅使用同一实拍输入和已记录的模型产物。实际候选帧位于 0.375 秒，两个浏览器均保存了待修订、拒绝两个判断和各自理由。产品保真依靠人工判断，此轮浏览器使用已生成的字节。 双浏览器实播四帧参考，将输入 PTS 0.25 秒映射至原片 1.24 秒并保存待修订决议。 |

这些报告各有专用读取规则，核对正确的文件、报告字段及预期结果。单个 `passed: true` 或存在测试文件均不足以建立上述记录。这五种报告只支持表中明确列出的局部观察。运行时间、软件环境、模型能力和素材条件以各报告实际保存的内容为准；旧报告缺少统一的当前版本指纹，保留为空。

## 登记新运行

1. 将实际生成的 JSON 放在仓库 `evidence/` 内，并保留原始测量。索引中的路径按仓库根目录解析，禁止路径逃逸及符号链接。索引使用文件原始字节的 SHA-256。修复或重新运行可产生另一条记录，并保留先前记录及适用范围。
2. 对已列出的旧格式，读取规则固定报告类型、具体业务断言、功能 ID 和原文门槛。对今后正式验收，使用 `criterion-run-v1`。逐个功能记录运行身份、命令、环境（`local`、`production` 或 `external_live`）、运行目标、结束时间、原始 HTML 摘要及运行代码指纹。由执行器保存独立的原始测量文件并用 `measurement.path/sha256` 引用；测量需包含相同的 run_id、feature_id、runtime_sha256、environment、target 和成功状态。生产凭据还需 deployment_id，外部实联还需 service 和 external_receipt_id。填写可供核验的标识，勿记录平台凭据。
3. 原文条件采用 route ID，或 `control:<原 key>`，以及一基序号的步骤 S、质量门槛 Q、失败条件 F。每条运行断言含 `criterion`、`extent`、`passed`、`expected` 及文字 `observation`。原始测量文件以 `observations[criterion].actual` 和 `.extent` 保存实测值及覆盖范围。核验时严格比较 JSON 类型。局部条件使用 `partial`，完整条件使用 `complete`。同一运行内每个条件只登记一次。可运行的最小正、反例和所需字段见 [验证器测试](../tests/test_feature_run_evidence.py)。
4. 将 `{id, profile, path, sha256}` 加入索引 `runs`。对新报告只有检验通过、功能与门槛匹配的凭据才进入对应功能。在其他进程、模型、素材或生产环境运行时，必须记录各自的目标及结果。

## 晋级整项

执行器提交的运行结果与独立评审分别保存为不同文件。正式评审使用 `atlas-acceptance-review-1`，含 decision、feature_id、runtime_sha256、atlas_sha256、reviewer、reviewed_at、rationale、accepted，以及固定每份依据 ID 和 SHA-256 的 basis。将评审的 `{id, path, sha256}` 加入索引 `reviews`。同一功能和同一运行版本的一种决定只能登记一次。

- `full_business_acceptance` 要求同一功能、同一运行版本的正式实测凭据合起来覆盖原文全部路线、控件、步骤、质量门槛与失败条件。评审人需独立于每份依据的执行者，评审时间不得早于依据中的运行结束时间。
- `production_verified` 和 `external_live_verified` 各自需要同一目标环境实际运行的完整条件证据、独立评审及当前版本的整项业务验收。评审的 target 必须和所有依据中的实际目标相同。只有本地夹具时，这两项保持未晋级。
- 运行指纹由 `scripts.feature_run_evidence.runtime_fingerprint` 计算，覆盖后端、前端、原图谱和列出的依赖及运行配置。相关文件改变后，原有决定保留为历史依据，当前版本要重新验证。纯说明文档与新增证据文件不会使运行版本变化。
- SHA-256 检测证据与索引之间的内容变化。报告来源、评审人身份和观察是否足以证明业务陈述需要由维护者独立核对。全部功能的汇总由已验证决定计算。跨模块的 69 个原文场景另外审查。

在仓库根目录运行 `python scripts/build_implementation_manifest.py` 和 `python scripts/feature_workflow_audit.py` 更新派生清单，再执行 `python scripts/check_contracts.py`。只读复核可以直接执行检查器和 `python -m unittest tests.test_feature_run_evidence`，无需重新下载模型、调用服务或发布作品。
