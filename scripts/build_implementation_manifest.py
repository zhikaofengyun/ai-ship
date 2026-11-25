"""Maintain truthful per-feature implementation traceability without score inflation."""

from pathlib import Path
import ast
from collections import Counter
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
atlas = json.loads((ROOT / "contracts" / "atlas.json").read_text())

SOURCE_SHA256 = "65a0e6e7f19b0e087be4ff0adcd1166c7073211caa5988f739df05b47971dee4"
STATUS_LABELS = {
    "local_business_subset": "真实局部路径",
    "generic_framework_only": "通用基础/配置",
    "external_adapter_unverified": "适配器未实联",
    "contract_and_configuration_only": "仅合同配置",
}
# Explicit audited v6.0 sets. A new source ID never defaults to implemented.
STATUS_IDS = {
    "local_business_subset": frozenset("""
        A01 A02 A03 A04 A05 A06 A07 B01 B02 B03 B04 B05 B06 C01 C02 C03 C04 C05 C06 C07 C08 C09
        C10 C11 D01 D02 D03 D04 D05 D06 D07 D08 E01 E02 E03 E04 E05 E06 E07 E08 E09 F01 F02 F03
        F04 F05 F06 F07 F08 F09 G01 G02 G03 G04 G05 G06 H01 H02 H03 H04 H05 H06 H07 H08 H09 H10
        H11 H12 I01 I02 I03 I04 I05 I06 I07 J02 J03 J04 J05 J06 K01 K02 K03 K04 K05 K06 L01 L02
        L03 L04 L05 L06 L07 M01 M02 M03 M04 M05 M06 M07 M08 M09 N01 N02 N03 N04 N05 N06 N07 O01
        O02 O03 O04 O05 O06 O07 P01 P02 P03 P04 P05 P06 S01 S02 S03 S04 S05 S06 T01 T02 T03 T04 T05
        T06 U01 U02 U03 U04 U05 U06 U07 V01 V02 V03 V04 V05 V06 V07 V08 V09 W01 W02 W03 W04 W05
        W06 W07 W08 W09 X01 X02 X03 X04 X05 X06 X07 X08 Y01 Y02 Y03 Y04 Y05 Y06 Y07 Y08 Y09
    """.split()),
    "generic_framework_only": frozenset(),
    "external_adapter_unverified": frozenset("""
        J01 J07
    """.split()),
    "contract_and_configuration_only": frozenset(),
}
EXPECTED_COUNTS = dict(zip(STATUS_LABELS, (176, 0, 2, 0)))
EXPECTED_WORKFLOW_ROUTES = frozenset("""
    G02-R2 G03-R2 E01-R3 I03-R2 I04-R1 I07-R2 L01-R1 L07-R1 V01-R3 V03-R2 E01-R1 E08-R1 Y03-R1 Y06-R2
""".split())

# Mappings indicate an implemented path within a feature, never every route/gate.
groups = [
    (
        "P01 P03 P04 P06",
        ["app/api.py", "app/commands.py"],
        "tests/test_core.py",
        "项目创建、配置、版本与基线冻结",
    ),
    (
        "P02",
        ["app/production.py"],
        "tests/test_production.py",
        "带来源的商品事实CSV导入、精确金额和异常行隔离",
    ),
    (
        "P05 W02 W09 C02",
        ["app/workflow.py", "web/workflow.js"],
        "tests/test_core.py",
        "类型端口DAG编译、边界检查与画布配置",
    ),
    ("T01 T02", ["app/production.py"], "tests/test_production.py", "人工来源证据与观测/到期窗口"),
    (
        "A01 A05 A07 I01 I02",
        ["app/media.py"],
        "tests/test_media.py",
        "真实媒体导入、去重、探测、对象归档与分块续传",
    ),
    (
        "I03 I04 I07",
        ["app/media.py"],
        "tests/test_media.py",
        "明确支持的FFmpeg配方、实际参数与衍生谱系",
    ),
    (
        "I06 A04 K04",
        ["app/production.py", "app/store.py"],
        "tests/test_production.py",
        "当前撤权/到期检查与冻结版本引用；不执行远端删除",
    ),
    (
        "L01 L07 B01 B02",
        ["app/media.py"],
        "tests/test_media.py",
        "真实镜头差异候选、帧预览、音轨与起音候选；非语义或BPM保证",
    ),
    (
        "L02 L03 L05 L06",
        ["app/production.py"],
        "tests/test_production.py",
        "人工片段标注、真实区间与版本引用",
    ),
    (
        "S01 S02 S03 S06",
        ["app/production.py", "app/media.py"],
        "tests/test_production.py",
        "结构化/全文召回、精确字节去重以及命中证据；可选多语言文本索引见 S02",
    ),
    (
        "U01 U02 U03 U07",
        ["app/production.py", "app/media.py"],
        "tests/test_production.py",
        "素材区间使用、并发预留、缺失友好的透明评分",
    ),
    (
        "V01 V03 V04 V05 V08",
        ["app/production.py", "app/commands.py"],
        "tests/test_production.py",
        "人工脚本/导入音轨/手动时间锚和版本引用；可选本地中英文 ASR 见 L04/V04；英文口播可用已核验的本地 Flite 声音合成",
    ),
    ("B05 B06", ["app/media.py"], "tests/test_media.py", "音轨混合与响度处理、全片实际解码检查"),
    (
        "N01 N03 N05 N06",
        ["app/commands.py", "app/media.py", "app/production.py"],
        "tests/test_media.py",
        "手动分镜记录、时间范围验证、画幅及镜头预留",
    ),
    (
        "E01 E02 E03 E04 E05 E07 E08",
        ["web/editor.js", "app/media.py", "app/commands.py"],
        "tests/test_media.py",
        "可编辑时间线、真实预览、剪切字幕有限关键帧、版本与锁版",
    ),
    (
        "E06 E09 C04 C05 C10",
        ["app/commands.py", "app/workflow.py", "app/store.py"],
        "tests/test_core.py",
        "CAS变更、人工锁和真实字段/区间依赖影响；AI编辑提案未自动执行",
    ),
    (
        "Y01 Y02 Y03 Y05 Y06 Y07 Y08 Y09",
        ["app/media.py"],
        "tests/test_media.py",
        "单机有界FFmpeg渲染、实测QC、尺寸派生、清单与交付包；不含全部外部后端",
    ),
    (
        "J03 J04 J06 F01 F02 F04 F06 F08",
        ["app/production.py", "app/auth.py"],
        "tests/test_production.py",
        "冻结发布意图、DST明确排期、精确人工确认与人工回执；不自动外发",
    ),
    (
        "M01 M02 M03 M05 M06 M07 X01",
        ["app/production.py"],
        "tests/test_production.py",
        "真实指标人工导入、口径分组、零/缺失/未成熟区分与重复快照核对",
    ),
    (
        "M08 D03 D08",
        ["app/production.py", "app/media.py", "app/store.py"],
        "tests/test_production.py",
        "不可变事实引用、半开区间反查、对象hash与谱系",
    ),
    (
        "X02 X04 X05",
        ["app/production.py"],
        "tests/test_production.py",
        "冻结有界实验、稳定分配与描述性比较；不伪称因果提升",
    ),
    (
        "C01 C07 C08 C09 C11 H03 H12",
        ["app/providers.py", "app/commands.py"],
        "tests/test_production.py",
        "模型/角色/任务配置，带预算与独立组的串行异构提案；供应商需凭据联调",
    ),
    (
        "C03 C06 H05 H06 H07 H11 O02 O03 O06 K06",
        ["app/commands.py", "app/providers.py", "app/media.py"],
        "tests/test_core.py",
        "租约/fencing、检查点、超时未知结果、预算和取消准入",
    ),
    (
        "H01 H02 H08 H09 O07 W06",
        ["app/api.py", "app/schemas.py", "app/catalog.py"],
        "tests/test_core.py",
        "版本化合同、JSON Schema/能力声明及扩展记录；未接所有候选工具",
    ),
    (
        "H10",
        ["app/production.py"],
        "tests/test_production.py",
        "隔离重放清单准备及依赖核对；未自动执行全部历史重放",
    ),
    (
        "D01 D02 D05 O01",
        ["app/store.py", "app/api.py"],
        "tests/test_core.py",
        "不可变事实、事务事件/消费水位/SSE、投影检索和审计",
    ),
    (
        "D07",
        ["scripts/backup.py", "scripts/restore.py"],
        "tests/test_maintenance.py",
        "SQLite一致性快照、真实对象校验与受控恢复",
    ),
    (
        "K01 K02 K03 K05",
        ["app/auth.py", "app/providers.py", "app/media.py"],
        "tests/test_core.py",
        "服务端租户/角色、密钥引用、不执行不可信表达式、对象版本确认",
    ),
    (
        "O04 O05 X03 X08",
        ["app/api.py", "tests/test_core.py", "tests/test_media.py", "tests/test_production.py"],
        "tests/test_core.py",
        "运行状态与冻结回归测试；生产负载告警和策略晋升仍未验证",
    ),
    (
        "W01 W03 W04 W05 W07 W08",
        ["web/app.js", "web/ui.js", "web/styles.css", "web/editor.js"],
        "scripts/browser_regression.py",
        "结构化工作台、素材联动、审阅、导航、外观和可访问性设置",
    ),
]
mapping = {}
for ids, code, tests, description in groups:
    for fid in ids.split():
        mapping[fid] = {"code": code, "test_definition": tests, "implemented_path": description}

