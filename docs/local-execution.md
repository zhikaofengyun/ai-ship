# 有界本地工作流合同

这份合同只描述实际可执行的确定性本地路线。它不将原文 534 路线都标成已实现。

## 路线与能力

| 原文路线 | 操作 | 真实输出 | 仍需另行核实 |
| --- | --- | --- | --- |
| G02-R2 / G03-R2 | 固定生成配置，请求静帧或视频，支持项目原图或在固定视频原件的指定时码解码出一帧 PNG 作为模型输入 | 经探针核对的生成素材、供应商作业回执 | 需安装并配置相应服务与权重，远端等待从同一请求恢复 |
| E01-R3 | 导入人工编辑的 OpenTimelineIO 文档并核对本项目的素材 | 独立 Timeline 及来源区间 | 第三方未知效果与未解析来源会阻止导入 |
| I03-R2 / I04-R1 / I07-R2 | 明确参数的 FFmpeg 逐素材处理 | AssetVersion、TransformRun | 原文自适应推荐、AI 消除/修复不在此适配器 |
| L01-R1 | 真实视频差异切镜头候选 | Shot 集合 | 语义和主体标签未自动证明 |
| L07-R1 | 实际解码并提取原声音轨 | Audio | 无音轨时明确失败，不能造静音当原声 |
| V01-R3 | 人工脚本写入与引用校验 | Script | 不把录入文字当作已验证商品事实 |
| V03-R2 | 使用本项目已审核的 Flite 内置音色合成固定版本的英文脚本 | Audio、ToolCall，实际 WAV 字节、采样数与时长 | 音色用途须经审批，词级打点和发音审阅单独进行 |
| E01-R1 | 内部 Timeline 数据入库 | Timeline | 未支持的效果必须由渲染器拒绝 |
| E08-R1 | 用户确认精确 hash 后锁版 | Timeline 新版本 | 技术冻结不代替内容/版权审批 |
| Y03-R1 | 真实 FFmpeg 合成和技术质检 | RenderJob、Artifact、RenderManifest | publish_ready 仍为 false，语义/事实质检另行处理 |
| Y06-R2 | 实际 ZIP 离线交付 | DeliveryPackage | 不会自动上传外部存储或发布 |

## 输入、参数与产物绑定

`GET /api/v1/execution/capabilities`含每条路线的 JSON Schema 入参、输入实体类型、输出类型和真实边界。用户配置必须声明 max_steps、max_attempts、budget；可声明 max_seconds（本地默认 600 秒，上限 3600 秒）。生成请求按已登记的配置派发，等待回执时不重复提交；本地设备成本没有单价时保持未知。

下面是一条能够实际跑通的两节点链。原始素材必须由导入接口真实入库，再替换示例中的现存 ID 和版本。代码中的 `<asset-id>` 是占位说明，不是演示数据库内容。

```json
{
  "nodes": [
    {
      "id": "transform",
      "feature_id": "I07",
      "route_id": "I07-R2",
      "parameters": {},
      "operation_args": {"recipe": {"trim_start": 0, "trim_end": 1, "scale": 1.016}},
      "input_bindings": {"asset_id": {"record_id": "<asset-id>", "version": 1}}
    },
    {
      "id": "audio",
      "feature_id": "L07",
      "route_id": "L07-R1",
      "parameters": {},
      "operation_args": {},
      "input_bindings": {"asset_id": {"node_id": "transform", "output": "version"}}
    }
  ],
  "edges": [{"source": "transform", "target": "audio", "type": "control"}],
  "max_steps": 10,
  "max_attempts": 2,
  "max_seconds": 120,
  "budget": 0
}
```

这里的控制边只规定先后，实际数据读取由 typed input binding 固定版本。输入绑定也可设置 value 为 ref、data 或 version；缺省为 id。嵌套路径如 data.clips.0.asset_id 可精确绑定，不执行表达式或任意 JavaScript/Python。节点不能读取非上游节点的产物。

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/v1/execution/capabilities` | 准确可执行范围与参数 Schema |
| POST | `/api/v1/execution/start` | `{workflow_id,expected_revision,idempotency_key}`，冻结并执行 |
| POST | `/api/v1/execution/{run_id}/resume` | `{expected_revision}`，保留已完成节点继续 |
| GET | `/api/v1/execution/{run_id}/recovery` | 未结尝试的真实已提交候选 |
| POST | `/api/v1/execution/{run_id}/reconcile` | `{expected_revision,node_id,outputs,reason}`，核对同节点真实产物 |
| POST | `/api/v1/commands` | SuspendTask / RequestCancel 等已有完整命令信封 |

启动请求幂等重发不会重跑；恢复是单独操作。完成或取消的运行不能重新启动，可创建引用已有成果的新任务。未知副作用优先核对；没有实际产物的节点不能凭一段说明直接改成完成。

POST /api/v1/execution/{run_id}/revise-node 支持受版本保护的未开始节点修订，已完成的输出保留。配方资格与运行时指纹见 [采集与工程依据](production-integrations.md)。

## 本地执行限制

运行由当前 API 工作进程同步推进；检查点、租约、输出和控制请求持久化。批量制作另有可恢复的单机持久工作队列，见[批次执行](batch-production.md)。通用图的独立运行与批次领取有各自的恢复机制，跨机器集群仍待验证。并发渲染受媒体服务自身资源限制；同一个 Run 只允许一个有效租约。重启后发现未结节点会进入待核对，保留真实证据。用户取消不会立即杀死当前 FFmpeg 进程，界面必须显示停止中，直到当前本地操作确认到达安全检查点。

已验证：真实变换→提音、幂等重发、独立分支、步数限制、暂停→恢复不重做、取消保留已完成节点、源版本变化阻断、凭据撤销阻断、过期 fencing 拒绝写入、已提交孤儿产物核对后不重复执行。具体运行日志以每轮回归证据为准。
