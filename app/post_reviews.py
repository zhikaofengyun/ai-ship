"""UTF-8 metric archive intake and descriptive whole-post comparisons."""

import csv
import hashlib
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import Field, ValidationError

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import (
    Body,
    MetricImport,
    MetricRow,
    current_dependency_problem,
    digest,
    record,
    ref,
    save,
    store_metric_rows,
    timestamp,
)
from .selection import ChosenVersion, Label, Note, on_request
from .store import get_store

router = APIRouter(prefix="/production/post-reviews", tags=["metric intake and post review"])


class Archive(Body):
    request_id: Label
    project_id: Label
    filename: Label
    csv_text: str = Field(min_length=1, max_length=2_000_000)


@router.post("/archive")
def import_archive(body: Archive, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        try:
            reader = csv.DictReader(
                io.StringIO(body.csv_text.removeprefix("\ufeff"), newline=""), strict=True
            )
            columns = reader.fieldnames or []
            required = {k for k, v in MetricRow.model_fields.items() if v.is_required()}
            if (
                len(set(columns)) != len(columns)
                or not required.issubset(columns)
                or set(columns) - set(MetricRow.model_fields)
            ):
                raise DomainError(
                    "CSV_COLUMNS",
                    "CSV 表头重复、缺少必需列或包含未知列。",
                    details={"required": sorted(required)},
                )
            rows = []
            for line in reader:
                if None in line or None in line.values():
                    raise DomainError(
                        "CSV_ROW", "列数与表头不一致。", details={"line": reader.line_num}
                    )
                values = {k: v if v != "" else None for k, v in line.items()}
                if values.get("paid_or_organic") is None:
                    values.pop("paid_or_organic", None)
                try:
                    rows.append(MetricRow.model_validate(values))
                except ValidationError as e:
                    raise DomainError(
                        "CSV_ROW",
                        "指标行不符合数据合同。",
                        details={
                            "line": reader.line_num,
                            "fields": [list(err["loc"]) for err in e.errors()],
                        },
                    )
                if len(rows) > 1000:
                    raise DomainError("CSV_ROWS", "单次导入上限为 1000 行。")
        except csv.Error as e:
            raise DomainError("CSV_SYNTAX", "CSV 引号或字段不完整。") from e
        if not rows:
            raise DomainError("CSV_ROWS", "CSV 没有数据行。")
        content_hash = hashlib.sha256(body.csv_text.encode("utf-8")).hexdigest()
        archive = save(
            actor,
            "Source",
            body.project_id,
            {
                "name": body.filename,
                "origin_kind": "manual_metric_csv",
                "provenance_manifest": {
                    "sha256": content_hash,
                    "rows": len(rows),
                    "encoding": "UTF-8",
                    "source_authentication": "user_supplied",
                },
            },
            reason="登记指标 CSV 的实际内容散列与行数",
        )
        result = store_metric_rows(
            MetricImport(project_id=body.project_id, rows=rows), actor, archive
        )
        return {"archive": archive, **result}

    return ok(on_request("post-metric-archive", body, actor, apply), request)


class Comparison(Body):
    project_id: Label
    request_id: Label
    baseline: ChosenVersion
    comparison: ChosenVersion
    hypothesis: Note
    counterevidence: Note
    proposed_change: Note
    experiment_method: Note
    stop_rule: Note


def signature(d):
    return tuple(
        d.get(k)
        for k in (
            "account_id",
            "metric_name",
            "unit",
            "currency",
            "counting_mode",
            "paid_or_organic",
            "source",
            "window_start",
            "window_end",
        )
    )


def snapshot(actor, project, pin):
    chosen = record(actor, pin.id, "Metric", project=project)
    if chosen["version"] != pin.version or current_dependency_problem(chosen):
        raise DomainError("STALE_METRIC", "所选指标已更新或撤回。", 409)
    data = chosen["data"]
    if (
        not data.get("post_record_id")
        or not data.get("counting_mode")
        or not data.get("intake_fingerprint")
    ):
        raise DomainError(
            "POST_METRIC_REQUIRED", "须选择经指标导入、带实际帖子与累计或区间口径的指标。"
        )
    post = record(actor, data["post_record_id"], "Post", project=project)
    account = record(actor, data["account_id"], "Account", project=project)
    if (
        current_dependency_problem(account)
        or current_dependency_problem(post)
        or (
            post["data"].get("account_id") != data["account_id"]
            or not post["data"].get("platform_post_id")
        )
    ):
        raise DomainError("POST_SCOPE", "账号或实际帖子身份无法核对。", 409)
    event_ref = next(
        (
            r
            for r in post["refs"]
            if r["kind"] == "PlatformEvent" and r["id"] == post["data"].get("correlation_evidence")
        ),
        None,
    )
    if not event_ref:
        raise DomainError("POST_EVIDENCE", "缺少与该帖子绑定的发布回执证据。", 409)
    event = record(
        actor, event_ref["id"], "PlatformEvent", version=event_ref["version"], project=project
    )
    if event["data"].get("publication_id") != post["data"].get("publication_id") or not event[
        "data"
    ].get("evidence"):
        raise DomainError("POST_EVIDENCE", "当前关联的回执与帖子不一致。", 409)
    with get_store().connection() as con:
        records = con.execute(
            """SELECT r.id,r.kind,r.project_id,r.tenant_id,v.* FROM records r
            JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Metric' AND json_extract(v.data_json,'$.post_record_id')=?
              AND json_extract(v.data_json,'$.metric_name')=?""",
            (actor.tenant_id, project, post["id"], data["metric_name"]),
        )
        related = [get_store()._row_record(row) for row in records]
    actual = [r for r in related if signature(r["data"]) == signature(data)]
    if any(current_dependency_problem(x) for x in actual):
        raise DomainError("STALE_METRIC", "该口径的指标有撤回版本，请核对来源。", 409)
    if any(
        digest({k: r["data"][k] for k in MetricRow.model_fields if k in r["data"]})
        != r["data"].get("intake_fingerprint")
        or r["data"].get("value_or_null") != r["data"].get("value")
        for r in actual
    ):
        raise DomainError("UNVERIFIED_SNAPSHOT", "该口径包含未经核对的指标修订。", 409)
    newest = max(timestamp(r["data"]["observed_at"]) for r in actual)
    latest = [r for r in actual if timestamp(r["data"]["observed_at"]) == newest]
    if len(latest) != 1:
        raise DomainError(
            "CONFLICTING_SNAPSHOTS", "同一时刻存在冲突回填，请以来源的新观测时间补录。", 409
        )
    last = latest[0]
    d = last["data"]
    if (
        d.get("value_or_null") is None
        or not d.get("data_mature_after")
        or timestamp(d["data_mature_after"]) > min(newest, datetime.now(timezone.utc))
        or timestamp(d["data_mature_after"]) < timestamp(d["window_end"])
    ):
        raise DomainError("METRIC_NOT_MATURE", "最新快照缺失或成熟时间尚未核实。", 409)
    return {
        "post": post,
        "selected": chosen,
        "latest": last,
        "observations": len(actual),
        "event": event,
    }


@router.post("/compare")
def compare(body: Comparison, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        a, b = [snapshot(actor, body.project_id, s) for s in (body.baseline, body.comparison)]
        x, y = a["latest"]["data"], b["latest"]["data"]
        if a["post"]["data"]["platform_post_id"] == b["post"]["data"][
            "platform_post_id"
        ] or signature(x) != signature(y):
            raise DomainError(
                "INCOMPARABLE_POSTS",
                "须为同账号的两个不同帖子，指标、计数方式、窗口、币种、流量与来源均一致。",
            )
        if (x.get("denominator") is None) != (y.get("denominator") is None):
            raise DomainError("INCOMPARABLE_POSTS", "两组指标的分母覆盖不一致。")
        observations = [
            {
                "post": ref(e["post"]),
                "selected": ref(e["selected"]),
                "latest": ref(e["latest"]),
                "snapshot_count": e["observations"],
                "value": e["latest"]["data"]["value_or_null"],
                "denominator": e["latest"]["data"].get("denominator"),
                "identity_basis": "recorded_receipt",
                "final_media_status": e["post"]["data"].get("final_media_status", "unknown"),
            }
            for e in (a, b)
        ]
        metric_refs = [ref(e["latest"]) for e in (a, b)]
        refs = [
            ref(item)
            for e in (a, b)
            for item in (e["post"], e["event"], e["selected"], e["latest"])
        ]
        insight = save(
            actor,
            "Insight",
            body.project_id,
            {
                "name": "同账号帖子比较 · " + x["metric_name"],
                "metric_refs": metric_refs,
                "scope": {"level": "post", "clip_attribution": "unavailable"},
                "observations": observations,
                "difference": y["value_or_null"] - x["value_or_null"],
                "causal_claim": False,
                "analysis_dataset_hash": digest(observations),
                "uncertainty": "手工回执和来源指标；未独立核验公开状态或最终媒体，整帖观测不支持片段归因。",
            },
            refs,
            "冻结两帖同口径快照并核对回填",
        )
        hypothesis = save(
            actor,
            "Hypothesis",
            body.project_id,
            {
                "name": "待检验的改版假设",
                "claim": body.hypothesis,
                "metric_snapshot_refs": metric_refs,
                "counter_evidence": [body.counterevidence],
                "proposed_change": {
                    "change": body.proposed_change,
                    "experiment_method": body.experiment_method,
                    "stop_rule": body.stop_rule,
                },
                "valid_scope": {
                    "project": body.project_id,
                    "level": "post",
                    "evidence": "human_hypothesis",
                },
                "tested": False,
            },
            [ref(insight), *metric_refs],
            "记录待验证的反证与下一次改版实验",
        )
        return {"insight": insight, "hypothesis": hypothesis}

    return ok(on_request("post-level-review", body, actor, apply), request)