EXECUTION_FEATURES = frozenset("""
    P05 W02 W09 C02 C03 C05 C06 C10 H01 H05 H06 H07 H11 O03 O06 K06
    I03 I04 I07 L01 L07 V01 E01 E08 Y03 Y06
""".split())
BUDGET_FEATURES = frozenset("""
    P04 C01 C03 C06 C07 C09 C11 H03 H05 H07 H12 O02 O03 O06 K06
""".split())

# These endpoints are a declared existing subset, not all source routes.
ENDPOINT_GROUPS = [
    (
        "F03",
        [
            "POST /api/v1/production/commerce/packages",
            "GET /api/v1/production/commerce/packages/{package_id}/file",
            "POST /api/v1/production/commerce/bindings",
            "POST /api/v1/production/commerce/receipts",
            "POST /api/v1/production/commerce/observations",
        ],
    ),
    (
        "J05",
        [
            "POST /api/v1/production/account-quota/settings",
            "GET /api/v1/production/account-quota/{project_id}/{account_id}",
            "POST /api/v1/production/account-quota/act",
            "POST /api/v1/production/account-quota/provider-observation",
        ],
    ),
    ("Y04", ["POST /api/v1/media/render-jobs/{job_id}/recover"]),
    (
        "H04",
        ["POST /api/v1/execution/context-packs", "GET /api/v1/execution/{run_id}/context-pack"],
    ),
    (
        "M09",
        [
            "POST /api/v1/production/post-reviews/archive",
            "POST /api/v1/production/post-reviews/compare",
        ],
    ),
    ("V02", ["POST /api/v1/production/narration/readings"]),
    ("A06", ["POST /api/v1/production/acquisition/changes"]),
    ("S03", ["POST /api/v1/production/asset-families/compare"]),
    ("S04", ["POST /api/v1/production/asset-families/group"]),
    (
        "M04",
        [
            "POST /api/v1/production/comment-feedback/observe",
            "POST /api/v1/production/comment-feedback/classify",
            "GET /api/v1/production/comment-feedback/{project_id}/summary",
            "GET /api/v1/production/comment-feedback/{project_id}/{comment_id}/body",
        ],
    ),
    (
        "V03",
        [
            "POST /api/v1/production/narration/source-reviews",
            "POST /api/v1/production/narration/synthesize",
        ],
    ),
    ("T02", ["POST /api/v1/production/creative/verify-trend"]),
    ("U04", ["POST /api/v1/production/assets/post-mapping"]),
    ("X02", ["POST /api/v1/production/score-policies/propose"]),
    ("X03", ["POST /api/v1/production/score-policies/evaluate"]),
    ("T03", ["POST /api/v1/production/creative/match"]),
    ("T04", ["POST /api/v1/production/creative/directions"]),
    ("T05", ["POST /api/v1/production/creative/preflight"]),
    ("S01 S02", ["POST /api/v1/production/asset-index/query"]),
    (
        "S05 D05",
        ["POST /api/v1/production/asset-index/build", "POST /api/v1/production/asset-index/switch"],
    ),
    ("B03", ["POST /api/v1/production/soundtrack/rights"]),
    (
        "B04 B05",
        [
            "POST /api/v1/production/soundtrack/mixes",
            "POST /api/v1/production/soundtrack/adopt",
            "POST /api/v1/production/soundtrack/remove-music",
        ],
    ),
    (
        "B06",
        [
            "POST /api/v1/production/soundtrack/inspect-output",
            "POST /api/v1/production/soundtrack/listen-review",
        ],
    ),
    (
        "V06 V09",
        [
            "POST /api/v1/production/voice-studio/maps",
            "POST /api/v1/production/voice-studio/observations",
            "POST /api/v1/production/voice-studio/plans",
            "POST /api/v1/production/voice-studio/adopt",
        ],
    ),
    ("V08", ["POST /api/v1/production/voice-studio/repair"]),
    ("V07", ["POST /api/v1/production/voice-studio/rhythm"]),
    (
        "N04",
        [
            "POST /api/v1/production/variants/{run_id}",
            "POST /api/v1/production/variants/{run_id}/{family_id}/select",
        ],
    ),
    ("U04", ["POST /api/v1/production/assets/cooldown"]),
    (
        "U05",
        [
            "POST /api/v1/production/assets/exploration",
            "POST /api/v1/production/assets/processing-evidence",
        ],
    ),
    ("U06", ["POST /api/v1/production/assets/tier"]),
    (
        "X06",
        [
            "POST /api/v1/production/score-policies/propose",
            "POST /api/v1/production/score-policies/evaluate",
            "POST /api/v1/production/score-policies/approve",
            "POST /api/v1/production/score-policies/activate",
        ],
    ),
    ("X07", ["POST /api/v1/production/score-policies/rollback"]),
    ("B02", ["POST /api/v1/production/music/markers"]),
    ("T06", ["POST /api/v1/production/selection/brief"]),
    ("N02", ["POST /api/v1/production/selection/shot"]),
    (
        "E01 Y09",
        [
            "POST /api/v1/projects/{project_id}/timeline-exchange/" + suffix
            for suffix in ("export", "inspect", "import")
        ],
    ),
    ("P01", ["POST /api/v1/projects"]),
    ("P02", ["POST /api/v1/production/products/import"]),
    ("T01 T02", ["POST /api/v1/production/trends"]),
    (
        "A01 A05 A07 I01 I02",
        [
            "POST /api/v1/media/import",
            "POST /api/v1/media/uploads",
            "PUT /api/v1/media/uploads/{entry_id}",
            "POST /api/v1/media/uploads/{entry_id}/complete",
        ],
    ),
    ("I03 I04 I07", ["POST /api/v1/media/{asset_id}/transform"]),
    ("L01", ["POST /api/v1/media/{asset_id}/detect-shots", "GET /api/v1/media/{asset_id}/frame"]),
    ("L02 L03 L05 L06", ["POST /api/v1/production/tags"]),
    ("L07 V03", ["POST /api/v1/media/{asset_id}/extract-audio", "POST /api/v1/media/import"]),
    ("S01 S02 S03 S06", ["GET /api/v1/production/search"]),
    (
        "U02 N06",
        [
            "POST /api/v1/production/usage/reserve",
            "POST /api/v1/production/usage/{usage_id}/release",
        ],
    ),
    ("U03 U07", ["POST /api/v1/production/assets/score"]),
    ("V04 V05", ["POST /api/v1/production/voice/align"]),
    (
        "B01 B02",
        ["POST /api/v1/media/{asset_id}/analyze-audio", "GET /api/v1/media/{asset_id}/waveform"],
    ),
    ("B05 B06 E01 E02 E04 E05 E08 Y01 Y02 Y03 Y05 Y07 Y08", ["POST /api/v1/media/render"]),
    ("E04", ["POST /api/v1/media/captions/parse", "POST /api/v1/media/captions/export"]),
    ("E03", ["GET /api/v1/media/{asset_id}/file", "GET /api/v1/media/{asset_id}/frame"]),
    (
        "E06 E09 C04 C05 C10 V08",
        ["POST /api/v1/dependencies/impact", "POST /api/v1/parameters/resolve"],
    ),
    (
        "Y06 Y09",
        [
            "GET /api/v1/media/artifacts/{artifact_id}/file",
            "GET /api/v1/media/artifacts/{artifact_id}/package",
        ],
    ),
    ("J03 J04 F01 F04", ["POST /api/v1/production/publications"]),
    (
        "J06 K05 F02",
        [
            "POST /api/v1/production/approvals",
            "GET /api/v1/production/publications/{publication_id}/preflight",
        ],
    ),
    ("F06 F08", ["POST /api/v1/production/publications/{publication_id}/receipt"]),
    ("A04 I06 K04", ["GET /api/v1/production/publications/{publication_id}/preflight"]),
    (
        "M01 M02 M03 M05 M06 M07 X01",
        ["POST /api/v1/production/metrics/import", "GET /api/v1/production/analytics"],
    ),
    (
        "X02 X04 X05",
        [
            "POST /api/v1/production/experiments",
            "POST /api/v1/production/experiments/{experiment_id}/evaluate",
        ],
    ),
    (
        "M08 D03 D08",
        ["GET /api/v1/production/trace/{record_id}", "POST /api/v1/production/time-map/trace"],
    ),
    ("H10", ["POST /api/v1/production/replay/prepare"]),
    ("C01 C07 C09 C11", ["POST /api/v1/providers/team"]),
    ("H03 H12 K02", ["GET /api/v1/providers/availability", "POST /api/v1/providers/chat"]),
    ("P05 W02 W09 C02", ["POST /api/v1/workflows/validate"]),
    (
        "C03 C06 H06 H07 H11 O06 K06",
        [
            "POST /api/v1/execution/{run_id}/resume",
            "GET /api/v1/execution/{run_id}/recovery",
            "POST /api/v1/execution/{run_id}/reconcile",
        ],
    ),
    ("D01 D02 D05 O01", ["GET /api/v1/events", "GET /api/v1/events/stream"]),
]
# Direct actions in the project production workspace are separate from the ten
# static DAG handlers. Keep the verified HTTP method and actual router path.
auto_edit_route = "/api/v1/production/auto-edits"
generation_route = "/api/v1/production/generation"
ENDPOINT_GROUPS += [
    (
        "P01 C01 C07 C08 C09 C11 H03 H12 G01 L01 L04 L05 S06 N01 N02 N03 W01",
        ["POST " + auto_edit_route],
    ),
    (
        "C06 N06 E01 E08 W03 W04",
        ["GET " + auto_edit_route + "/{run_id}", "POST " + auto_edit_route + "/{run_id}/approve"],
    ),
    ("N02 N07 G06 E06", ["POST " + auto_edit_route + "/{run_id}/replace-shot"]),
    ("E08 Y01 Y03 Y05 Y06 Y08", ["POST " + auto_edit_route + "/{run_id}/render"]),
    ("G01 G02 G03 G04 G06", ["GET " + generation_route + "/profiles", "POST " + generation_route]),
    (
        "G02 G03 G04 G05 G06",
        ["GET " + generation_route, "POST " + generation_route + "/{id}/refresh"],
    ),
    (
        "G02 G03",
        [
            "GET " + generation_route + "/reference/frame",
            "GET " + generation_route + "/reference/assets",
        ],
    ),
    ("G04", ["POST " + generation_route + "/{id}/cancel"]),
    ("G02 G05 G06", ["POST " + generation_route + "/{id}/hold"]),
    (
        "E05 Y06",
        [
            "POST /api/v1/projects/{project_id}/timeline-exchange/" + key
            for key in ("export", "inspect", "import")
        ],
    ),
]
CURRENT_CODE_GROUPS = [
    ("F03", ["app/commercial_handoff.py", "web/commercial-handoff.js"]),
    ("J05", ["app/account_quota.py", "web/account-quota.js"]),
    ("Y04", ["app/render_recovery.py", "web/render-recovery.js"]),
    ("H04", ["app/context_pack.py", "web/context-pack.js"]),
    ("M09", ["app/post_reviews.py", "web/post-reviews.js"]),
    ("V02", ["app/script_readings.py", "web/narration.js"]),
    ("A06", ["app/source_changes.py", "app/production.py", "web/source-changes.js"]),
    ("S03 S04", ["app/asset_families.py", "web/asset-families.js"]),
    ("M04", ["app/comment_feedback.py", "web/comment-feedback.js"]),
    ("V03", ["app/narration.py", "web/narration.js", "app/execution.py"]),
    ("E05", ["app/clip_mask.py"]),
    ("T02", ["app/creative.py", "web/creative-actions.js"]),
    ("X02 X03", ["app/score_policies.py", "web/score-policies.js"]),
    ("U04 U05 U06", ["web/asset-lifecycle.js"]),
    ("U05", ["app/auto_edits.py", "web/project-production.js"]),
    ("T03 T04 T05", ["app/creative.py", "web/creative-actions.js"]),
    ("D05 S01 S02 S05", ["app/asset_index.py", "app/index_schema.py", "web/asset-search.js"]),
    ("B03 B04 B05 B06", ["app/soundtrack.py", "web/soundtrack.js"]),
    ("V06 V07 V08 V09", ["app/voice_studio.py", "web/voice-studio.js"]),
    ("V07", ["app/voice_rhythm.py"]),
    ("N04", ["app/variants.py", "app/variant_fingerprints.py", "web/variant-panel.js"]),
    ("U04 U05 U06", ["app/asset_lifecycle.py"]),
    ("X06 X07", ["app/score_policies.py", "web/score-policies.js"]),
    (
        "P01 C01 C06 C07 C08 C09 C11 H03 H12 N01 N02 N03 N06 N07 E01 E06 E08 Y01 Y03 Y05 Y06 Y08 W01 W03 W04",
        ["app/auto_edits.py", "web/project-production.js"],
    ),
    ("P01 L01 L04 L05 S06 N01 N02 N03", ["app/visual_candidates.py"]),
    ("G01 G02 G03 G04 G05 G06", ["app/generation.py", "web/generation-controls.js"]),
    ("C01 C07 C09 C11 H03 H12", ["app/providers.py"]),
    (
        "E03 E07 W03 W04 W07 W08",
        [
            "web/editor.js",
            "web/editor-layout.js",
            "web/workspace-layout.js",
            "web/editor-viewer.js",
        ],
    ),
    (
        "E02 E03 E05 Y01 Y03 Y05 Y06 Y08",
        ["app/transitions.py", "app/media.py", "web/clip-transitions.js"],
    ),
    ("E01 E05 Y06 Y09", ["app/timeline_exchange.py"]),
]
COMMAND_GROUPS = [
    ("P01", ["CreateProjectVersion", "UpdateProjectVersion"]),
    ("P06", ["FreezeProjectBaseline"]),
    ("P05 W02 W09 C02", ["SaveWorkflowVersion", "UpdateWorkflow", "StartWorkflowRun"]),
    ("V01", ["CreateFactBoundScript", "SaveScriptVersion", "UpdateScriptVersion"]),
    ("N01 N03 N05 N06", ["CommitPlanVersion", "UpdatePlan"]),
    ("E01 E02 E07", ["ImportTimelineIR", "UpdateTimeline"]),
    ("E08", ["LockTimelineRevision"]),
    ("E06 E09 C04 C05 C10", ["ApplyChangeSet"]),
    (
        "C03 C06 H05 H06 H07 H11 O03 O06 K06",
        ["AcquireLease", "ReleaseLease", "SuspendTask", "ResumeTask", "RequestCancel"],
    ),
    ("P04 O02", ["ReserveBudget", "ReleaseBudget"]),
    ("C08", ["CreateTaskContract", "AmendTaskContract"]),
    ("C01 C07 C09 C11", ["SaveAgentRole", "SaveAgentDeployment", "RecordDecision"]),
    ("H03 H12", ["SaveModelProfile"]),
    ("X03 X08", ["CreateEvaluationSuite"]),
    ("W01 W03 W04 W05 W07 W08", ["SaveView", "UpdateView"]),
]

