"""Bounded, version-pinned creative context for model-generated rough cuts."""

from .errors import DomainError
from .production import current_dependency_problem, record, ref
from .store import canonical, get_store


def feedback_context(actor, project, selected):
    result = []
    for pin in selected:
        item = record(actor, pin.id, project=project)
        if item["version"] != pin.version or item["kind"] not in {"Trend", "Insight"}:
            raise DomainError("FEEDBACK_VERSION", "请选择本项目当前的热点或效果分析版本。", 409)
        scope = item["data"].get("scope")
        comparison = (
            isinstance(scope, dict) and scope.get("level") == "matched_placement_hypothesis"
        )
        placement = (
            item["kind"] == "Insight"
            and isinstance(scope, dict)
            and (scope.get("level") == "placement_reuse_hypothesis" or comparison)
        )
        # The joined cohort query below checks all signed receipts and the exact current
        # Post revisions in one snapshot. Avoid an additional query per post/receipt.
        immediate = [
            link
            for link in item["refs"]
            if not placement or link["kind"] not in {"Post", "Insight"}
        ]
        for pointer in [ref(item), *immediate]:
            current = record(actor, pointer["id"], pointer["kind"], project=project)
            if current_dependency_problem(current):
                raise DomainError("FEEDBACK_WITHDRAWN", "迭代依据已过期或撤回。", 409)
            if current["version"] != pointer["version"] and current["kind"] in {
                "Source",
                "Metric",
                "Post",
            }:
                raise DomainError("FEEDBACK_VERSION", "热点来源或指标映射已变更，请重新核对。", 409)
        data = item["data"]
        if item["kind"] == "Trend":
            if not any(r["kind"] == "Source" for r in item["refs"]):
                raise DomainError("FEEDBACK_PROVENANCE", "热点缺少固定来源。", 409)
            excerpt = {
                k: data[k]
                for k in (
                    "title",
                    "platform",
                    "region",
                    "observed_at",
                    "expires_at",
                    "verification",
                    "observations",
                )
                if k in data
            }
        else:
            # A generic editable Insight must not impersonate a calculated review.
            with get_store().connection() as conn:
                verified = conn.execute(
                    """SELECT 1 FROM command_results WHERE tenant_id=?
                    AND (idempotency_key LIKE 'post-level-review:%' OR idempotency_key LIKE 'retention-mapping:%' OR idempotency_key LIKE 'placement-hypothesis:%' OR idempotency_key LIKE 'placement-comparison-hypothesis:%')
                    AND json_extract(response_json,'$.insight.id')=?
                    AND json_extract(response_json,'$.insight.hash')=? LIMIT 1""",
                    (actor.tenant_id, item["id"], item["hash"]),
                ).fetchone()
            if not verified:
                raise DomainError(
                    "FEEDBACK_PROVENANCE", "请选择由帖子复盘或留存映射生成的分析。", 409
                )
            if comparison:
                from .placement_comparisons import recheck_comparison

                recheck_comparison(actor, project, data["observation"])
            elif placement:
                from .placement_performance import recheck_hypothesis

                recheck_hypothesis(actor, project, data)
            excerpt = {
                k: data[k]
                for k in (
                    "name",
                    "placement_cohort",
                    "observation",
                    "reuse_hypothesis",
                    "scope",
                    "difference",
                    "observations",
                    "uncertainty",
                    "retention_summary",
                )
                if k in data
            }
        shortened = False
        if placement:
            obs = data["observation"]
            note = {}
            for key, value in data["reuse_hypothesis"].items():
                encoded = value.encode("utf-8")
                note[key] = encoded[:180].decode("utf-8", errors="ignore")
                shortened |= len(encoded) > 180
            if comparison:
                excerpt = {
                    "observation": {
                        k: obs[k]
                        for k in (
                            "scope",
                            "matched_days",
                            "matched_baseline",
                            "matched_candidate",
                            "difference_pp",
                            "leave_one_day_out_range_pp",
                            "evidence_hash",
                        )
                    },
                    "editor_hypothesis": note,
                    "causal_claim": False,
                }
            else:
                excerpt = {
                    "placement_cohort": data["placement_cohort"],
                    "observation": {
                        k: obs[k]
                        for k in (
                            "source_id",
                            "source_version",
                            "role",
                            "posts",
                            "mean_post_retention",
                            "multi_source_posts",
                            "evidence_hash",
                        )
                    },
                    "editor_hypothesis": note,
                    "causal_claim": False,
                }
        serialized = canonical(excerpt).encode("utf-8")
        # Keep provenance and mark the excerpt boundary. Never send the entire raw ledger.
        result.append(
            {
                "source": ref(item),
                "content_hash": item["hash"],
                "data_excerpt": serialized[:1800].decode("utf-8", errors="ignore"),
                "truncated": shortened or len(serialized) > 1800,
                "causal_claim": False,
            }
        )
    return result


def revision_context(plan):
    """Carry the last executable edit and concrete reasons for rework into the next turn."""
    if not plan:
        return None
    data = plan["data"]
    return {
        "prior_plan": ref(plan),
        "shots": [
            {key: shot[key] for key in ("asset_id", "source_in", "source_out")}
            for shot in data["shots"]
        ],
        **(
            {
                "verified_opening_options": [
                    {k: row[k] for k in ("asset_id", "asset_version", "source_in", "source_out")}
                    for row in data["opening_check"].get("alternatives", [])
                ]
            }
            if data.get("opening_check", {}).get("status") in {"conflict", "unverified"}
            else {}
        ),
        "review_blocker": data["review_blocker"],
        "reviews": [
            {
                key: r.get(key)
                for key in (
                    "role",
                    "decision",
                    "reason",
                    "evidence_id",
                    "observed_value",
                    "evidence_problem",
                )
            }
            for r in data["reviews"]
        ],
    }
