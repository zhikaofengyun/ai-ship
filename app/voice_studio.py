"""VoiceMap planning, human observations and checked clip use (Atlas V05–V09)."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import hashlib
import re
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, model_validator

from .api import ok
from .auth import Actor, current_actor, require_approval, require_write
from .errors import DomainError
from .media import frame_png, probe_media, resolve_media
from .production import current_dependency_problem, record, ref, revise, save
from .selection import ChosenVersion, Label, Note, SelectionBody, live, on_request
from .store import get_store
from .voice_rhythm import geometry

router = APIRouter(prefix="/production/voice-studio", tags=["voice-studio"])
VERSION = "voice-evidence-1"


class Operation(SelectionBody):
    project_id: Label
    request_id: Label


class Meaning(SelectionBody):
    subject: Label
    product: ChosenVersion | None = None
    quantity: int | None = Field(default=None, ge=1, le=1000000, strict=True)
    unit: Label | None = None
    action: Label | None = None

    @model_validator(mode="after")
    def quantity_and_unit(self):
        if (self.quantity is None) != (self.unit is None):
            raise ValueError("数量和单位必须同时填写")
        return self


class Cue(SelectionBody):
    key: Label
    words: Note
    meaning: Meaning
    start: Decimal | None = Field(default=None, ge=0, le=3600, decimal_places=6)
    end: Decimal | None = Field(default=None, gt=0, le=3600, decimal_places=6)
    heard: bool = False
    price_caption: bool = False


class MapInput(Operation):
    name: Label
    audio: ChosenVersion | None = None
    reviewed_audio: ChosenVersion | None = None
    script: ChosenVersion | None = None
    previous: ChosenVersion | None = None
    cues: list[Cue] = Field(min_length=1, max_length=64)


class Observation(Operation):
    media: ChosenVersion
    meaning: Meaning
    start: Decimal = Field(ge=0, le=3600, decimal_places=6)
    end: Decimal = Field(gt=0, le=3600, decimal_places=6)
    seen_at: Decimal = Field(ge=0, le=3600, decimal_places=6)
    visual_note: Note


class PlanInput(Operation):
    voice_map: ChosenVersion
    observations: list[ChosenVersion] = Field(max_length=64)
    previous: ChosenVersion | None = None
    locked: dict[Label, ChosenVersion] = Field(default_factory=dict, max_length=64)
    unlock: list[Label] = Field(default_factory=list, max_length=64)


class Adopt(Operation):
    plan: ChosenVersion


class Repair(Adopt):
    anchor: Label
    action: Literal["generation", "rewrite"]
    instruction: Note


def pin(actor, project, value, kind):
    return live(
        actor,
        ChosenVersion.model_validate({"id": value["id"], "version": value["version"]}),
        kind,
        project,
    )


def receipt(actor, item, operation):
    return save(
        actor,
        "Audit",
        item["project_id"],
        {
            "action": operation,
            "name": "口播制作证据",
            "voice_subject": item["id"],
            "voice_hash": item["hash"],
            "voice_version": item["version"],
            "voice_protocol": VERSION,
        },
        [ref(item)],
    )


def check_receipt(item, action):
    # Audit records cannot be created through the generic record interface. A copied
    # or externally edited Tag, VoiceMap or Plan does not inherit a verified result.
    with get_store().connection() as conn:
        proof = conn.execute(
            """
            SELECT 1 FROM records r JOIN revisions v ON v.record_id=r.id AND v.tenant_id=r.tenant_id
            AND v.version=r.latest_version WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Audit'
            AND json_extract(v.data_json,'$.action')=? AND json_extract(v.data_json,'$.voice_subject')=?
            AND json_extract(v.data_json,'$.voice_hash')=? AND json_extract(v.data_json,'$.voice_protocol')=?
            LIMIT 1""",
            (item["tenant_id"], item["project_id"], action, item["id"], item["hash"], VERSION),
        ).fetchone()
    if proof is None:
        raise DomainError("VOICE_REVIEW_REQUIRED", "当前版本缺少对应的口播或画面审阅记录。", 409)


def checked_media(actor, project, chosen, kind):
    media, blob, path = resolve_media(get_store(), actor, chosen.id, project_id=project)
    if media["kind"] != kind or media["version"] != chosen.version:
        raise DomainError("VOICE_MEDIA_CHANGED", "请重新选择当前项目内的媒体版本。", 409)
    return media, blob["data"]["checksum"], path


def audio_clock(actor, project, chosen):
    audio, checksum, path = checked_media(actor, project, chosen, "Audio")
    stream = next((s for s in probe_media(path)["streams"] if s["codec_type"] == "audio"), {})
    rate = int(stream.get("sample_rate") or 0)
    duration = Decimal(str(stream.get("duration") or 0))
    if rate < 1 or duration <= 0:
        raise DomainError("VOICE_CLOCK_MISSING", "音频流缺少可核实的时长或采样率。")
    return audio, {
        "sha256": checksum,
        "sample_rate": rate,
        "samples": sample(duration, rate),
        "origin": "playable_audio_start",
    }


def sample(seconds, rate):
    return int((seconds * rate).to_integral_value(rounding=ROUND_HALF_UP))


def checked_meaning(actor, project, meaning):
    facts = meaning.model_dump(mode="json")
    product = None
    if meaning.product:
        product = live(actor, meaning.product, "Product", project)
        data = product["data"]
        if not data.get("SKU") or not (data.get("evidence") or data.get("evidence_source_id")):
            raise DomainError("VOICE_PRODUCT_EVIDENCE", "SKU 主张需要商品版本与可核对的事实来源。")
        facts["product"] = ref(product)
        facts["sku"] = data["SKU"]
    return facts, product


def distinct_refs(items):
    return list({(r["id"], r["version"]): r for r in items}.values())


CURRENCY_CODES = {
    "元": "CNY",
    "人民币": "CNY",
    "￥": "CNY",
    "¥": "CNY",
    "CNY": "CNY",
    "USD": "USD",
    "美元": "USD",
    "EUR": "EUR",
    "欧元": "EUR",
    "€": "EUR",
    "GBP": "GBP",
    "英镑": "GBP",
    "£": "GBP",
}
PRICE_PREFIX = re.compile(
    r"(人民币|CNY|USD|EUR|GBP|[￥¥€£])\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE
)
PRICE_SUFFIX = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(人民币|美元|欧元|英镑|元|CNY|USD|EUR|GBP)", re.IGNORECASE
)


def verify_explicit_price(words, product):
    tokens = [(currency, amount) for currency, amount in PRICE_PREFIX.findall(words)]
    tokens.extend((currency, amount) for amount, currency in PRICE_SUFFIX.findall(words))
    for currency, amount in tokens:
        if (
            product.get("price") is None
            or Decimal(str(product["price"])) != Decimal(amount)
            or product.get("currency") != CURRENCY_CODES[currency.upper()]
        ):
            raise DomainError(
                "VOICE_PRICE_CONFLICT", "台词中的明确价格或币种与冻结的商品事实不一致。"
            )


@router.post("/maps")
def make_map(body: MapInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def execute():
        project = body.project_id
        previous = live(actor, body.previous, "VoiceMap", project) if body.previous else None
        if previous:
            check_receipt(previous, "voice.cues")
        audio, clock = audio_clock(actor, project, body.audio) if body.audio else (None, None)
        if (
            previous
            and previous["data"].get("clock") != clock
            and any(c.heard for c in body.cues)
            and (body.reviewed_audio is None or body.reviewed_audio != body.audio)
        ):
            raise DomainError(
                "VOICE_REHEAR_REQUIRED", "声音内容变化，须重新试听并确认新音频版本。", 409
            )
        script = live(actor, body.script, "Script", project) if body.script else None
        refs = [ref(audio)] if audio else []
        keys = set()
        result, previous_end, cursor = [], 0, 0
        for cue in body.cues:
            if cue.key in keys:
                raise DomainError("VOICE_DUPLICATE_ANCHOR", "每个锚点需要独立的标记。")
            keys.add(cue.key)
            facts, product = checked_meaning(actor, project, cue.meaning)
            price_text = None
            if product:
                refs.append(ref(product))
            if cue.price_caption:
                pd = product["data"] if product else {}
                if pd.get("price") is None or not pd.get("currency"):
                    raise DomainError("VOICE_PRICE_EVIDENCE", "价格文字层需要当前商品价格和币种。")
                price_text = f"{pd['currency']} {pd['price']}"
            if product:
                verify_explicit_price(cue.words, product["data"])
            if script:
                pos = script["data"]["text"].find(cue.words, cursor)
                if pos < 0:
                    raise DomainError("VOICE_SCRIPT_CONFLICT", "口播词句须按所选脚本的顺序出现。")
                cursor = pos + len(cue.words)
            if (cue.start is None) != (cue.end is None) or (cue.heard and cue.start is None):
                raise DomainError("VOICE_RANGE_REQUIRED", "请填写完整的起止时间后确认试听。")
            span = None
            if cue.start is not None:
                if not clock:
                    raise DomainError("VOICE_AUDIO_REQUIRED", "精确时间锚点需要真实音轨。")
                span = [sample(x, clock["sample_rate"]) for x in (cue.start, cue.end)]
                if not previous_end <= span[0] < span[1] <= clock["samples"]:
                    raise DomainError(
                        "VOICE_RANGE_INVALID", "音频采样区间重叠、为空或超出实际音轨。"
                    )
                previous_end = span[1]
            result.append(
                {
                    "anchor_id": cue.key,
                    "token": cue.words,
                    "semantic": facts,
                    "sample_range": span,
                    "timing_status": (
                        "human_verified" if cue.heard else ("estimated" if span else "untimed")
                    ),
                    "price_caption": price_text,
                }
            )
        if not script:
            script = save(
                actor,
                "Script",
                project,
                {
                    "name": body.name,
                    "language": record(actor, project, "Project")["data"].get("language", "zh-CN"),
                    "text": "\n".join(c.words for c in body.cues),
                    "claims": [
                        {
                            "anchor": c["anchor_id"],
                            "meaning": c["semantic"],
                            "price_caption": c["price_caption"],
                        }
                        for c in result
                    ],
                },
                distinct_refs([r for r in refs if r["kind"] == "Product"]),
            )
        refs.append(ref(script))
        old = {a["anchor_id"]: a for a in previous["data"]["anchors"]} if previous else {}
        audio_changed = bool(previous and previous["data"].get("clock") != clock)
        changed = sorted(
            {a["anchor_id"] for a in result if audio_changed or old.get(a["anchor_id"]) != a}
            | (set(old) - keys)
        )
        data = {
            "name": body.name,
            "audio_version": ref(audio) if audio else None,
            "audio_hash": clock["sha256"] if clock else None,
            "script_version": ref(script),
            "clock": clock,
            "anchors": result,
            "changed_anchors": changed,
            "audio_changed": audio_changed,
            "validation_status": (
                "human_timed"
                if all(a["timing_status"] == "human_verified" for a in result)
                else "draft"
            ),
        }
        result_map = (
            revise(actor, previous, data, "修订口播锚点", distinct_refs(refs))
            if previous
            else save(actor, "VoiceMap", project, data, distinct_refs(refs))
        )
        receipt(actor, result_map, "voice.cues")
        return {"map": result_map, "script": script}

    return ok(on_request("voice-map", body, actor, execute), request)


@router.post("/observations")
def observe(body: Observation, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def execute():
        media, checksum, path = checked_media(actor, body.project_id, body.media, "AssetVersion")
        probe = probe_media(path)
        if (
            not probe["has_video"]
            or probe["is_still"]
            or not body.start <= body.seen_at < body.end <= Decimal(str(probe["duration"]))
        ):
            raise DomainError("VOICE_SHOT_RANGE", "选定时刻必须位于实际视频的可用半开区间内。")
        facts, product = checked_meaning(actor, body.project_id, body.meaning)
        image = frame_png(get_store(), actor, media["id"], float(body.seen_at), 320)
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DomainError("VOICE_FRAME_MISSING", "此时刻没有可解码的画面。")
        shot = save(
            actor,
            "Shot",
            body.project_id,
            {
                "name": facts["subject"],
                "asset_version_id": media["id"],
                "source_in": float(body.start),
                "source_out": float(body.end),
                "frame_rate": probe.get("fps"),
            },
            [ref(media)],
        )
        tag = save(
            actor,
            "Tag",
            body.project_id,
            {
                "name": facts["subject"],
                "shot_id": shot["id"],
                "visual_facts": facts,
                "media": ref(media),
                "shot": ref(shot),
                "source_range": [float(body.start), float(body.end)],
                "sample_time": float(body.seen_at),
                "frame_sha256": hashlib.sha256(image).hexdigest(),
                "source_sha256": checksum,
                "review_note": body.visual_note,
                "reviewer": actor.actor_id,
                "method": "human_range_review",
            },
            [ref(shot), ref(media), *([ref(product)] if product else [])],
        )
        receipt(actor, tag, "voice.observed")
        return {"observation": tag, "shot": shot}

    return ok(on_request("voice-observe", body, actor, execute), request)


def verify_map(actor, project, chosen):
    vm = live(actor, chosen, "VoiceMap", project)
    check_receipt(vm, "voice.cues")
    for r in vm["refs"]:
        pin(actor, project, r, r["kind"])
    clock = vm["data"].get("clock")
    if clock:
        audio = vm["data"]["audio_version"]
        _, current = audio_clock(
            actor, project, ChosenVersion(id=audio["id"], version=audio["version"])
        )
        if current != clock:
            raise DomainError("VOICE_CLOCK_CHANGED", "音频内容或时钟变化，请重新试听打点。", 409)
    return vm


def read_observation(actor, project, choice):
    tag = live(actor, choice, "Tag", project)
    check_receipt(tag, "voice.observed")
    for r in tag["refs"]:
        pin(actor, project, r, r["kind"])
    media = tag["data"]["media"]
    _, checksum, _ = checked_media(
        actor, project, ChosenVersion(id=media["id"], version=media["version"]), "AssetVersion"
    )
    if checksum != tag["data"]["source_sha256"]:
        raise DomainError("VOICE_SOURCE_CHANGED", "已观察画面的媒体内容发生变化。", 409)
    return tag


def compare(anchor, tag, duration):
    fields = anchor["semantic"]
    observed = tag["data"]["visual_facts"]
    reasons = [
        key
        for key in ("subject", "product", "quantity", "unit", "action")
        if fields.get(key) is not None and fields[key] != observed.get(key)
    ]
    interval = tag["data"]["source_range"]
    if duration is not None and interval[1] - interval[0] + 1e-6 < duration:
        reasons.append("duration")
    return reasons


@router.post("/plans")
def make_plan(body: PlanInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def execute():
        vm = verify_map(actor, body.project_id, body.voice_map)
        previous = live(actor, body.previous, "Plan", body.project_id) if body.previous else None
        prior_slots = {}
        if previous:
            check_receipt(previous, "voice.plan")
            if previous["data"]["voice_map_version"]["id"] != vm["id"]:
                raise DomainError("VOICE_MAP_MISMATCH", "已有方案使用另一份口播。", 409)
            prior_slots = {s["anchor"]: s for s in previous["data"]["slots"]}
        anchors = vm["data"]["anchors"]
        ids = {a["anchor_id"] for a in anchors}
        protected_keys = {key for key, s in prior_slots.items() if s["locked"]}
        if (
            set(body.locked) - ids
            or set(body.unlock) - protected_keys
            or protected_keys - ids - set(body.unlock)
        ):
            raise DomainError(
                "VOICE_LOCK_CONFLICT", "存在未知或被删除的锁定锚点，请先明确解锁。", 409
            )
        tags = [read_observation(actor, body.project_id, chosen) for chosen in body.observations]
        by_id = {t["id"]: t for t in tags}
        if len(tags) != len(by_id):
            raise DomainError("VOICE_DUPLICATE_OBSERVATION", "候选观察不可重复。")
        slots, clock = [], vm["data"].get("clock")
        for a in anchors:
            key = a["anchor_id"]
            span = a["sample_range"]
            duration = (span[1] - span[0]) / clock["sample_rate"] if span and clock else None
            selected = None
            lock = body.locked.get(key)
            existing = prior_slots.get(key)
            if existing and existing["locked"] and key not in body.unlock:
                kept = existing["selected"]
                if lock and (lock.id, lock.version) != (kept["id"], kept["version"]):
                    raise DomainError("VOICE_LOCK_CONFLICT", "请先明确解锁原有选取。", 409)
                lock = ChosenVersion(id=kept["id"], version=kept["version"])
            candidates = [{"tag": ref(t), "differences": compare(a, t, duration)} for t in tags]
            accepted = [c["tag"] for c in candidates if not c["differences"]]
            if lock:
                selected = next(
                    (t for t in accepted if (t["id"], t["version"]) == (lock.id, lock.version)),
                    None,
                )
                if not selected:
                    raise DomainError(
                        "VOICE_LOCK_EVIDENCE", "锁定候选不可用或与主张冲突，须明确解锁或补证。", 409
                    )
            elif accepted:
                selected = accepted[0]
            slots.append(
                {
                    "anchor": key,
                    "token": a["token"],
                    "candidates": candidates,
                    "selected": selected,
                    "locked": bool(lock),
                    "time_ok": a["timing_status"] == "human_verified",
                    "semantic_ok": bool(selected),
                    "sample_range": span,
                    "price_caption": a["price_caption"],
                }
            )
        ready = all(s["semantic_ok"] and s["time_ok"] for s in slots)
        data = {
            "name": vm["data"]["name"],
            "voice_map_version": ref(vm),
            "rhythm": None,
            "rhythm_mix": None,
            "slots": slots,
            "status": "ready_for_review" if ready else "draft",
        }
        refs = [ref(vm), *[ref(t) for t in tags]]
        plan = (
            revise(actor, previous, data, "重排已核实镜头并保留锁定", refs)
            if previous
            else save(actor, "Plan", body.project_id, data, refs)
        )
        receipt(actor, plan, "voice.plan")
        return {"plan": plan}

    return ok(on_request("voice-plan", body, actor, execute), request)


def load_review(actor, project, choice):
    plan = live(actor, choice, "Plan", project)
    check_receipt(plan, "voice.plan")
    vm = plan["data"]["voice_map_version"]
    if plan["data"].get("rhythm_mix"):
        mix = pin(actor, project, plan["data"]["rhythm_mix"], "Mix")
        if music := mix["data"].get("music"):
            current = pin(actor, project, music, "Music")
            audio = current["data"]["audio_ref"]
            audio_clock(actor, project, ChosenVersion(id=audio["id"], version=audio["version"]))
    return plan, verify_map(actor, project, ChosenVersion(id=vm["id"], version=vm["version"]))


@router.post("/adopt")
def adopt(body: Adopt, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def execute():
        project = body.project_id
        plan, vm = load_review(actor, project, body.plan)
        clock = vm["data"]["clock"]
        if not clock or plan["data"]["status"] != "ready_for_review":
            raise DomainError("VOICE_INCOMPLETE", "仍有未核实的发音时间或镜头缺口。", 409)
        audio = vm["data"]["audio_version"]
        anchors = {a["anchor_id"]: a for a in vm["data"]["anchors"]}
        clips, captions, refs, used, selection_cache = [], [], [ref(plan), ref(vm), audio], [], {}
        for index, slot in enumerate(plan["data"]["slots"]):
            a = anchors[slot["anchor"]]
            chosen = slot["selected"]
            if chosen["id"] not in selection_cache:
                selection_cache[chosen["id"]] = read_observation(
                    actor, project, ChosenVersion(id=chosen["id"], version=chosen["version"])
                )
            tag = selection_cache[chosen["id"]]
            start, end = [
                sample_position / clock["sample_rate"] for sample_position in a["sample_range"]
            ]
            duration = end - start
            if compare(a, tag, duration) or duration < 0.04:
                raise DomainError(
                    "VOICE_CLIP_UNUSABLE", "声画主张无法核实或锚点短于当前渲染最小片段。", 409
                )
            visual = tag["data"]
            rate = clock["sample_rate"]
            _, _, source_in, source_out, _, _ = geometry(slot, tag, rate)
            padding = (
                (plan["data"].get("rhythm") or {})
                .get("padding_samples", {})
                .get(slot["anchor"], [0, 0])
            )
            lead, tail = padding
            source_start = (source_in - lead) / rate
            source_end = (source_out + tail) / rate
            clip_start = start - lead / rate
            clip = {
                "id": f"voice-{index+1}",
                "name": slot["token"],
                "asset_id": visual["media"]["id"],
                "source_in": source_start,
                "source_out": source_end,
                "timeline_start": clip_start,
                "track": 0,
                "speed": 1,
                "volume": 0,
            }
            clips.append(clip)
            refs.extend([ref(tag), visual["media"], visual["shot"]])
            if a["price_caption"]:
                captions.append({"start": start, "end": end, "text": a["price_caption"]})
            used.append((slot, a, tag, clip, [clip_start, end + tail / rate]))
        cfg = record(actor, project, "Project")["data"]
        timeline = save(
            actor,
            "Timeline",
            project,
            {
                "name": vm["data"]["name"],
                "status": "draft",
                "locked": False,
                "voice_alignment_plan": ref(plan),
                "format": {
                    "width": cfg.get("width", 1280),
                    "height": cfg.get("height", 720),
                    "fps": 25,
                },
                "clips": clips,
                "captions": captions,
                "audio_tracks": [
                    {
                        "id": "narration",
                        "name": "口播",
                        "asset_id": audio["id"],
                        "source_in": 0,
                        "source_out": clock["samples"] / clock["sample_rate"],
                        "timeline_start": 0,
                        "track": 0,
                        "speed": 1,
                        "volume": 1,
                    }
                ],
            },
            distinct_refs(refs),
        )
        bindings = []
        for slot, a, tag, clip, output in used:
            selected = tag["data"]
            data = {
                "name": a["token"],
                "voice_map_ref": ref(vm),
                "script_ref": vm["data"]["script_version"],
                "audio_ref": audio,
                "anchor_ids": [slot["anchor"]],
                "semantic": a["semantic"],
                "audio_sample_range": a["sample_range"],
                "audio_timebase": {"num": 1, "den": clock["sample_rate"]},
                "validation_status": "human_verified_clip_use",
                "source_evidence": {
                    "tag": ref(tag),
                    "frame_sha256": selected["frame_sha256"],
                    "at": selected["sample_time"],
                    "source_range": [clip["source_in"], clip["source_out"]],
                    "audio_sha256": clock["sha256"],
                },
                "selected_shot_refs": [selected["shot"]],
                "plan_ref": ref(plan),
                "timeline_ref": ref(timeline),
                "clip_use_ids": [clip["id"]],
                "output_ranges": [output],
            }
            bindings.append(
                save(
                    actor,
                    "AlignmentBinding",
                    project,
                    data,
                    [ref(vm), audio, ref(tag), ref(plan), ref(timeline), selected["shot"]],
                )
            )
        receipt(actor, timeline, "voice.adopt")
        return {"timeline": timeline, "bindings": bindings}

    return ok(on_request("voice-adopt", body, actor, execute), request)


@router.post("/repair")
def repair(body: Repair, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def execute():
        p, vm = load_review(actor, body.project_id, body.plan)
        slot = next((s for s in p["data"]["slots"] if s["anchor"] == body.anchor), None)
        if not slot or slot["semantic_ok"]:
            raise DomainError("VOICE_GAP_REQUIRED", "请先选择没有合格候选的缺口。", 409)
        anchor = next(a for a in vm["data"]["anchors"] if a["anchor_id"] == body.anchor)
        if body.action == "generation":
            prompt = f"{anchor['token']}\n{body.instruction}"
            task = save(
                actor,
                "TaskContract",
                body.project_id,
                {
                    "name": "缺镜补材",
                    "status": "draft",
                    "feature_id": "V06",
                    "goal": prompt,
                    "success_criteria": [
                        "成片素材入库且版本可核验",
                        "复核画面主体、数量、动作及源区间后重排",
                    ],
                    "required_outputs": ["AssetVersion"],
                    "input_bindings": {
                        "anchor": body.anchor,
                        "claim": anchor["semantic"],
                        "voice_map": ref(vm),
                        "plan": ref(p),
                    },
                },
                [ref(vm), ref(p)],
            )
            return {
                "task": task,
                "generation_request": {
                    "project_id": body.project_id,
                    "slot": body.anchor[:160],
                    "prompt": prompt,
                    "idempotency_key": "voice-gap-" + task["id"],
                },
            }
        original = pin(actor, body.project_id, vm["data"]["script_version"], "Script")
        text = original["data"]["text"]
        cursor = 0
        for cue in vm["data"]["anchors"]:
            pos = text.find(cue["token"], cursor)
            if pos < 0:
                raise DomainError("VOICE_SCRIPT_CONFLICT", "台词锚点与原稿不一致。", 409)
            if cue["anchor_id"] == body.anchor:
                break
            cursor = pos + len(cue["token"])
        new = text[:pos] + body.instruction + text[pos + len(anchor["token"]) :]
        # Fork the draft. The old script/map and other locked bindings remain usable.
        draft = save(
            actor,
            "Script",
            body.project_id,
            {
                "name": original["data"].get("name", "口播") + " · 改稿",
                "language": original["data"].get("language", "zh-CN"),
                "text": new,
                "status": "draft",
                "changed_anchor": body.anchor,
                "source_script": ref(original),
                "requires_new_audio": True,
                "claims_to_review": [body.anchor],
            },
            [ref(original), ref(p), ref(vm)],
        )
        return {"script": draft}

    return ok(on_request("voice-repair", body, actor, execute), request)


def verify_timeline_alignment(actor, first_revision, timeline):
    """Revalidate the frozen binding against source versions and the actual edited clip ranges."""
    origin = first_revision["data"]
    check_receipt(first_revision, "voice.adopt")
    plan_pin = origin["voice_alignment_plan"]
    plan, _ = load_review(
        actor,
        first_revision["project_id"],
        ChosenVersion(id=plan_pin["id"], version=plan_pin["version"]),
    )
    for chosen in {
        s["selected"]["id"]: (s["selected"]["id"], s["selected"]["version"])
        for s in plan["data"]["slots"]
    }.values():
        read_observation(
            actor, first_revision["project_id"], ChosenVersion(id=chosen[0], version=chosen[1])
        )
    data = timeline["data"]
    observed = {clip["id"]: clip for clip in origin["clips"]}
    if {c.get("id") for c in data.get("clips", [])} != set(observed) or len(
        data.get("clips", [])
    ) != len(observed):
        raise DomainError("VOICE_TIMELINE_REVIEW", "镜头构成发生变化，请重新核对声画绑定。", 409)

    def clip_shape(clip):
        return (
            clip.get("id"),
            clip.get("asset_id"),
            clip.get("asset_version_id"),
            clip.get("source_in"),
            clip.get("source_out"),
            clip.get("timeline_start", 0),
            clip.get("speed", 1),
            clip.get("track", 0),
            clip.get("volume", 1),
            clip.get("opacity", 1),
            clip.get("x", 0),
            clip.get("y", 0),
            clip.get("width"),
            clip.get("height"),
            clip.get("fit", "contain"),
            clip.get("transition_in") or None,
            clip.get("keyframes") or None,
        )

    if any(
        clip.get("id") not in observed
        or clip_shape(clip) != clip_shape(observed[clip["id"]])
        or clip.get("transition_in")
        or clip.get("keyframes")
        for clip in data["clips"]
    ):
        raise DomainError(
            "VOICE_TIMELINE_REVIEW", "所用画面、原片区间或输出时钟变化，请重核相应锚点。", 409
        )
    if list(map(clip_shape, data.get("audio_tracks", []))) != list(
        map(clip_shape, origin["audio_tracks"])
    ):
        raise DomainError("VOICE_AUDIO_REVIEW", "声音来源、音量或播放范围变化，请重新试听。", 409)
    for caption in origin["captions"]:
        if not any(
            all(c.get(k) == value for k, value in caption.items()) for c in data.get("captions", [])
        ):
            raise DomainError("VOICE_PRICE_REVIEW", "绑定的价格文字或显示时间已改变。", 409)


class RhythmInput(Adopt):
    mode: Literal["beat", "semantic", "manual"]
    music: ChosenVersion | None = None
    marker_types: list[Literal["beat", "downbeat", "section"]] = Field(
        default_factory=lambda: ["downbeat", "beat"], max_length=3
    )
    tolerance_ms: int = Field(default=120, ge=0, le=2000, strict=True)
    manual_cuts: dict[Label, Decimal] = Field(default_factory=dict, max_length=63)


@router.post("/rhythm")
def resolve_rhythm(body: RhythmInput, request: Request, actor: Actor = Depends(current_actor)):
    from .voice_rhythm import position, solve

    require_write(actor)

    def execute():
        p, vm = load_review(actor, body.project_id, body.plan)
        slots = p["data"]["slots"]
        clock = vm["data"].get("clock")
        if (
            not clock
            or len(slots) < 2
            or any(not s["semantic_ok"] or not s["time_ok"] for s in slots)
        ):
            raise DomainError(
                "VOICE_RHYTHM_INCOMPLETE", "至少两段已核对的声音与画面才能调整剪点。", 409
            )
        if set(body.manual_cuts) - {s["anchor"] for s in slots[:-1]} or any(
            not t.is_finite() or not 0 <= t <= 3600 for t in body.manual_cuts.values()
        ):
            raise DomainError("VOICE_RHYTHM_RANGE", "人工剪点必须属于实际的相邻锚点。")
        if body.mode == "beat" and (body.music is None or not body.marker_types):
            raise DomainError("VOICE_BEATS_REQUIRED", "请先选择已试听打点的音乐与标记类型。")
        rate = clock["sample_rate"]
        music = live(actor, body.music, "Music", body.project_id) if body.music else None
        markers = []
        if music:
            md = music["data"]
            if (
                md.get("analysis_method") != "manual_marks"
                or not md.get("use_markers")
                or not md.get("audio_ref")
            ):
                raise DomainError(
                    "VOICE_BEATS_UNVERIFIED", "没有对应音轨的有效人工标记，暂不据此移动剪点。", 409
                )
            mp = md["audio_ref"]
            _, mc = audio_clock(
                actor, body.project_id, ChosenVersion(id=mp["id"], version=mp["version"])
            )
            # Older marker records can be renewed by saving their actual audio marks.
            with get_store().connection() as conn:
                proof = conn.execute(
                    """SELECT 1 FROM records r JOIN revisions v ON r.id=v.record_id
                       AND r.tenant_id=v.tenant_id AND r.latest_version=v.version
                       WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Audit'
                         AND json_extract(v.data_json,'$.action')='music.manual_markers'
                         AND json_extract(v.data_json,'$.music_hash')=?
                         AND json_extract(v.data_json,'$.music_id')=? LIMIT 1""",
                    (actor.tenant_id, body.project_id, music["hash"], music["id"]),
                ).fetchone()
            if not proof:
                raise DomainError(
                    "VOICE_BEATS_UNVERIFIED", "请在节奏标记中重新试听并保存这版音乐的标记。", 409
                )
            for mark in md["markers"]:
                if mark["type"] in body.marker_types and 0 <= mark["sample"] < mc["samples"]:
                    markers.append(
                        {
                            "sample": position(Decimal(mark["sample"]) / mc["sample_rate"], rate),
                            "type": mark["type"],
                            "music_sample": mark["sample"],
                            "basis": "human_marked",
                        }
                    )
        tags = {s["selected"]["id"]: s["selected"] for s in slots}
        resolved = {
            tid: read_observation(
                actor, body.project_id, ChosenVersion(id=tid, version=t["version"])
            )
            for tid, t in tags.items()
        }
        result = solve(
            slots,
            resolved,
            rate,
            markers,
            body.mode,
            position(Decimal(body.tolerance_ms) / 1000, rate),
            {k: position(v, rate) for k, v in body.manual_cuts.items()},
        )
        mix = save(
            actor,
            "Mix",
            body.project_id,
            {
                "name": "声画剪点与配乐提示",
                "status": "cue_sheet",
                "voice_map": ref(vm),
                "music": ref(music) if music else None,
                "rhythm_solution": result,
                "audio_track_added": False,
            },
            [ref(vm), *([ref(music), music["data"]["audio_ref"]] if music else [])],
        )
        old_reference = p["data"].get("rhythm_mix") or {}
        refs = [r for r in p["refs"] if r["id"] != old_reference.get("id")]
        plan = revise(
            actor,
            p,
            {"rhythm": result, "rhythm_mix": ref(mix)},
            "仅在已核对语音之间调整剪点",
            [*refs, ref(mix)],
        )
        receipt(actor, plan, "voice.plan")
        return {"plan": plan, "mix": mix}

    return ok(on_request("voice-rhythm", body, actor, execute), request)