# Precise test definitions are supporting evidence only. No run is fabricated.
TEST_GROUPS = [
    (
        "F03 J05",
        "tests/test_quota_commercial_integration.py",
        "QuotaCommercialIntegration.test_binding_and_reservation_race_cannot_both_target_the_same_version",
    ),
    (
        "F03",
        "tests/test_commercial_handoff.py",
        "CommercialHandoffTests.test_manual_binding_preflight_observed_products_and_disclosure_are_separate",
    ),
    (
        "J05",
        "tests/test_account_quota.py",
        "AccountQuotaTests.test_fair_race_expiry_and_unknown_handoff_never_auto_releases",
    ),
    (
        "Y04",
        "tests/test_render_recovery.py",
        "RenderRecoveryTests.test_running_predecessor_is_fenced_after_confirmed_handoff",
    ),
    (
        "H04",
        "tests/test_context_pack.py",
        "RecoveryPackTests.test_evidence_sources_and_stops_survive_pressure_and_unknown_requires_review",
    ),
    (
        "M09",
        "tests/test_post_reviews.py",
        "PostReviewTests.test_late_backfill_pins_new_observation_and_keeps_old_report",
    ),
    (
        "M09",
        "tests/test_post_reviews.py",
        "PostReviewTests.test_missing_latest_ambiguous_time_and_incomparable_rejected",
    ),
    (
        "V02",
        "tests/test_narration.py",
        "NarrationTests.test_corrected_price_reading_preserves_currency_numbers_and_uses_actual_tts",
    ),
    (
        "A06",
        "tests/test_source_changes.py",
        "SourceChangeTests.test_scoped_manifest_separates_origin_removal_rights_and_historical_posts",
    ),
    (
        "A06",
        "tests/test_source_changes.py",
        "SourceChangeTests.test_revision_review_dedup_late_conflict_and_per_entry_failure",
    ),
    ("A06", "scripts/browser_creative.py", "run"),
    (
        "S03 S04",
        "tests/test_asset_families.py",
        "AssetFamilyTests.test_decoded_frames_near_review_and_retirement_recompute_family_pressure",
    ),
    (
        "S03 S04",
        "tests/test_asset_families.py",
        "AssetFamilyTests.test_same_bytes_and_same_source_distinct_from_semantic_and_partial_ranges",
    ),
    (
        "X07",
        "tests/test_score_policies.py",
        "ScorePolicyTests.test_changed_sample_automatically_rolls_back_before_next_score",
    ),
    (
        "M04",
        "tests/test_comment_feedback.py",
        "CommentFeedbackTests.test_independent_body_and_classification_revisions",
    ),
    (
        "M04",
        "tests/test_comment_feedback.py",
        "CommentFeedbackTests.test_deletion_purges_excerpts_and_excludes_judgments",
    ),
    (
        "V03",
        "tests/test_narration.py",
        "NarrationTests.test_dag_freezes_script_and_review_and_produces_playable_audio",
    ),
    (
        "T02 T03",
        "tests/test_creative.py",
        "CreativeTests.test_reposted_sources_conflicts_expiry_and_hypothesis_gate",
    ),
    (
        "V07",
        "tests/test_voice_studio.py",
        "VoiceStudioTests.test_rhythm_preserves_speech_and_lock_and_uses_a_real_marked_audio_clock",
    ),
    (
        "E05",
        "tests/test_media.py",
        "MediaIntegrationTests.test_rect_mask_after_rotation_and_cover_is_applied_once_to_decoded_pixels",
    ),
    (
        "U04",
        "tests/test_asset_lifecycle.py",
        "AssetLifecycleTests.test_family_pressure_distinguishes_reservations_previews_and_accounts",
    ),
    (
        "U05",
        "tests/test_asset_lifecycle.py",
        "AssetLifecycleTests.test_exploration_preserves_floor_and_scoped_time_limited_exceptions",
    ),
    (
        "U06",
        "tests/test_asset_lifecycle.py",
        "AssetLifecycleTests.test_cold_restore_preserves_refs_alias_and_index_and_rechecks_bytes_rights",
    ),
    (
        "X02 X03 X06 X07",
        "tests/test_score_policies.py",
        "ScorePolicyTests.test_freeze_activate_consume_replay_and_rollback",
    ),
    (
        "X03 X06",
        "tests/test_score_policies.py",
        "ScorePolicyTests.test_incomplete_duplicate_or_failed_case_blocks_approval",
    ),
    (
        "T03",
        "tests/test_creative.py",
        "CreativeTests.test_scoped_match_versions_and_evergreen_fallback",
    ),
    (
        "T04 T05 T06",
        "tests/test_creative.py",
        "CreativeTests.test_director_proposals_comparison_and_approval_share_pinned_facts",
    ),
    (
        "S01 S02 S05 D05",
        "tests/test_asset_index.py",
        "AssetIndexTests.test_shadow_switch_fallback_incremental_then_rollback",
    ),
    (
        "D05 S05",
        "tests/test_asset_index.py",
        "AssetIndexTests.test_switch_race_stale_source_and_atomic_failure_keep_previous",
    ),
    (
        "B03",
        "tests/test_soundtrack.py",
        "SoundtrackTests.test_licensed_stock_and_scope_expiry_no_music_and_platform_handoff",
    ),
    (
        "B04 B05 B06",
        "tests/test_soundtrack.py",
        "SoundtrackTests.test_stock_arrange_adopt_without_duplication_deliver_measure_and_revoke",
    ),
    (
        "B05",
        "tests/test_soundtrack.py",
        "SoundtrackTests.test_ducking_changes_only_music_under_dialogue_and_periods_are_preserved",
    ),
    (
        "V06 V09",
        "tests/test_voice_studio.py",
        "VoiceStudioTests.test_actual_audio_clock_counterexample_clip_audio_caption_render_and_idempotency",
    ),
    (
        "V08",
        "tests/test_voice_studio.py",
        "VoiceStudioTests.test_equal_duration_audio_changes_require_new_attestation_and_changed_clip_blocks_render",
    ),
    (
        "N04",
        "tests/test_variants.py",
        "StoryboardVariantTests.test_actual_picture_text_audio_structure_family_choose_render_and_decode",
    ),
    (
        "N04",
        "tests/test_variants.py",
        "StoryboardVariantTests.test_locked_slot_manual_rewrite_and_repeat_history",
    ),
    ("T03 T04 T05", "scripts/browser_creative.py", "run"),
    (
        "B02",
        "tests/test_rhythm.py",
        "ManualRhythmTests.test_manual_audio_marks_independent_clock_disable_and_versions",
    ),
    (
        "T06",
        "tests/test_selection.py",
        "SelectionTests.test_creative_choice_retains_alternatives_and_exact_approval",
    ),
    (
        "N02",
        "tests/test_selection.py",
        "SelectionTests.test_real_shot_selection_then_locked_change_gap_and_lineage",
    ),
    (
        "E01 Y09",
        "tests/test_timeline_exchange.py",
        "TimelineExchangeTests.test_official_serializer_cuts_gap_captions_pinned_refs_and_idempotent_import",
    ),
    (
        "E04",
        "tests/test_captions.py",
        "CaptionExchangeTests.test_multiline_overlapping_cues_keep_actual_millisecond_and_language",
    ),
    (
        "P02",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_product_csv_money_exact_and_bad_rows_isolated",
    ),
    (
        "T01 T02",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_trend_source_window_and_atomicity",
    ),
    (
        "A01 A05 A07 I01 I02",
        "tests/test_media.py",
        "MediaIntegrationTests.test_chunk_upload_resume_duplicate_chunk_and_full_hash",
    ),
    (
        "I03 I04 I07",
        "tests/test_media.py",
        "MediaIntegrationTests.test_transform_real_bytes_trim_scale_color_loudness",
    ),
    (
        "A04 I06 K04",
        "tests/test_media.py",
        "MediaIntegrationTests.test_artifact_traces_current_grant_through_frozen_deep_inputs",
    ),
    (
        "L01 B01 B02",
        "tests/test_media.py",
        "MediaIntegrationTests.test_real_scene_candidates_frame_and_onsets_are_not_semantic_claims",
    ),
    (
        "L07 V03",
        "tests/test_media.py",
        "MediaIntegrationTests.test_real_waveform_and_audio_extract",
    ),
    (
        "L02 L03 L05 L06 S01 S02 S06 V04 V05",
        "tests/test_production.py",
        "RealMediaCrossDomainTests.test_real_probe_bounds_tags_search_and_audio_anchors",
    ),
    (
        "S03",
        "tests/test_media.py",
        "MediaIntegrationTests.test_import_probe_original_dedup_and_relative_path",
    ),
    (
        "U02 N06",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_usage_reservation_conflict_release_and_history",
    ),
    (
        "U03 U07",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_score_missing_dimensions_not_zero",
    ),
    (
        "E05",
        "tests/test_media.py",
        "MediaIntegrationTests.test_keyframe_render_changes_actual_frame_pixels",
    ),
    (
        "U01 B05 B06 E01 E02 E04 Y01 Y02 Y03 Y05 Y06 Y07 Y08 Y09",
        "tests/test_media.py",
        "MediaIntegrationTests.test_actual_multitrack_render_manifest_package_and_qc_boundary",
    ),
    (
        "E08",
        "tests/test_media.py",
        "MediaIntegrationTests.test_render_rejects_unlocked_stale_version_and_unsupported_keyframes",
    ),
    (
        "E06 E09 C04 C05 C10 V08",
        "tests/test_core.py",
        "CoreStoreTests.test_dependency_invalidation_uses_fields_and_half_open_ranges",
    ),
    (
        "J03 J04 J06 F01 F02 F04 F06 F08 K05",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_publication_reviews_current_rights_and_manual_receipt",
    ),
    (
        "M01 M02 M03 M05 M06 M07 X01",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_metric_zero_null_duplicates_and_scope_groups",
    ),
    (
        "X02 X04 X05",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_experiment_unit_data_and_honest_effect_boundary",
    ),
    (
        "C01 C07 C09 C11",
        "tests/test_production.py",
        "ProviderLedgerTests.test_three_role_team_persists_checkpoints_and_independent_proposals",
    ),
    (
        "H03 H12",
        "tests/test_production.py",
        "ProviderLedgerTests.test_model_success_idempotency_and_config_price_not_bill",
    ),
    (
        "H10 D08",
        "tests/test_production.py",
        "ProductionIntegrationTests.test_replay_checks_bytes_and_current_rights_and_bidirectional_lineage",
    ),
    (
        "M08 D03 D08",
        "tests/test_production.py",
        "RealMediaCrossDomainTests.test_actual_render_timemap_and_replay_resolve_immutable_bytes",
    ),
    (
        "D07",
        "tests/test_maintenance.py",
        "MaintenanceTests.test_backup_restore_checks_database_and_real_object_bytes",
    ),
    (
        "D07",
        "tests/test_maintenance.py",
        "MaintenanceTests.test_corrupt_and_path_traversal_archives_do_not_commit",
    ),
]


