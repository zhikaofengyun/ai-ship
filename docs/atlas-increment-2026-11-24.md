# 2026-11-24 原文质量门槛增量核验

本次选取 16 条与新实现直接相关的原文门槛，逐条回查 14 个具名回归用例。用例使用隔离的项目与本地 SQLite。涉及音频或视频输出的用例调用实际 FFmpeg。平台帐号、帖子和商业授权均为测试数据。下表只更新所列门槛的证据，整项功能的生产验收单列。 [实际运行记录](../evidence/ai-production/atlas-increment-2026-11-24.json) · [前一版 1,678 条快照](quality-gate-evidence.md)

“本地路线直接核验”表示该条明文条件在所列路线及测试范围内有直接断言。“部分核验”列明仍缺的分支。原评级取自前一版 JSON 快照。

| 原文编号与条件 | 原评级 | 本轮判定 | 实际行为与边界 | 证据 |
| --- | --- | --- | --- | --- |
| E05/Q08　遮罩与跟踪明确坐标阶段，原片旋转和裁切变换只应用一次 | 尚无直接证据 | 部分核验 | 在已旋转且使用 cover 的实际输出上采样像素，确认矩形遮罩仅按适配后的画面坐标应用一次。跟踪路径、动态遮罩和其他坐标阶段仍待核验。 | [MediaIntegrationTests.test_rect_mask_after_rotation_and_cover_is_applied_once_to_decoded_pixels](../tests/test_media.py) |
| T02/Q03　不确定性保留到创意简报 | 尚无直接证据 | 本地路线直接核验 | 同源转载只有一组支持证据。默认拒绝直接采用；显式按假设使用后，风险随简报保存。过期后即使选择假设也会拒绝。 | [CreativeTests.test_reposted_sources_conflicts_expiry_and_hypothesis_gate](../tests/test_creative.py) |
| T05/Q01　诊断定位到段落/素材族 | 尚无直接证据 | 部分核验 | 两个导演候选有一个重复镜头族，程序定位到第二个镜头并按 50% 阈值标记。自动从历史成片识别叙事段落仍待验证。 | [CreativeTests.test_director_proposals_comparison_and_approval_share_pinned_facts](../tests/test_creative.py) |
| A06/Q02　重复事件不重复破坏 | 尚无直接证据 | 本地路线直接核验 | 对同一来源撤权事件再次提交，取得相同回执；其他待发意图及既有 Post 的版本保持原值。 | [SourceChangeTests.test_scoped_manifest_separates_origin_removal_rights_and_historical_posts](../tests/test_source_changes.py) |
| A06/Q04　删除和撤权不是普通缺失 | 尚无直接证据 | 本地路线直接核验 | 未找到只留下观察记录，确认原出处移除后已存媒体仍可使用；核实权利撤回后，该来源关联的待发意图在交接前阻断。 | [SourceChangeTests.test_scoped_manifest_separates_origin_removal_rights_and_historical_posts](../tests/test_source_changes.py) |
| A06/Q05　传播到已排程作品 | 尚无直接证据 | 部分核验 | 版本变动使仍依赖旧来源的待发意图出现 SOURCE_CONTENT_CHANGED。此例使用本地发布意图，未调用任何平台后台排程。 | [SourceChangeTests.test_revision_review_dedup_late_conflict_and_per_entry_failure](../tests/test_source_changes.py) |
| V07/Q01　每个实际偏移符合时钟与范围 | 尚无直接证据 | 部分核验 | 在真实音频采样时钟上以已标拍点解算切口，保留发音区间与人工锁。多语种自动对齐和其他节奏策略尚待分别核验。 | [VoiceStudioTests.test_rhythm_preserves_speech_and_lock_and_uses_a_real_marked_audio_clock](../tests/test_voice_studio.py) |
| V03/Q01　可播放的真实音频与hash存在 | 部分核验 | 本地路线直接核验 | 从应用下载本地 Flite 生成的 WAV，检查文件 SHA-256、多个未静音样本值和解码帧数，回查冻结脚本与 ToolCall。 | [NarrationTests.test_pcm_dedup_tempo_current_voice_rights_and_unknown_language](../tests/test_narration.py) |
| V03/Q03　不能用预计时长替代实际样本数 | 部分核验 | 本地路线直接核验 | 记录的采样数和采样率与重新打开的 WAV 一致；提高语速后成品样本数实际减少。 | [NarrationTests.test_pcm_dedup_tempo_current_voice_rights_and_unknown_language](../tests/test_narration.py) |
| V02/Q01　显示金额和口播归一化值可互相核对 | 尚无直接证据 | 部分核验 | USD 79.90 的展示文本与英语口播归一化值分别保存，冻结数量和商品记录，实际合成使用新稿。未在全部币种、译法和商品语料上复核。 | [NarrationTests.test_corrected_price_reading_preserves_currency_numbers_and_uses_actual_tts](../tests/test_narration.py) |
| S04/Q05　聚类变更触发频率重算 | 尚无直接证据 | 部分核验 | 人工素材族建立和退役后重新计算有范围的使用压力。尚无大规模语义聚类器和分布式异步重算证据。 | [AssetFamilyTests.test_decoded_frames_near_review_and_retirement_recompute_family_pressure](../tests/test_asset_families.py) |
| X06/Q01　生效以事务成功为准，批准或排期不算生效 | 尚无直接证据 | 本地路线直接核验 | 项目软评分在批准后仍用原权重；提交切换后，后续实际评分开始使用新版本。重复激活返回同一操作结果。 | [ScorePolicyTests.test_freeze_activate_consume_replay_and_rollback](../tests/test_score_policies.py) |
| U06/Q03　复苏不能跳过当前有效性 | 尚无直接证据 | 本地路线直接核验 | 本地冷存恢复检查真实字节与当前使用权；校验失败或撤权时不恢复可用性。 | [AssetLifecycleTests.test_cold_restore_preserves_refs_alias_and_index_and_rechecks_bytes_rights](../tests/test_asset_lifecycle.py) |
| M04/Q02　分类能回到实际正文版本 | 尚无直接证据 | 本地路线直接核验 | 人工分类绑定摘录的具体正文版本，正文修订后旧分类不冒用为新版本分类。 | [CommentFeedbackTests.test_independent_body_and_classification_revisions](../tests/test_comment_feedback.py) |
| M09/Q06　曲线与最终媒体一致才映射到镜头；只具整片聚合时禁止声称具体镜头直接导致销量 | 尚无直接证据 | 部分核验 | 两帖同口径聚合指标允许描述性比较，返回的片段归因标明不可用。迟到数据改变新报告但不改旧报告；有真实时间曲线时的最终媒体映射待验。 | [PostReviewTests.test_late_backfill_pins_new_observation_and_keeps_old_report](../tests/test_post_reviews.py) |
| B03/Q01　实际音乐身份与许可证据一致 | 尚无直接证据 | 部分核验 | 合成库存音频绑定用途、市场和期限；范围不符被拒，平台声音引用不能冒充本地许可或混进成片。生产平台实际附曲仍未核实。 | [SoundtrackTests.test_licensed_stock_and_scope_expiry_no_music_and_platform_handoff](../tests/test_soundtrack.py) |
