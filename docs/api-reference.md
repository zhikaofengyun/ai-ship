# 前后端接口规范

版本：0.1.0；源架构 schema 6.0。机器可读合同为 `contracts/openapi.json`，服务启动后同一合同从 `/openapi.json` 读取。

## 共同规则

所有业务接口以 `/api/v1` 开头。同源浏览器与 API 使用同一事实账本，JSON 成功返回 `{data, trace_id}`，失败返回 `{error:{code,message,details},trace_id}`；二进制媒体和 SSE 使用合同声明的专用响应。

服务端决定 tenant_id、actor_id、role。local 模式仅接受回环地址；team 模式使用 Bearer 身份，浏览器媒体和 SSE 使用 POST /session 换取的 HttpOnly 短期 Cookie。会话撤销会重新检查服务端密钥映射。

修改既有事实必须带 expected_revision；409 后重新读取差异，不自动覆盖。Commands、模型调用和工作流启动各有幂等键；请求超时不等于执行失败，先查询原调用/运行。文件分块按已确认 offset 续传。所有引用必须固定版本且在同租户/明确项目内。

实体草稿形状见64份JSON Schema；Pydantic声明操作请求，媒体配方还有实际素材探测与参数组合验证。源码未明确的嵌套载荷仍有扩展区，不能仅凭字段存在就视为供应商已接通。

## 状态码

| 状态 | 处理 |
|---|---|
| 200 / 201 | 读取或事实已提交；查看 data 中领域状态，不推断业务已完成 |
| 401 / 403 | 身份、角色、租户、当前授权或同源条件不满足 |
| 404 | 版本或实际字节不存在 |
| 409 | CAS、锁、上传offset、幂等或结果核对冲突 |
| 413 | 请求体或媒体资源超过上限 |
| 422 | 数据类型、语义、必需输入或路线验收不满足 |
| 503 | 依赖、能力或存储暂不可用 |

## 全部已实现 HTTP 操作