TEST_GROUPS += [
    (
        "G02 G03 G04 G06",
        "tests/test_generation.py",
        "GenerationTests.test_reference_uploaded_read_back_bound_to_graph_and_generated_lineage",
    ),
    (
        "G03 G04 I06 K04",
        "tests/test_generation.py",
        "GenerationTests.test_reference_revocation_between_upload_submit_and_adoption",
    ),
    ("C01 C07 C09 C11 K02 H03 H12", "scripts/verify_local_team.py", "main"),
    (
        "C07 C09 C11",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_evidence_mismatch_blocks_even_when_real_protocol_roles_vote_accept",
    ),
    (
        "P01 L01 L05 S06 N01 N02 N03 N06 E01 E08 Y03 Y05 Y06 Y08 W01 W03",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_real_analysis_approval_render_decode_replay_and_listing",
    ),
    (
        "N02 N03 G01",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_invalid_unknown_shortfall_and_unconfigured_model",
    ),
    (
        "C01 C07 C08 C09 C11",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_real_http_contract_three_roles_cost_replay_and_independent_judge",
    ),
    ("H03 H12 P01 N01", "scripts/verify_local_director.py", "main"),
    ("G02 G03 G04 G05 G06", "scripts/verify_generation_worker.py", "run"),
    (
        "G02 G05 G06",
        "tests/test_generation.py",
        "GenerationTests.test_verified_generated_image_stays_distinct_from_explicit_timed_still_and_is_reusable",
    ),
    (
        "G03 G04 G05 G06",
        "tests/test_generation.py",
        "GenerationTests.test_queue_download_probe_generated_origin_and_deduplicated_adoption",
    ),
    (
        "G02 G03",
        "tests/test_generation.py",
        "GenerationTests.test_real_camera_frame_pts_png_scope_idempotency_and_revocation",
    ),
    (
        "G02 G03",
        "tests/test_generation.py",
        "GenerationTests.test_full_project_reference_search_paginates_and_restores_older_originals",
    ),
    (
        "G02 G03",
        "tests/test_generation.py",
        "GenerationTests.test_variable_frame_rate_and_nonzero_origin_use_presentation_timestamps",
    ),
    (
        "G03 N06",
        "tests/test_batches.py",
        "BatchGenerationTransport.test_camera_frame_batch_slot_mixed_with_original_clip",
    ),
    (
        "G04",
        "tests/test_generation.py",
        "GenerationTests.test_lost_ack_reconciles_original_queue_and_never_interrupts_other_jobs",
    ),
    (
        "G05",
        "tests/test_generation.py",
        "GenerationTests.test_running_cancel_preserves_actual_terminal_and_rejects_unverified_video",
    ),
    (
        "L04",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_overlapping_requests_reserve_once_and_cannot_forge_ocr_or_double_apply",
    ),
    (
        "G06 N02 N07 E06",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_single_shot_fork_retains_unchanged_intervals_and_replays",
    ),
    (
        "E02 E03 E05 Y03 Y05 Y08",
        "tests/test_clip_transitions.py",
        "ClipTransitionTests.test_decoded_five_transitions_audio_mix_and_lineage",
    ),
    (
        "E01 E05 Y06 Y09",
        "tests/test_timeline_exchange.py",
        "TimelineExchangeTests.test_all_five_renderable_transitions_restore_full_ranges_and_source_link",
    ),
    (
        "Y06 Y09",
        "tests/test_timeline_exchange.py",
        "TimelineExchangeTests.test_asymmetric_external_dissolve_preserves_nominal_cut_on_reexport",
    ),
    ("C06 E03 E07 W03 W04 W07 W08", "scripts/browser_workspace_layout.py", "run"),
    ("P01 C06 N01 N02 N07 E01 E08 W01 W03 W04", "scripts/browser_ai_production.py", "run"),
]


