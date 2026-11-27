"""Reviewable semantic families from versioned local CLIP frame embeddings."""

import hashlib
from itertools import combinations
import math
from statistics import fmean
import struct

from . import frame_vectors
from .asset_lifecycle import current_pin
from .auth import actor_still_authorized
from .errors import DomainError
from .media import resolve_media
from .production import ref
from .selection import ChosenVersion

METHOD = "clip_bidirectional_frame_coverage_complete_link_v1"
_VECTOR = struct.Struct("<512f")


def model_space():
    model = frame_vectors.local_model()
    if not model:
        raise DomainError(
            "CLIP_UNAVAILABLE", "请配置本地 CLIP 模型；也可在素材族中选用人工语义类目。", 503
        )
    return model[0]


def snapshot(store, actor, pid, chosen, space):
    if len({pin.id for pin in chosen}) != len(chosen):
        raise DomainError("FAMILY_MEMBERS", "请选择不同的素材版本。")
    if not actor_still_authorized(actor):
        raise DomainError("IDENTITY_REVOKED", "工作区授权已变化。", 403)
    evidence, vectors = {}, {}
    with store.read_snapshot() as db:
        for pin in sorted(chosen, key=lambda p: p.id):
            item = current_pin(actor, pid, pin)
            resolve_media(store, actor, pin.id, project_id=pid, purpose="process")
            rows = db.execute(
                "SELECT pts_us,rgb_sha256,embedding FROM media_frame_vectors WHERE "
                "tenant=? AND project=? AND space=? AND version_id=? AND pin=? ORDER BY pts_us LIMIT 4",
                (actor.tenant_id, pid, space, pin.id, item["hash"]),
            ).fetchall()
            if not rows:
                raise DomainError(
                    "CLIP_INDEX_REQUIRED",
                    f"素材 {item['data'].get('filename') or pin.id} 缺少当前模型与版本的视觉索引，请先建立画面索引。",
                    409,
                )
            if len(rows) > 3:
                raise DomainError("CLIP_INDEX_INVALID", "采样帧超出当前视觉模型的索引规格。", 409)
            frames, values = [], []
            for pts, rgb, blob in rows:
                vector = _VECTOR.unpack(blob) if len(blob) == _VECTOR.size else ()
                norm = math.sqrt(sum(x * x for x in vector))
                if not math.isfinite(norm) or abs(norm - 1) > 0.001:
                    raise DomainError(
                        "CLIP_INDEX_INVALID", "视觉索引向量损坏，请修复素材索引。", 409
                    )
                frames.append(
                    {
                        "pts_us": pts,
                        "rgb_sha256": rgb,
                        "embedding_sha256": hashlib.sha256(blob).hexdigest(),
                    }
                )
                values.append(tuple(x / norm for x in vector))
            evidence[pin.id] = {
                "asset": ref(item),
                "revision_sha256": item["hash"],
                "frames": frames,
            }
            vectors[pin.id] = values
    return evidence, vectors


def complete_link(ids, scores, threshold):
    """Greedy complete-link with deterministic tie breaks over stable sorted member IDs."""
    groups = [(i,) for i in sorted(ids)]
    while True:
        best = None
        for left, right in combinations(groups, 2):
            score = min(scores[tuple(sorted((a, b)))] for a in left for b in right)
            candidate = (-score, left, right)
            if score >= threshold and (best is None or candidate < best):
                best = candidate
        if best is None:
            return sorted(groups)
        _, left, right = best
        groups.remove(left)
        groups.remove(right)
        groups.append(tuple(sorted(left + right)))
        groups.sort()


def suggestions(evidence, vectors, threshold):
    scores, pairs = {}, {}
    for a, b in combinations(sorted(evidence), 2):
        left, right = vectors[a], vectors[b]
        matrix = [[max(-1, min(1, sum(x * y for x, y in zip(u, v)))) for v in right] for u in left]
        coverage = min(fmean(max(row) for row in matrix), fmean(max(col) for col in zip(*matrix)))
        scores[a, b] = coverage
        i, j = max(
            ((i, j) for i in range(len(left)) for j in range(len(right))),
            key=lambda ij: matrix[ij[0]][ij[1]],
        )
        pairs[a, b] = {
            "left": a,
            "right": b,
            "score": coverage,
            "left_pts_us": evidence[a]["frames"][i]["pts_us"],
            "right_pts_us": evidence[b]["frames"][j]["pts_us"],
            "best_frame_cosine": matrix[i][j],
        }
    groups, singles = [], []
    for members in complete_link(evidence, scores, threshold):
        if len(members) < 2:
            singles.extend(members)
        else:
            matches = [pairs[key] for key in combinations(members, 2)]
            groups.append(
                {
                    "members": list(members),
                    "minimum_score": min(p["score"] for p in matches),
                    "pairs": matches,
                }
            )
    return {"groups": groups, "unmatched": singles}


def review(store, actor, pid, audit, members, space):
    d = audit["data"]
    with store.connection() as db:
        issued = db.execute(
            "SELECT json_extract(response_json,'$.audit.hash') FROM command_results "
            "WHERE tenant_id=? AND idempotency_key=?",
            (actor.tenant_id, "asset-family-suggest:" + str(d.get("request_id", ""))),
        ).fetchone()
    if not issued or issued[0] != audit["hash"] or d.get("algorithm") != METHOD:
        raise DomainError("CLIP_AUDIT_REQUIRED", "请选择由视觉聚类生成的原始复核记录。", 409)
    if d["space"] != space:
        raise DomainError("CLIP_SPACE_CHANGED", "视觉模型已变化，请重新索引并生成候选。", 409)
    all_pins = [
        ChosenVersion(id=k, version=v["asset"]["version"]) for k, v in d["evidence"].items()
    ]
    current, _ = snapshot(store, actor, pid, all_pins, space)
    if current != d["evidence"]:
        raise DomainError("CLIP_EVIDENCE_CHANGED", "采样帧或向量已变化，请重新生成候选。", 409)
    selected = sorted(m.id for m in members)
    matching = next((g for g in d["groups"] if g["members"] == selected), None)
    if not matching or any(m.version != current[m.id]["asset"]["version"] for m in members):
        raise DomainError("CLIP_GROUP_MISMATCH", "复核成员及版本须与同一个建议族完全一致。", 409)
    return {
        "algorithm": METHOD,
        "space": space,
        "threshold": d["threshold"],
        "similarity": matching["minimum_score"],
        "pairs": matching["pairs"],
        "evidence_id": audit["id"],
        "frames": [current[i] for i in selected],
    }
