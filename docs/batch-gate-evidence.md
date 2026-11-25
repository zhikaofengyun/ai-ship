# 本轮按原文核对的门槛与失败条件

以下核对沿着一次真实制作的先后顺序，定位在上传的 v6.0 图谱。Q 编号对应所在功能 operation.qualityGates 的一基序号，F 编号对应 operation.failurePaths。其他原文要求仍完整保存在 [逐功能对照](feature-workflows.md)。某个门槛的证据只能说明表中实际做过的部分。

| 图谱位置 | 在正常业务入口验证的行为 | 证据 |
| --- | --- | --- |
| P03/Q01、Q02、Q03 | 审批固定到市场、账号、来源及文本版本。改动 SKU、价格数字或禁词会被拒绝，受众假设保留录入身份。实际执行检查字幕和有效期 | [集成测试](../tests/test_batch_market.py) · [浏览器报告](../evidence/batch-production/browser/market-brief.json) |
| A02/Q01、Q02、Q04 | Commons 实联下载的视频有来源、大小、许可及摘要。读取成功后使用授权仍待核实 | [网络实测记录](../evidence/batch-production/network-observations.json) · [中途停用测试](../tests/test_source_media.py) |
| S02/Q02、Q03、Q04 | 以模型图及分词器摘要隔离空间，缺模型保留字面搜索。实测授权撤回后排除语义命中 | [真实模型结果](../evidence/batch-production/semantic-retrieval.json) · [索引测试](../tests/test_asset_index.py) |
| I05/Q01、Q04、Q05；D06/Q02、Q05 | 用途和准入变化传递到衍生件；重放备份时重核现行墓碑与租约。外部副本单独登记为待核 | [准入、撤回与恢复测试](../tests/test_asset_lifecycle.py) |
| C08/Q01、Q05、Q06 | 批次绑定版本化 TaskContract，在实际时间线及编码件分别检查；未知必需证据或编码时长不符会阻断产物交付 | [质量合同测试](../tests/test_batch_acceptance.py) |
| C10/Q01、Q03、Q04；F05/Q02、Q03 | 工作流局部改图受版本与节点状态守卫，检查点和已发生的副作用保留；迟到回执能核对回原意图 | [改图测试](../tests/test_execution.py) · [发布核对](../tests/test_publication_lifecycle.py) |
| H08/Q01、Q03、Q05 | 已登记配方接受运行时合同指纹、负向图和能力检查。核验失败保留套件，禁止用未审核配方派发 | [底座资格测试](../tests/test_harness_qualification.py) |
| O04/Q01、Q03；O05/Q01、Q03 | 查询排队、租约与故障原因；仅对可比较输出范围做样本比较。时间线、短片和 1080 竖片的环境与实测分开记录 | [观测测试](../tests/test_batch_observability.py) · [短片和草稿](../evidence/batch-production/capacity.json) · [竖片](../evidence/batch-production/portrait-capacity.json) |
| F07/Q01、Q03、Q04、Q06；F05/F03 | 本地取消、远端实际帖子、迟到成功和人工撤下各自记账。旧帖及其历史指标仍可核对 | [撤回与晚回执测试](../tests/test_publication_lifecycle.py) |
| M09/Q02、Q06、Q08；M09/F04 | 先核实平台帖子和下载成片，再用实渲时间图映射。逐位置比较按账号、流量、观察窗和原片版本分组。没有曲线端点时显示缺项，不能据此断言单镜头提升销量 | [真实文件浏览器回归](../evidence/batch-production/browser/placement-performance.json) · [查询界面](../evidence/batch-production/browser/placement-performance.png) |
| X03/Q02、Q03、Q05 | 固定训练集与独立留出集，保存失败样本；发布和回退有各自审批。评测不会直接替换正在使用的策略 | [评分策略测试](../tests/test_score_policies.py) |

[本轮实现审计](source-coverage-review.md) · [69 项场景逐项审计](scenario-coverage.md) · [旧版逐门槛证据快照](quality-gate-evidence.md)