CURRENT_CODE_GROUPS += [
    ("A02 A03", ["app/source_media.py", "web/source-media.js"]),
    ("A03", ["app/source_media.py"]),
    ("T01 T02", ["app/trend_feeds.py"]),
    ("S02", ["app/text_vectors.py", "app/asset_index.py", "scripts/verify_semantic_search.py"]),
    ("D04 C07", ["app/edit_memory.py", "app/context_pack.py", "web/edit-preferences.js"]),
    ("D06 I05", ["app/media_admission.py", "scripts/restore.py"]),
    ("F05 F07 F09", ["app/publication_lifecycle.py", "web/publication-lifecycle.js"]),
    ("J01 J02 J07 M01", ["app/tiktok_metrics.py"]),
    ("M01 M03", ["app/tiktok_metrics.py"]),
    ("M09 M08 U03 X01", ["app/retention.py", "app/edit_feedback.py"]),
    ("X03 X07", ["app/score_policies.py"]),
    ("C07 C09 H05", ["app/auto_edits.py", "app/edit_feedback.py"]),
    ("C05 C10", ["app/graph_revision.py", "app/execution.py"]),
    ("H02 H08 H09 W06 O07 X08", ["app/harness_qualification.py", "web/harness-runtime.js"]),
    ("O04 O05", ["app/batch_observability.py", "web/batch-observability.js"]),
    (
        "C02 O03 P05 Y03",
        [
            "app/batches.py",
            "app/batch_queue.py",
            "app/batch_workflows.py",
            "web/batch-workspace.js",
        ],
    ),
    ("C08 P04 N05", ["app/batch_acceptance.py", "web/batch-contracts.js"]),
    ("G02 G03", ["app/generation.py", "app/execution.py"]),
    ("E01", ["app/timeline_exchange.py", "app/execution.py"]),
]
TEST_GROUPS += [
    (
        "A02 A03",
        "tests/test_source_media.py",
        "SourceMediaTests.test_verified_bytes_source_and_idempotent_retry",
    ),
    (
        "A03",
        "tests/test_source_media.py",
        "SourceMediaTests.test_disabling_connector_before_download_commit",
    ),
    (
        "T01 T02",
        "tests/test_trend_feeds.py",
        "TrendFeedTests.test_rss_with_pinned_evidence_and_duplicate_sync",
    ),
    (
        "S02",
        "tests/test_asset_index.py",
        "AssetIndexTests.test_missing_semantic_runtime_keeps_existing_literal_workflow",
    ),
    (
        "D04 C07",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_bitemporal_project_preferences_feed_model_and_enforce_withdrawal",
    ),
    (
        "D06 I05",
        "tests/test_asset_lifecycle.py",
        "AssetLifecycleTests.test_source_admission_expiry_reconciles_real_derivative_and_preserves_shared_bytes",
    ),
    (
        "F05 F07 F09",
        "tests/test_publication_lifecycle.py",
        "PublicationLifecycleTests.test_changed_schedule_races_and_predecessor_late_evidence_blocks_successor",
    ),
    (
        "J01 J02 J07 M01",
        "tests/test_tiktok_metrics.py",
        "TikTokMetricsTests.test_identity_capability_probe_and_suspend",
    ),
    (
        "M01 M03",
        "tests/test_tiktok_metrics.py",
        "TikTokMetricsTests.test_missing_post_zero_field_and_retry",
    ),
    (
        "M09 M08 U03 X01",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_retention_maps_actual_render_to_frozen_edit_feedback",
    ),
    (
        "X03 X07",
        "tests/test_score_policies.py",
        "ScorePolicyTests.test_fit_draft_holdout_and_production_release",
    ),
    (
        "C07 C09 H05",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_durable_second_director_turn_consumes_failure_and_keeps_original",
    ),
    (
        "C05 C10",
        "tests/test_execution.py",
        "ExecutionTests.test_revise_only_unstarted_node_preserves_checkpoint_and_executes_new_graph",
    ),
    (
        "H02 H08 H09 W06 O07 X08",
        "tests/test_harness_qualification.py",
        "QualificationTests.test_qualified_workflow_dispatch_and_runtime_drift",
    ),
    (
        "O04 O05",
        "tests/test_batch_observability.py",
        "BatchObservabilityTests.test_comparison_same_outputs_with_fixed_sample_and_reject_mismatched_scope",
    ),
    (
        "C02 O03 P05 Y03",
        "tests/test_batches.py",
        "BatchTests.test_batch_dispatches_frozen_dag_input_and_records_child_run",
    ),
    (
        "C08 P04 N05",
        "tests/test_batch_acceptance.py",
        "BatchAcceptanceTests.test_versioned_preflight_and_actual_decoded_output",
    ),
    (
        "G02 G03",
        "tests/test_generation.py",
        "GenerationTests.test_generated_workflow_parks_and_resumes_same_request_with_one_attempt",
    ),
    (
        "E01",
        "tests/test_timeline_exchange.py",
        "TimelineExchangeTests.test_hand_edited_otio_is_a_dag_input",
    ),
]
ENDPOINT_GROUPS += [
    (
        "A02 A03",
        [
            "GET /api/v1/production/source-media/search",
            "POST /api/v1/production/source-media/import",
        ],
    ),
    ("A03", ["POST /api/v1/production/source-media/settings"]),
    ("T01 T02", ["POST /api/v1/production/trends/sync"]),
    ("S02", ["POST /api/v1/production/asset-index/embed"]),
    ("D04 C07", ["POST /api/v1/execution/edit-preferences"]),
    (
        "D06 I05",
        [
            "POST /api/v1/production/assets/admission",
            "POST /api/v1/production/assets/retention-reconcile",
        ],
    ),
    (
        "F05 F07 F09",
        [
            "POST /api/v1/production/publication-lifecycle/{pid}/change",
            "POST /api/v1/production/publication-lifecycle/{pid}/observe",
            "POST /api/v1/production/publication-lifecycle/{pid}/cancel",
        ],
    ),
    (
        "J01 J02 J07 M01",
        [
            "GET /api/v1/production/tiktok/accounts/{account_id}/connection",
            "POST /api/v1/production/tiktok/accounts/{account_id}/connection",
        ],
    ),
    ("M01 M03", ["POST /api/v1/production/tiktok/metrics"]),
    ("M09 M08 U03 X01", ["POST /api/v1/production/post-reviews/retention-mapping"]),
    ("X03 X07", ["POST /api/v1/production/score-policies/optimize"]),
    ("C05 C10", ["POST /api/v1/execution/{run_id}/revise-node"]),
    (
        "H02 H08 H09 W06 O07 X08",
        [
            "GET /api/v1/harness/runtime",
            "POST /api/v1/harness/qualify",
            "POST /api/v1/harness/dispatch",
        ],
    ),
    (
        "O04 O05",
        [
            "GET /api/v1/production/batches/observability/health",
            "GET /api/v1/production/batches/observability/compare",
        ],
    ),
    (
        "C02 O03 P05 Y03",
        ["POST /api/v1/production/batches", "GET /api/v1/production/batches/{bid}"],
    ),
    ("C08 P04 N05", ["POST /api/v1/production/batches/contracts"]),
]