| 方法 | 路径 | 操作 | 请求体 Schema |
|---|---|---|---|
| POST | `/api/v1/session` | Browser Session | `—` |
| DELETE | `/api/v1/session` | Delete Browser Session | `—` |
| GET | `/api/v1/health` | Health | `—` |
| GET | `/api/v1/catalog` | Get Catalog | `—` |
| GET | `/api/v1/catalog/workspace` | Get Workspace Catalog | `—` |
| GET | `/api/v1/contracts/entities/{kind}` | Entity Schema | `—` |
| GET | `/api/v1/projects` | Projects | `—` |
| POST | `/api/v1/projects` | Create Project | `ProjectCreate` |
| GET | `/api/v1/records` | Records | `—` |
| POST | `/api/v1/records` | Create Record | `RecordCreate` |
| GET | `/api/v1/records/{record_id}` | Get Record | `—` |
| PATCH | `/api/v1/records/{record_id}` | Patch Record | `RecordUpdate` |
| GET | `/api/v1/records/{record_id}/history` | Record History | `—` |
| GET | `/api/v1/events` | Events | `—` |
| GET | `/api/v1/events/stream` | Event Stream | `—` |
| GET | `/api/v1/runs` | Runs | `—` |
| POST | `/api/v1/commands` | Run Command | `CommandEnvelope` |
| POST | `/api/v1/workflows/validate` | Compile Workflow | `object / runtime guards` |
| POST | `/api/v1/parameters/resolve` | Resolve | `object / runtime guards` |
| POST | `/api/v1/dependencies/impact` | Impact | `ImpactBody` |
| GET | `/api/v1/machines/{machine_id}` | Machine | `—` |
| POST | `/api/v1/machines/{machine_id}/validate` | Machine Transition | `object / runtime guards` |
| GET | `/api/v1/summary` | Summary | `—` |
| GET | `/api/v1/media/{asset_id}/frame` | Frame Endpoint | `—` |
| POST | `/api/v1/media/{asset_id}/detect-shots` | Detect Shots Endpoint | `ShotDetectionInput` |
| POST | `/api/v1/media/{asset_id}/analyze-audio` | Analyze Audio Endpoint | `—` |
| DELETE | `/api/v1/media/uploads/{entry_id}` | Cancel Upload Endpoint | `—` |
| GET | `/api/v1/media/uploads/{entry_id}` | Upload Status Endpoint | `—` |
| PUT | `/api/v1/media/uploads/{entry_id}` | Upload Chunk Endpoint | `binary` |
| POST | `/api/v1/media/uploads` | Start Upload Endpoint | `MediaUploadStart` |
| POST | `/api/v1/media/uploads/{entry_id}/complete` | Finish Upload Endpoint | `—` |
| GET | `/api/v1/media/capabilities` | Media Capabilities | `—` |
| POST | `/api/v1/media/import` | Import Endpoint | `binary` |
| POST | `/api/v1/media/render` | Render Endpoint | `MediaRenderInput` |
| GET | `/api/v1/media/artifacts/{artifact_id}/file` | Artifact File Endpoint | `—` |
| GET | `/api/v1/media/artifacts/{artifact_id}/package` | Package Endpoint | `—` |
| GET | `/api/v1/media/{asset_id}/file` | Media File Endpoint | `—` |
| POST | `/api/v1/media/{asset_id}/transform` | Transform Endpoint | `MediaTransformInput` |
| GET | `/api/v1/media/{asset_id}/waveform` | Waveform Endpoint | `—` |
| POST | `/api/v1/media/{asset_id}/extract-audio` | Extract Audio Endpoint | `AudioExtractInput` |
| POST | `/api/v1/media/render-jobs/{job_id}/recover` | Recover | `Recovery` |
| POST | `/api/v1/media/captions/parse` | Parse Captions | `ParseCaptions` |
| POST | `/api/v1/media/captions/export` | Export Captions | `ExportCaptions` |
| POST | `/api/v1/production/selection/brief` | Approve Brief | `ReviewBrief` |
| POST | `/api/v1/production/selection/shot` | Select Shot | `SelectShot` |
| GET | `/api/v1/production/assets/admission` | Read Admission | `—` |
| POST | `/api/v1/production/assets/admission` | Decide | `Admission` |
| POST | `/api/v1/production/assets/retention-reconcile` | Reconcile | `SourceCommand` |
| GET | `/api/v1/production/assets/retention-reconcile/{audit_id}` | Read Targets | `—` |
| POST | `/api/v1/production/assets/cooldown` | Cooldown | `Cooldown` |
| POST | `/api/v1/production/assets/post-mapping` | Verify Post | `VerifyPost` |
| POST | `/api/v1/production/assets/processing-evidence` | Processing Evidence | `ProcessingEvidence` |
| POST | `/api/v1/production/assets/exploration` | Exploration | `Exploration` |
| POST | `/api/v1/production/assets/tier` | Change Tier | `TierChange` |
| POST | `/api/v1/production/asset-families/compare` | Compare | `Compare` |
| POST | `/api/v1/production/asset-families/group` | Group | `Group` |
| POST | `/api/v1/production/creative/verify-trend` | Verify Trend | `TrendReview` |
| POST | `/api/v1/production/creative/match` | Match Topic | `TopicMatch` |
| POST | `/api/v1/production/creative/directions` | Propose Directions | `Directions` |
| POST | `/api/v1/production/creative/preflight` | Creative Preflight | `Preflight` |
| POST | `/api/v1/production/trends/sync` | Sync | `FeedSync` |
| GET | `/api/v1/production/tiktok/accounts/{account_id}/connection` | Show Connection | `—` |
| POST | `/api/v1/production/tiktok/accounts/{account_id}/connection` | Change Connection | `ConnectionAction` |
| POST | `/api/v1/production/tiktok/metrics` | Sync Metrics | `SyncMetrics` |
| POST | `/api/v1/production/acquisition/changes` | Amend Sources | `Manifest` |
| GET | `/api/v1/production/source-media/settings` | Read Settings | `—` |
| POST | `/api/v1/production/source-media/settings` | Configure | `SourceSettings` |
| GET | `/api/v1/production/source-media/search` | Search | `—` |
| POST | `/api/v1/production/source-media/import` | Acquire | `Acquisition` |
| POST | `/api/v1/production/music/markers` | Save Markers | `ManualRhythm` |
| GET | `/api/v1/providers/availability` | Availability | `—` |
| POST | `/api/v1/providers/chat` | Chat Route | `ChatInput` |
| POST | `/api/v1/providers/team` | Run Team | `TeamInput` |
| POST | `/api/v1/production/trends` | Import Trend | `TrendInput` |
| POST | `/api/v1/production/products/import` | Import Products | `ProductImport` |
| POST | `/api/v1/production/tags` | Add Tag | `TagInput` |
| GET | `/api/v1/production/search` | Search Assets | `—` |
| POST | `/api/v1/production/usage/reserve` | Reserve Usage | `ReservationInput` |
| POST | `/api/v1/production/usage/{usage_id}/release` | Release Usage | `ReleaseInput` |
| POST | `/api/v1/production/assets/score` | Score Asset | `ScoreInput` |
| POST | `/api/v1/production/voice/align` | Align Voice | `AlignmentInput` |
| POST | `/api/v1/production/time-map/trace` | Trace Time | `TraceInput` |
| POST | `/api/v1/production/approvals` | Approve | `ApprovalInput` |
| POST | `/api/v1/production/publications` | Create Publication | `PublicationInput` |
| GET | `/api/v1/production/publications/{publication_id}/preflight` | Preflight | `—` |
| POST | `/api/v1/production/publications/{publication_id}/receipt` | Manual Receipt | `ReceiptInput` |
| POST | `/api/v1/production/metrics/import` | Import Metrics | `MetricImport` |
| GET | `/api/v1/production/analytics` | Analytics | `—` |
| POST | `/api/v1/production/experiments` | Create Experiment | `ExperimentInput` |
| POST | `/api/v1/production/experiments/{experiment_id}/evaluate` | Evaluate Experiment | `EvaluationInput` |
| GET | `/api/v1/production/trace/{record_id}` | Lineage | `—` |
| POST | `/api/v1/production/replay/prepare` | Prepare Replay | `ReplayInput` |
| POST | `/api/v1/production/account-quota/settings` | Configure | `Settings` |
| GET | `/api/v1/production/account-quota/{project_id}/{account_id}` | Status | `—` |
| POST | `/api/v1/production/account-quota/act` | Action | `Action` |
| POST | `/api/v1/production/account-quota/provider-observation` | Observe | `ProviderObservation` |
| POST | `/api/v1/production/commerce/packages` | Prepare | `Prepare` |
| GET | `/api/v1/production/commerce/packages/{package_id}/file` | Download | `—` |
| POST | `/api/v1/production/commerce/bindings` | Bind | `Bind` |
| POST | `/api/v1/production/commerce/observations` | Observe | `Attachment` |
| POST | `/api/v1/production/commerce/receipts` | Receive | `Collect` |
| POST | `/api/v1/production/publication-lifecycle/{pid}/cancel` | Cancel | `Mutation` |
| POST | `/api/v1/production/publication-lifecycle/{pid}/change` | Change | `Change` |
| POST | `/api/v1/production/publication-lifecycle/{pid}/observe` | Observe | `app__publication_lifecycle__Observation` |
| GET | `/api/v1/production/score-policies/{project_id}` | Read Pointer | `—` |
| POST | `/api/v1/production/score-policies/propose` | Propose | `Proposal` |
| POST | `/api/v1/production/score-policies/optimize` | Fit Candidate | `FitCandidate` |
| POST | `/api/v1/production/score-policies/evaluate` | Evaluate | `Evaluation` |
| POST | `/api/v1/production/score-policies/approve` | Approve | `ApprovalRequest` |
| POST | `/api/v1/production/score-policies/activate` | Activate | `Activate` |
| POST | `/api/v1/production/score-policies/rollback` | Rollback | `Rollback` |
| POST | `/api/v1/production/comment-feedback/observe` | Observe | `app__comment_feedback__Observation` |
| GET | `/api/v1/production/comment-feedback/{project_id}/{comment_id}/body` | Get Body | `—` |
| POST | `/api/v1/production/comment-feedback/classify` | Classify | `Classification` |
| GET | `/api/v1/production/comment-feedback/{project_id}/summary` | Summary | `—` |
| POST | `/api/v1/production/post-reviews/retention-mapping` | Map Retention | `RetentionMapping` |
| GET | `/api/v1/production/post-reviews/placement-performance` | Performance | `—` |
| POST | `/api/v1/production/post-reviews/archive` | Import Archive | `Archive` |
| POST | `/api/v1/production/post-reviews/compare` | Compare | `Comparison` |
| GET | `/api/v1/production/auto-edits` | List Edits | `—` |
| POST | `/api/v1/production/auto-edits` | Start | `AutoEdit` |
| GET | `/api/v1/production/auto-edits/{run_id}` | Get Edit | `—` |
| POST | `/api/v1/production/auto-edits/{run_id}/approve` | Approve Edit | `AcceptEdit` |
| POST | `/api/v1/production/auto-edits/{run_id}/render` | Render Edit | `RenderEdit` |
| POST | `/api/v1/production/auto-edits/{run_id}/replace-shot` | Replace Shot | `ReplaceShot` |
| GET | `/api/v1/production/batches/observability/health` | Health | `—` |
| GET | `/api/v1/production/batches/observability/compare` | Compare | `—` |
| POST | `/api/v1/production/batches/contracts` | Create Contract | `ContractInput` |
| GET | `/api/v1/production/batches/contracts` | List Contracts | `—` |
| POST | `/api/v1/production/batches/market-briefs` | Create | `MarketBrief` |
| GET | `/api/v1/production/batches/market-briefs` | List Briefs | `—` |
| POST | `/api/v1/production/batches` | Enqueue | `Recipe` |
| GET | `/api/v1/production/batches` | List Batches | `—` |
| GET | `/api/v1/production/batches/{bid}` | Get Batch | `—` |
| POST | `/api/v1/production/batches/{bid}/{action}` | Control | `—` |
| GET | `/api/v1/production/generation/profiles` | Available | `—` |
| GET | `/api/v1/production/generation` | Jobs | `—` |
| POST | `/api/v1/production/generation` | Generate | `Generate` |
| POST | `/api/v1/production/generation/{id}/refresh` | Refresh | `—` |
| POST | `/api/v1/production/generation/{id}/cancel` | Cancel | `—` |
| POST | `/api/v1/production/generation/{id}/hold` | Hold Still | `Hold` |
| GET | `/api/v1/execution/capabilities` | Get Capabilities | `—` |
| POST | `/api/v1/execution/start` | Start Endpoint | `StartInput` |
| POST | `/api/v1/execution/{run_id}/resume` | Resume Endpoint | `ResumeInput` |
| GET | `/api/v1/execution/{run_id}/recovery` | Recovery | `—` |
| POST | `/api/v1/execution/{run_id}/reconcile` | Reconcile | `ReconcileInput` |
| GET | `/api/v1/harness/runtime` | Catalog | `—` |
| POST | `/api/v1/harness/qualify` | Qualify | `Qualify` |
| POST | `/api/v1/harness/dispatch` | Dispatch | `Dispatch` |
| POST | `/api/v1/execution/edit-preferences` | Write Preference | `EditPreference` |
| GET | `/api/v1/execution/edit-preferences` | Read Preferences | `—` |
| POST | `/api/v1/execution/{run_id}/revise-node` | Revise Node | `NodeChange` |
| POST | `/api/v1/execution/context-packs` | Pin Context | `PackInput` |
| GET | `/api/v1/execution/{run_id}/context-pack` | Read Context | `—` |
| POST | `/api/v1/projects/{project_id}/timeline-exchange/export` | Export Timeline | `ExportInput` |
| POST | `/api/v1/projects/{project_id}/timeline-exchange/inspect` | Inspect Timeline | `InspectInput` |
| POST | `/api/v1/projects/{project_id}/timeline-exchange/import` | Import Timeline | `ImportInput` |
| GET | `/api/v1/production/asset-index/history` | History | `—` |
| POST | `/api/v1/production/asset-index/build` | Build | `Build` |
| POST | `/api/v1/production/asset-index/switch` | Switch | `Switch` |
| POST | `/api/v1/production/asset-index/embed` | Embed | `Embed` |
| POST | `/api/v1/production/asset-index/query` | Query | `Query` |
| POST | `/api/v1/production/voice-studio/maps` | Make Map | `MapInput` |
| POST | `/api/v1/production/voice-studio/observations` | Observe | `app__voice_studio__Observation` |
| POST | `/api/v1/production/voice-studio/plans` | Make Plan | `PlanInput` |
| POST | `/api/v1/production/voice-studio/adopt` | Adopt | `Adopt` |
| POST | `/api/v1/production/voice-studio/repair` | Repair | `Repair` |
| POST | `/api/v1/production/voice-studio/rhythm` | Resolve Rhythm | `RhythmInput` |
| POST | `/api/v1/production/narration/source-reviews` | Review Source | `SourceReview` |
| POST | `/api/v1/production/narration/synthesize` | Synthesize | `Synthesis` |
| POST | `/api/v1/production/narration/readings` | Save Reading | `Reading` |
| GET | `/api/v1/production/variants/{run_id}` | List Families | `—` |
| POST | `/api/v1/production/variants/{run_id}` | Generate | `FamilySpec` |
| POST | `/api/v1/production/variants/{run_id}/{family_id}/select` | Choose | `SelectVariant` |
| POST | `/api/v1/production/soundtrack/rights` | Review Rights | `RightsReview` |
| POST | `/api/v1/production/soundtrack/mixes` | Create Mix | `MixRequest` |
| POST | `/api/v1/production/soundtrack/adopt` | Adopt Mix | `AdoptMix` |
| POST | `/api/v1/production/soundtrack/inspect-output` | Inspect Output | `InspectOutput` |
| POST | `/api/v1/production/soundtrack/remove-music` | Remove Music | `RemoveMusic` |
| POST | `/api/v1/production/soundtrack/listen-review` | Listen Review | `ListeningReview` |

共 180 个 HTTP 操作。完整 path/query/header 条件、枚举、范围、错误与二进制声明在 OpenAPI 中。

## 工作流和组件扩展

`GET /execution/capabilities` 返回当前真实路线处理器与对应 operation_args Schema。节点直接输入使用 `{record_id,version,value}`，上游输入使用 `{node_id,output,index?,value}`，value 是 id/ref/data/version。上游绑定必须存在于真实DAG祖先，禁止读取仅在画布上相邻的节点。

新增实际执行器必须同时实现：能力准入、冻结输入、原文质量门槛、预算和时限、持久检查点、fencing写入、真实产物/错误/未知结果、事件与回归。静态注册到目录并不会自动成为可执行能力。

参考：`docs/data-contracts.md`、`docs/media-capabilities.md`、`docs/architecture.md`、`docs/implementation-matrix.md`。
