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
        for pointer in [ref(item), *item["refs"]]:
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
                    AND (idempotency_key LIKE 'post-level-review:%' OR idempotency_key LIKE 'retention-mapping:%')
                    AND json_extract(response_json,'$.insight.id')=?
                    AND json_extract(response_json,'$.insight.hash')=? LIMIT 1""",
                    (actor.tenant_id, item["id"], item["hash"]),
                ).fetchone()
            if not verified:
                raise DomainError(
                    "FEEDBACK_PROVENANCE", "请选择由帖子复盘或留存映射生成的分析。", 409
                )
            excerpt = {
                k: data[k]
                for k in (
                    "name",
                    "scope",
                    "difference",
                    "observations",
                    "uncertainty",
                    "retention_summary",
                )
                if k in data
            }
        serialized = canonical(excerpt).encode("utf-8")
        # Keep provenance and mark the excerpt boundary. Never send the entire raw ledger.
        result.append(
            {
                "source": ref(item),
                "content_hash": item["hash"],
                "data_excerpt": serialized[:1800].decode("utf-8", errors="ignore"),
                "truncated": len(serialized) > 1800,
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