CURRENT_CODE_GROUPS += [
    ("P03", ["app/batch_market.py", "app/batches.py"]),
    ("M03 M08 M09 U01 U03 X01", ["app/placement_performance.py", "web/placement-performance.js"]),
]
ENDPOINT_GROUPS += [
    ("P03", ["POST /api/v1/production/batches/market-briefs"]),
    ("M03 M08 M09 U01 U03 X01", ["GET /api/v1/production/post-reviews/placement-performance"]),
]
TEST_GROUPS += [
    (
        "P03",
        "tests/test_batch_market.py",
        "BatchMarketTests.test_market_scope_sku_number_and_prohibited_expression",
    ),
    (
        "P03",
        "tests/test_batch_market.py",
        "BatchMarketTests.test_caption_binding_gate_and_live_evidence_drift_before_worker",
    ),
    (
        "M03 M08 M09 U01 U03 X01",
        "tests/test_auto_edits.py",
        "AutoEditTests.test_retention_maps_actual_render_to_frozen_edit_feedback",
    ),
]


CURRENT_CODE_GROUPS += [
    ("A01 A05 A06", ["app/directory_import.py", "app/media.py", "web/directory-intake.js"]),
    ("H05 H08 D08", ["app/evidence_replay.py", "app/providers.py", "web/evidence-replay.js"]),
    ("M09 X01", ["app/edit_feedback.py"]),
    ("O03 C06", ["scripts/run.py", "app/batches.py"]),
    ("W03 W07 W08", ["web/workspace-layout.js", "web/asset-search.js", "web/editor.js"]),
]
ENDPOINT_GROUPS += [
    (
        "A01 A05 A06",
        [
            "GET /api/v1/production/directory-imports/roots",
            "POST /api/v1/production/directory-imports/observe",
            "POST /api/v1/production/directory-imports/advance",
            "POST /api/v1/production/directory-imports/items",
        ],
    ),
    (
        "H05 H08 D08",
        ["GET /api/v1/harness/replay-evidence", "GET /api/v1/harness/replay-evidence/{record_id}"],
    ),
    ("M09 X01", ["POST /api/v1/production/post-reviews/placement-hypotheses"]),
]
TEST_GROUPS += [
    (
        "A01 A05 A06",
        "tests/test_directory_import.py",
        "DirectoryIntakeTests.test_pending_during_write_same_name_history_and_duplicate_bytes_after_restart",
    ),
    (
        "A01 A05 A06",
        "tests/test_directory_import.py",
        "DirectoryIntakeTests.test_manifest_digest_link_swap_scope_and_per_item_failure",
    ),
    (
        "H05 H08 D08",
        "tests/test_evidence_replay.py",
        "EvidenceReplayTests.test_incomplete_revoked_and_external_publish_receipts_cannot_reissue",
    ),
    (
        "O03 C06",
        "tests/test_dedicated_worker.py",
        "DedicatedWorkerTests.test_worker_drain_restart_and_unique_completed_outputs",
    ),
    ("W03 W07 W08", "scripts/browser_module_layout.py", "run"),
]


CURRENT_CODE_GROUPS += [("J01 J02 J07", ["app/tiktok_login.py", "web/tiktok-login.js"])]
ENDPOINT_GROUPS += [
    (
        "J01 J02",
        [
            "POST /api/v1/production/tiktok/login/accounts/{account}/start",
            "GET /api/v1/production/tiktok/login/callback",
            "POST /api/v1/production/tiktok/login/accounts/{account}/refresh",
            "POST /api/v1/production/tiktok/login/accounts/{account}/revoke",
        ],
    )
]
TEST_GROUPS += [
    ("J01 J02 J07", "scripts/browser_tiktok_login.py", "run"),
    (
        "T01",
        "tests/test_trend_feeds.py",
        "TrendFeedTests.test_instance_charts_preserve_federated_origin_and_cannot_claim_a_region",
    ),
]


