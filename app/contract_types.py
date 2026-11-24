"""Explicit engineering field-shape policy for the 64 source entity contracts.

No scalar precision/enum is invented when the source is silent. Descriptive
provider/raw payloads have a named opaque-object boundary. Union references and
rational time preserve the HTML's stated alternatives.
"""

import copy
import re

REF = {
    "anyOf": [
        {"type": "string", "minLength": 1},
        {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "version": {"type": "integer", "minimum": 1},
                "kind": {"type": "string"},
            },
            "additionalProperties": True,
        },
        {"type": "null"},
    ],
    "x-shape": "immutable-reference",
}
REFS = {"type": "array", "items": REF}
JSON_OBJECT = {
    "type": ["object", "null"],
    "additionalProperties": True,
    "x-shape": "named-object-extension",
}
CONFIG = {"type": ["object", "array", "null"], "x-shape": "structured-configuration"}
ARRAY = {"type": ["array", "null"], "items": {}, "x-shape": "declared-collection"}
TEXT = {"type": ["string", "null"]}
NUMBER = {"type": ["number", "null"]}
INTEGER = {"type": ["integer", "null"]}
BOOL = {"type": ["boolean", "null"]}
VERSION = {
    "anyOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}, REF],
    "x-shape": "version-or-versioned-reference",
}
TIME = {"type": ["string", "null"], "format": "date-time", "x-shape": "utc-instant"}
RATIONAL = {
    "anyOf": [
        {"type": "object", "additionalProperties": True},
        {"type": "string", "pattern": r"^-?[0-9]+/[1-9][0-9]*$"},
        {"type": "null"},
    ],
    "x-shape": "explicit-rational-timebase",
}
RANGE = {
    "anyOf": [
        {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
        {"type": "object", "additionalProperties": True},
        {"type": "null"},
    ],
    "x-shape": "half-open-time-range",
}

ARRAY_NAMES = set(
    "claims trend_refs provenance_sources accepted_ranges input_contributors spatial_maps parent_contributions keyframes members evidence_frame_refs post_exposures transforms metric_snapshot_refs cue_ranges beats downbeats sections onsets instrumentation audio_tracks processing_chains slots anchors candidates reservations tracks clip_uses effects captions sequences parameter_bindings keyframe_curves effect_stacks mask_tracks motion_tracks text_layouts grant_refs current_grant_refs shop_bindings linked_accounts scopes post_refs post_info_ignored_fields provider_object_refs metric_refs confounders hypothesis_refs activation_receipts optional_nodes checkpoints typed_port_bindings subflow_refs loop_contracts nodes edges active_run_refs applied_change_ids parked_nodes immutable_completed_refs node_contract_refs role_refs options proposal_refs evidence_refs constraint_results contradiction_refs approval_refs reviewer_role_refs operations affected_node_ids affected_artifact_refs retained_refs lock_conflicts preconditions reconciliation_refs case_specs assertions hard_fail_rules required_capabilities coverage_gaps allowed_actions denied_actions resource_scopes required_scopes supported_modalities success_criteria required_outputs optional_outputs acceptance_tests hard_constraints authorization_requirements stop_conditions negative_preferences inferred_hypotheses conflicts override_precedence anchor_ids selected_shot_refs clip_use_ids output_ranges segments actual_outputs missing_dependencies files pending_actions source_versions stages links unresolved_refs chunk_manifest ordered_steps quality_gates actual_steps mask_refs step_results frame_decisions step_exceptions input_refs removed_intervals inserted_intervals audio_sample_maps modalities role_bindings independence_groups handoff_evidence supporting_evidence counter_evidence expected_outputs".split()
)
OBJECT_NAMES = set(
    "brand_rules parameter_defaults quality_profiles delivery_variants validity license_snapshot provenance_manifest media_probe transform generation_manifest generation_parameters probe_color_profile content_protection context_dimensions dimension_scores weight_version calibration_version gain ducking audio_bus_graph gain_automation measurement_report audio_clock_maps constraints source_pts_map color_pipeline orientation_overrides parameter_resolution_report brand_constraints legacy_clock_projection render_profile checkpoint spec quality_report transform_manifest handoff_manifest runtime_compatibility redaction_manifest platform_music_parameters creator_info_snapshot_ref dst_resolution precheck_violation_result precheck_quality_result window attribution_rule refund_rule date_boundary requested_coverage actual_coverage result assignment windows guardrails stop_rule typed_config evaluation rollout promotion promotion_gates budgets guards node_catalog_snapshot parameter_overrides parameter_schemas route_input_requirements edge_semantics bounded_loop_contract candidate_release usage negative_outcome_evidence scope limits correction outbox before_after schema observed_limit layout saved_filters selections operation_scope correlation_evidence moderation remote_deletion_evidence music_observation_evidence metadata_application_status_by_field budget_policy context_policy approval_policy input_schema output_schema compatibility resource_estimate soft_preferences budget_caps delegation_scope selection_snapshot resource_plan loop_budget score_breakdown selected_option uncertainty independence_evidence evidence_delta invalidation_plan safe_point_policy expected_revisions validation_evidence_refs dimension_weights expected_variance run_budget holdout_policy source_snapshot explicit_preferences allowed_learning_scope expiry_policy consent_record semantic clock_transform_ref render_profile_snapshot runtime_versions resources output_map replay_tolerances effective_parameter_snapshot resolution_dependencies effect_runtime_state text_shape_snapshot color_chain_snapshot audio_routing_snapshot approved_usage compatibility_snapshot loss_report reimport_mapping commercial_metadata query_scope visibility_filter staleness file_identity error seed_policy parameter_schema per_asset_overrides input_contract output_contract scope_policy step_contracts order_invariant frame_policy overlay_policy realized_parameters tool_versions checks comparison_evidence coverage_report sampling_parameters retry_policy region_policy parameter_support identity_evidence role_qualification route_health tool_allowlists evidence_access_policy handoff_schema independence_contract loop_contract release_scope proposed_change valid_scope config_versions tolerances side_effect_policy".split()
)
NUMBER_NAMES = set(
    "quantity price source_in source_out source_pts_start source_pts_end count confidence quality relevance fatigue observed_score coverage exposure gain loudness duration tempo_bpm analysis_confidence estimated actual correlation_confidence denominator value_or_null retention data_mature_after valid_time observed_time similarity resource_estimate cost_budget".split()
)
INTEGER_NAMES = set(
    "dimensions sample_n exposure_n fencing_token sample_rate sample_count normalized_offset_samples stream_index timebase_num timebase_den source_timebase_num source_timebase_den revision expected_revision expected_post_count schedule_generation connection_generation refresh_generation aggregate_version consumer_offset expected_object_version delegation_limit scope_revision graph_revision expected_graph_revision state_revision base_graph_revision subject_version event_watermark entry_count size_bytes transferred_bytes context_limit timeout_ms max_rounds max_delegation_depth concurrency token_budget audio_sample_rate".split()
)
BOOL_NAMES = set(
    "audience_overlap_unknown duplicate_intent_flag vocal_presence structured_output order_invariant".split()
)
OPAQUE_NAMES = {
    "raw_expiry_value",
    "refresh_expiry_raw",
    "schema",
    "native_payload_schema_ref",
    "pronunciation",
    "pronunciation_version",
    "provenance",
    "evidence",
    "quality",
    "capture_quality",
    "permission",
    "consent",
    "health",
    "input_bindings",
    "locks",
    "reviewer_lock",
    "voice_rights",
    "rights",
    "population",
    "hypothesis",
    "retention_policy",
    "encryption_key_ref",
    "endpoint_ref",
    "region_policy",
    "budget_caps",
    "input_contract",
    "output_contract",
    "holdout_policy",
    "source_snapshot",
    "body_retention_basis",
}
RANGE_NAMES = {
    "interval",
    "time_range",
    "timeline_range",
    "source_range",
    "evidence_audio_range",
    "audio_sample_range",
    "validity_window",
    "usage_window",
    "music_cue",
}

# Exact source data with polymorphic but structurally limited representations.
OVERRIDES = {
    ("Experiment", "guardrails"): {"type": ["array", "object", "null"]},
    ("Experiment", "stop_rule"): {"type": ["string", "object", "null"]},
    ("Metric", "attribution_rule"): {"type": ["string", "object", "null"]},
    ("Post", "correlation_evidence"): {"anyOf": [REF, REFS]},
    ("ReplayManifest", "side_effect_policy"): {"type": ["string", "object", "null"]},
    ("AgentRole", "context_policy"): {"type": ["string", "object", "null"]},
    ("AgentDeployment", "tool_allowlists"): {"type": ["array", "object", "null"]},
    ("AgentDeployment", "release_scope"): {"type": ["string", "object", "array", "null"]},
    ("Run", "context_ref"): {"anyOf": [REF, REFS]},
    ("Project", "goals"): {"type": ["string", "array", "object"]},
    ("Brief", "audience"): {"type": ["string", "array", "object", "null"]},
    ("Tag", "subject"): {"type": ["string", "object", "null"]},
    ("Tag", "entity"): {"type": ["string", "object", "null"]},
    ("Tag", "person"): {"type": ["string", "array", "object", "null"]},
    ("Tag", "reviewer_lock"): {"type": ["boolean", "object", "null"]},
    ("AssetScore", "eligibility"): {"type": ["boolean", "string", "object", "null"]},
    ("Audio", "transcript"): {"type": ["string", "array", "object", "null"]},
    ("Audio", "generated_or_imported"): {"type": ["string", "boolean", "null"]},
    ("Music", "emotion"): {"type": ["string", "array", "object", "null"]},
    ("Music", "analysis_outputs"): JSON_OBJECT,
    ("Mix", "gain"): {"type": ["number", "object", "null"]},
    ("Mix", "ducking"): {"type": ["boolean", "object", "null"]},
    ("Mix", "loudness"): {"type": ["number", "object", "null"]},
    ("Grant", "consent"): {"type": ["boolean", "object", "null"]},
    ("Metric", "source"): {"type": ["string", "object", "null"]},
    ("Metric", "window"): {"type": ["string", "object"]},
    ("Metric", "denominator"): {"type": ["number", "string", "object", "null"]},
    ("Metric", "data_mature_after"): {"type": ["number", "string", "null"]},
    ("Comment", "retention"): {"type": ["number", "string", "object", "null"]},
    ("Comment", "redaction"): {"type": ["boolean", "string", "object", "null"]},
    ("Experiment", "baseline"): REF,
    ("Experiment", "candidate"): REF,
    ("Policy", "parent"): REF,
    ("Run", "attempt_id"): {"type": ["string", "integer"]},
    ("ToolCall", "side_effect"): {"type": ["boolean", "string", "object", "null"]},
    ("ToolCall", "outcome"): {"type": ["string", "object", "null"]},
    ("ToolCall", "attempt_id"): {"type": ["string", "integer", "null"]},
    ("Approval", "scope"): {"type": ["string", "object", "array", "null"]},
    ("Approval", "limits"): {"type": ["object", "number", "null"]},
    ("Memory", "source_record"): REF,
    ("Memory", "valid_time"): {"type": ["string", "object", "array", "null"]},
    ("Memory", "observed_time"): TIME,
    ("Memory", "episode"): {"type": ["string", "object", "null"]},
    ("Cost", "account"): REF,
    ("Cost", "limits"): {"type": ["number", "object"]},
    ("Capability", "availability"): {"type": ["string", "object", "boolean", "null"]},
    ("Capability", "scopes"): {"type": ["array", "object", "null"]},
    ("DecisionRecord", "confidence"): {"type": ["number", "string", "object", "null"]},
    ("DecisionRecord", "economic_stop_reason"): TEXT,
    ("TaskContract", "input_bindings"): {"type": ["object", "array"]},
    ("TaskContract", "hard_constraints"): {"type": ["array", "object", "string", "null"]},
    ("TaskContract", "deadline"): TIME,
    ("TransformRecipe", "order_invariant"): {"type": ["boolean", "string", "object", "null"]},
    ("TransformRun", "comparison_evidence"): {"type": ["object", "array", "null"]},
    ("TimeMap", "source_timebase"): RATIONAL,
    ("TimeMap", "target_timebase"): RATIONAL,
    ("TimeMap", "mapping_quality"): {"type": ["string", "object", "null"]},
    ("ModelProfile", "structured_output"): {"type": ["boolean", "object", "null"]},
    ("AgentDeployment", "stop_conditions"): {"type": ["array", "object", "null"]},
    ("AgentDeployment", "handoff_evidence"): {"type": ["array", "object", "null"]},
    ("Hypothesis", "features"): {"type": ["array", "object", "null"]},
    ("ReplayManifest", "mode"): TEXT,
}


def typed_field(kind, name, description=None, relations=None):
    if (kind, name) in OVERRIDES:
        result = copy.deepcopy(OVERRIDES[(kind, name)])
    elif name in RANGE_NAMES:
        result = copy.deepcopy(RANGE)
    elif name in (
        "timebase",
        "frame_rate",
        "analysis_timebase",
        "output_timebase",
        "audio_timebase",
        "mapping_source_timebase",
        "mapping_target_timebase",
    ):
        result = copy.deepcopy(RATIONAL)
    elif name in BOOL_NAMES:
        result = copy.deepcopy(BOOL)
    elif name.endswith("_scope") or name in (
        "uncertainty",
        "stop_rule",
        "attribution_rule",
        "budget_policy",
        "expiry_policy",
        "retention_policy",
    ):
        result = {"type": ["string", "array", "object", "null"], "x-shape": "named-scope-or-rule"}
    elif name.endswith("_at") or name in (
        "deadline",
        "stable_since",
        "mtime",
        "scheduled_at_utc",
        "data_mature_at",
    ):
        result = copy.deepcopy(TIME)
    elif name in INTEGER_NAMES:
        result = copy.deepcopy(INTEGER)
    elif name.endswith("_refs") or name in ("reference_asset_versions", "provenance_sources"):
        result = copy.deepcopy(REFS)
    elif name.endswith("_ref") or name in ("parent_id", "family_root_ref"):
        result = copy.deepcopy(REF)
    elif name.endswith("_version") or name in (
        "version",
        "source_revision",
        "profile_version",
        "recipe_version",
        "deployment_version",
    ):
        result = copy.deepcopy(VERSION)
    elif name in ARRAY_NAMES:
        result = copy.deepcopy(ARRAY)
    elif name in OBJECT_NAMES:
        result = copy.deepcopy(
            CONFIG
            if name
            in (
                "constraints",
                "scope",
                "guards",
                "locks",
                "resources",
                "source_versions",
                "budgets",
                "stop_conditions",
            )
            else JSON_OBJECT
        )
    elif name in NUMBER_NAMES:
        result = copy.deepcopy(NUMBER)
    elif name in OPAQUE_NAMES:
        result = {
            "type": ["object", "array", "string", "null"],
            "x-shape": "named-descriptive-payload",
            "x-semantic-validation": "adapter-or-domain-handler-required",
        }
    elif (
        name.endswith("_id")
        or name.endswith("_hash")
        or name
        in (
            "hash",
            "checksum",
            "bytes_hash",
            "content_hash",
            "seed",
            "owner",
            "principal",
            "actor",
            "environment_digest",
            "object_key",
            "relative_path",
            "relative_local_path",
            "name",
        )
    ):
        result = {"type": ["string", "integer", "null"]} if name == "seed" else copy.deepcopy(TEXT)
    elif name in (
        "goals",
        "intent",
        "audience",
        "brand_constraints",
        "soft_preferences",
        "claim",
        "hypothesis",
        "uncertainty",
        "scope",
        "constraints",
        "assumptions",
    ):
        result = {
            "type": ["string", "object", "array", "null"],
            "x-shape": "structured-or-human-authored-statement",
        }
    else:
        result = copy.deepcopy(TEXT)
    if isinstance(description, dict) and description.get("type") in (
        "array",
        "object",
        "boolean",
        "integer",
        "number",
        "string",
    ):
        # Preserve source top-level shape. Descriptive child fields are refined by
        # operation validators; do not pretend source prose is JSON Schema.
        result = {"type": [description["type"], "null"]}
        if description["type"] == "array":
            result["items"] = {"type": ["object", "string", "number"]}
    result["x-validation-level"] = "engineering-structural"
    return result