CURRENT_CODE_GROUPS += [
    ("L04 V04", ["app/speech.py", "app/speech_worker.py", "web/speech-review.js"]),
]
ENDPOINT_GROUPS += [
    (
        "L04 V04",
        [
            "GET /api/v1/production/speech/models",
            "POST /api/v1/production/speech/recognize",
            "POST /api/v1/production/speech/review",
            "GET /api/v1/production/speech/reviews/{approval_id}/captions",
        ],
    ),
]
TEST_GROUPS += [
    ("L04 V04", "scripts/verify_local_speech.py", "run"),
    (
        "L04 V04",
        "tests/test_local_speech.py",
        "LocalSpeechTests.test_review_permissions_and_source_bound_indices",
    ),
]


CURRENT_CODE_GROUPS += [
    (
        "S02 L03",
        [
            "app/frame_vectors.py",
            "app/visual_index.py",
            "app/asset_index.py",
            "web/asset-search.js",
        ],
    ),
]
ENDPOINT_GROUPS += [
    (
        "S02 L03",
        [
            "POST /api/v1/production/asset-index/frames/build",
            "POST /api/v1/production/asset-index/query",
        ],
    ),
]
TEST_GROUPS += [
    ("S02 L03", "scripts/verify_visual_search.py", "verify"),
    (
        "S02",
        "tests/test_visual_index.py",
        "VisualIndexTests.test_missing_or_invalid_weights_preserve_literal_search_and_explicit_build_status",
    ),
    (
        "H08 O07",
        "tests/test_harness_qualification.py",
        "QualificationTests.test_handler_body_change_with_unchanged_schema_invalidates_qualification",
    ),
]


CURRENT_CODE_GROUPS += [
    (
        "G02 G03",
        [
            "app/generation_frames.py",
            "app/generation.py",
            "app/batches.py",
            "web/reference-frame.js",
        ],
    ),
]
TEST_GROUPS += [("G02 G03", "scripts/browser_generation_frames.py", "run")]


def read_audit_rows():
    rows = {}
    for line in (ROOT / "docs/source-coverage-review.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not re.match(r"^[A-Z]\d\d\s", cells[0]):
            continue
        fid, label = cells[0].split(" ", 1)
        levels = re.findall(r"\*\*(.*?)\*\*", cells[1])
        assert len(levels) == 1 and levels[0] in STATUS_LABELS.values(), fid
        assert fid not in rows, f"Duplicate audit row: {fid}"
        rows[fid] = {
            "label": label,
            "status_label": levels[0],
            "implemented_subset": cells[2],
            "remaining_scope": cells[3],
        }
    return rows


def read_scenario_audit():
    rows = {}
    directions = {}
    for line in (ROOT / "docs/scenario-coverage.md").read_text().splitlines():
        heading = re.match(r"^\| (?:正向|反向|跨模块) ([NOX]) \| (\d+) \| (\d+) \| 0 \|$", line)
        if heading:
            directions[heading[1]] = (int(heading[2]), int(heading[3]))
        match = re.match(r"^\| (R1-\d\d-[NOX]) /.*?\|.*?\| \*\*(.*?)\*\*", line)
        if match:
            assert match[1] not in rows, f"Duplicate scenario: {match[1]}"
            assert match[2] in {"部分", "未覆盖"}, "No evidence for full scenario acceptance"
            rows[match[1]] = "partial" if match[2] == "部分" else "uncovered"
    assert set(rows) == {s["id"] for s in atlas["v6"]["scenarioMatrix"]}
    for suffix in ("N", "O", "X"):
        counts = Counter(value for key, value in rows.items() if key.endswith(suffix))
        assert directions.get(suffix) == (
            counts["partial"],
            counts["uncovered"],
        ), f"Scenario totals differ: {suffix}"
    return rows


def workflow_registry():
    tree = ast.parse((ROOT / "app/execution.py").read_text())
    handlers = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "HANDLERS" for t in node.targets
        ):
            handlers = ast.literal_eval(node.value)
    assert (
        isinstance(handlers, dict) and set(handlers) == EXPECTED_WORKFLOW_ROUTES
    ), "Workflow registration changed: re-audit explicit support"
    return handlers


def test_symbol_exists(path, symbol):
    tree = ast.parse((ROOT / path).read_text())
    parts = symbol.split(".")
    body = tree.body
    for part in parts:
        node = next(
            (
                n
                for n in body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.name == part
            ),
            None,
        )
        if node is None:
            return False
        body = node.body
    return True


def declared_api_endpoints():
    # Mounted routers may inherit several nested prefixes. Use the executable contract.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.main import app

    return {
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }


def group_values(groups, fid):
    return sorted({value for ids, values in groups if fid in ids.split() for value in values})


def build_manifest():
    source = ROOT / "docs/AI_Video_Agent_Atlas.html"
    assert atlas["schemaVersion"] == "6.0"
    assert (
        hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    ), "Original HTML changed: classification requires a new audit"
    source_data = re.search(
        r'<script\s+type="application/json"\s+id="atlas-data">(.*?)</script>',
        source.read_text(),
        re.S,
    )
    assert source_data and json.loads(source_data[1]) == atlas, "Source JSON drift"
    seen = set()
    for status, ids in STATUS_IDS.items():
        assert len(ids) == EXPECTED_COUNTS[status]
        assert not seen.intersection(ids), "Coverage statuses must be mutually exclusive"
        seen.update(ids)
    assert (
        seen == {f["id"] for f in atlas["features"]} and len(seen) == 178
    ), "Source IDs changed: no automatic implemented default"
    audits = read_audit_rows()
    assert set(audits) == seen
    scenario_rows = read_scenario_audit()
    handlers = workflow_registry()
    endpoints = declared_api_endpoints()
    from scripts.feature_run_evidence import FLAGS, validate_index

    index = json.loads((ROOT / "contracts/feature-evidence.json").read_text())
    runs, reviews = validate_index(ROOT, atlas, index, SOURCE_SHA256)
    features = []
    for feature in atlas["features"]:
        fid = feature["id"]
        status = next(status for status, ids in STATUS_IDS.items() if fid in ids)
        audit = audits[fid]
        assert (
            audit["label"] == feature["label"] and audit["status_label"] == STATUS_LABELS[status]
        ), f"Audit classification drift: {fid}"
        mapped = mapping.get(
            fid,
            {
                "code": ["app/catalog.py", "app/schemas.py", "web/controls.js"],
                "test_definition": "scripts/check_contracts.py",
            },
        )
        code = set(mapped["code"]) | set(group_values(CURRENT_CODE_GROUPS, fid))
        test_files = {mapped["test_definition"]}
        definitions = [
            {
                "file": path,
                "symbol": symbol,
                "scope": "仅下列已实现子路径的测试定义；不代表整功能通过",
            }
            for ids, path, symbol in TEST_GROUPS
            if fid in ids.split()
        ]
        if fid in EXECUTION_FEATURES:
            code.update({"app/execution.py", "app/store.py"})
            test_files.add("tests/test_execution.py")
            definitions.append(
                {
                    "file": "tests/test_execution.py",
                    "symbol": "ExecutionTests.test_real_transform_then_extract_audio_and_idempotent_replay",
                    "scope": "共享本地执行器的真实媒体示例；不证明本功能的全部领域语义",
                }
            )
        if fid in BUDGET_FEATURES:
            code.add("app/budget.py")
            test_files.add("tests/test_budget.py")
            definitions.append(
                {
                    "file": "tests/test_budget.py",
                    "symbol": "BudgetIntegrationTests.test_manual_reservation_blocks_model_before_network_then_releases_exact_hold",
                    "scope": "共享预算准入和精确预留机制；供应商传输为显式模拟",
                }
            )
        if fid in {"T06", "N02"}:
            code.update({"app/selection.py", "web/selection-actions.js"})
        if fid in {"E01", "Y09"}:
            code.update({"app/timeline_exchange.py", "web/editor-exchange.js"})
        if fid == "B02":
            code.update({"app/rhythm.py", "web/rhythm.js", "web/editor.js"})
        if fid == "E04":
            code.add("app/captions.py")
        if fid in {"A04", "I06", "K04"}:
            code.add("app/media.py")
        for definition in definitions:
            test_files.add(definition["file"])
            assert test_symbol_exists(definition["file"], definition["symbol"]), definition
        if status == "contract_and_configuration_only":
            definitions = [
                {
                    "file": "scripts/check_contracts.py",
                    "symbol": "check",
                    "scope": "仅源定义/合同结构检查，没有专用业务完成证据",
                }
            ]
        source_routes = [r["id"] for r in feature["routes"]]
        registered_routes = [r for r in source_routes if r in handlers]
        direct_endpoints = group_values(ENDPOINT_GROUPS, fid)
        assert set(direct_endpoints) <= endpoints, f"Undeclared endpoint: {fid}"
        command_names = group_values(COMMAND_GROUPS, fid)
        if status == "contract_and_configuration_only":
            assert not registered_routes and not direct_endpoints and not command_names
        for path in code | test_files:
            assert (ROOT / path).is_file(), f"Missing evidence file: {path}"
        features.append(
            {
                "id": fid,
                "module": feature["module"],
                "label": feature["label"],
                "catalog_present": True,
                "status": status,
                "status_label": STATUS_LABELS[status],
                "source_route_count": len(source_routes),
                "source_route_ids": source_routes,
                "source_control_count": len(feature["operation"]["controls"]),
                "configuration": {
                    "ui": "web/controls.js",
                    "persistence": "View.operation_scope.feature_configs",
                    "create_endpoint": "POST /api/v1/records",
                    "update_endpoint": "PATCH /api/v1/records/{record_id}",
                    "boundary": "保存路线、控件和草稿；不触发业务执行",
                },
                "contract_request": {
                    "endpoint": "POST /api/v1/commands",
                    "command": "ConfigureFeature",
                    "result": "TaskContract 与需补充/blocked Run",
                    "executes_business_work": False,
                },
                "execution": {
                    "registered_workflow_routes": registered_routes,
                    "workflow_start_endpoint": (
                        "POST /api/v1/execution/start" if registered_routes else None
                    ),
                    "direct_api_endpoints": direct_endpoints,
                    "control_plane_commands": [
                        {"endpoint": "POST /api/v1/commands", "command": name}
                        for name in command_names
                    ],
                    "boundary": "仅支持审计列出的子集；部分入口为通用辅助机制，注册/保存/接口成功不代表原文领域目标已通过",
                },
                "implemented_subset": audit["implemented_subset"],
                "remaining_scope": audit["remaining_scope"],
                "code": sorted(code),
                "test_files": sorted(test_files),
                "test_definitions": definitions,
                "verification_runs": runs.get(fid, []),
                **({"acceptance_reviews": reviews[fid]} if fid in reviews else {}),
                "verification_boundary": (
                    "固定运行报告与原图谱条件逐项对应，适用范围见 checks。"
                    if fid in runs
                    else "尚未为该项绑定实际运行凭据。"
                ),
                **{
                    flag: any(r["current"] for r in reviews.get(fid, {}).get(flag, []))
                    for flag in FLAGS
                },
                "source_quality_gates": feature["operation"]["qualityGates"],
                "acceptance_remaining": (
                    []
                    if any(
                        r["current"]
                        for r in reviews.get(fid, {}).get("full_business_acceptance", [])
                    )
                    else feature["operation"]["qualityGates"]
                ),
                "acceptance_boundary": "原文门槛完整保留，未以目录/结构测试推导逐门槛或整功能通过",
                "requirement_ids": feature["requirements"],
                "scenario_ids": [
                    s["id"] for s in atlas["v6"]["scenarioMatrix"] if fid in s["featureIds"]
                ],
            }
        )
    counts = Counter(f["status"] for f in features)
    assert {status: counts[status] for status in STATUS_LABELS} == EXPECTED_COUNTS
    decisions = {flag: sum(f[flag] for f in features) for flag in FLAGS}
    run_features = sum(bool(f["verification_runs"]) for f in features)
    return {
        "schema_version": "implementation-2",
        "source_version": atlas["schemaVersion"],
        "source_html_sha256": SOURCE_SHA256,
        "boundary": f"原图谱共 {len(features)} 项；{run_features} 项绑定已核对的运行凭据，{decisions['full_business_acceptance']} 项取得当前运行版本的独立整项验收。目录和实际可执行路线分别统计。",
        "status_definitions": STATUS_LABELS,
        "counts": {
            "features": len(features),
            "by_status": {status: counts[status] for status in STATUS_LABELS},
            **decisions,
            "pinned_execution_reports": len(index["runs"]),
            "features_with_execution_reports": run_features,
        },
        "workflow_registry": {
            "source_file": "app/execution.py",
            "registered_routes": sorted(handlers),
            "registered_count": len(handlers),
            "boundary": "静态读取真实HANDLERS注册表；不是534条原文路线全量执行或全验收声明",
        },
        "scenario_coverage": {
            "total": len(scenario_rows),
            "partial": list(scenario_rows.values()).count("partial"),
            "uncovered": list(scenario_rows.values()).count("uncovered"),
            "full_acceptance_passed": list(scenario_rows.values()).count("accepted"),
            "audit_document": "docs/scenario-coverage.md",
            "boundary": "部分仅指列出的子约束证据；逐场景完整验收另行核实",
        },
        "audit_document": "docs/source-coverage-review.md",
        "legacy_classification": {
            "schema_version": "implementation-1",
            "local_path_partial": 133,
            "configuration_only": 45,
            "historical_only": True,
            "boundary": "旧133对应当时105+21+7，仅作历史记录，不作为当前实现数",
        },
        "features": features,
    }


def render_markdown(report):
    lines = [
        "# 功能实现逐项对应",
        "",
        report["boundary"],
        "",
        f"本表由 implementation-2 清单生成，与独立审计四类一致。176 项有真实局部路径、0 项通用基础/配置、2 项仅有未实联适配器、0 项仅合同配置；完整原文业务验收通过 {report['counts']['full_business_acceptance']}。历史 implementation-1 的 133 项‘局部路径’已拆成 105+21+7，不是已完成功能数。",
        "",
        "配置保存到 View；ConfigureFeature 只登记任务合同；真实执行走专用 API、明确控制面命令或已注册工作流路线。每项原文路线另列 source_route_ids，不等同于已经可执行。",
        "",
        f"已注册工作流路线（{report['workflow_registry']['registered_count']}）："
        + "、".join(report["workflow_registry"]["registered_routes"])
        + "。",
        "",
        f"69 个原文场景：{report['scenario_coverage']['partial']} 部分覆盖、{report['scenario_coverage']['uncovered']} 未覆盖、{report['scenario_coverage']['full_acceptance_passed']} 全场景验收通过。具体测试定义与未覆盖项见 docs/scenario-coverage.md。",
        "",
        "| ID | 功能 | 独立审计等级 | 当前真实子集 | 主要剩余范围 | 注册工作流路线 |",
        "|---|---|---|---|---|---|",
    ]
    for f in report["features"]:
        columns = [
            f["id"],
            f["label"],
            f["status_label"],
            f["implemented_subset"],
            f["remaining_scope"],
            "、".join(f["execution"]["registered_workflow_routes"]) or "无",
        ]
        lines.append(
            "| "
            + " | ".join(value.replace("|", "\\|").replace("\n", " ") for value in columns)
            + " |"
        )
    lines += [
        "",
        "测试定义见 test_definitions；实际运行的哈希、图谱条件、环境与核验范围见 verification_runs。[已核对的运行报告与晋级条件](feature-evidence.md)。",
        "",
        "本地三角色 Qwen2.5 1.5B、Qwen2.5 0.5B 与 SmolLM2 360M 具有分别核验的真实调用，错引观测的两张接受票被拦截。独立的单导演、SD1.5 静帧及 AnimateDiff-Lightning + SD1.5 动态视频已通过真实权重推理。付费供应商、平台账号、复杂声场 ASR、多语种 TTS、图像/视频的商品语义验收、大规模跨模态检索、商业编辑器往返及生产容量仍缺独立验收。",
    ]
    return "\n".join(lines) + "\n"


def main():
    report = build_manifest()
    (ROOT / "contracts/implementation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    (ROOT / "docs/implementation-matrix.md").write_text(render_markdown(report))
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
