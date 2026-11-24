"""Scoped stock music, reproducible cue mixing and measurements on delivered bytes."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, model_validator

from .api import ok
from .auth import Actor, actor_still_authorized, current_actor, require_approval, require_write
from .errors import DomainError
from .media import (
    check_current_media_rights,
    normalize_clip,
    persist_blob,
    probe_media,
    resolve_media,
    run_process,
)
from .production import record, ref, revise, save, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody, live, on_request
from .store import get_store

router = APIRouter(prefix="/production/soundtrack", tags=["music"])


class Operation(SelectionBody):
    project_id: Label
    request_id: Label


class RightsReview(Operation):
    mode: Literal["local_stock", "no_music", "platform_sound"]
    name: Label
    use: Literal["commercial", "editorial", "private"]
    market: Label
    evidence: Note
    audio: ChosenVersion | None = None
    allowed_markets: list[Label] = Field(default_factory=list, max_length=100)
    allowed_uses: list[Literal["commercial", "editorial", "private"]] = Field(
        default_factory=list, max_length=3
    )
    expires_at: str | None = Field(default=None, max_length=80)
    perpetual: bool = False
    account: ChosenVersion | None = None
    sound_id: Label | None = None


class Track(SelectionBody):
    role: Literal["music", "voice", "ambience", "effect"]
    audio: ChosenVersion
    music: ChosenVersion | None = None
    source_in: float = Field(default=0, ge=0, lt=3600)
    source_out: float = Field(gt=0, le=3600)
    start: float = Field(default=0, ge=0, lt=3600)
    gain_db: float = Field(default=0, ge=-72, le=18)
    fade_in: float = Field(default=0, ge=0, le=60)
    fade_out: float = Field(default=0, ge=0, le=60)

    @model_validator(mode="after")
    def check_range(self):
        span = self.source_out - self.source_in
        if span <= 0 or self.fade_in + self.fade_out > span + 1e-7 or self.start + span > 3600:
            raise ValueError("源片起止、淡入出及输出位置必须落在有效声音区间。")
        if (self.role == "music") != bool(self.music):
            raise ValueError("音乐轨须绑定已核实的音乐许可，其他轨只绑定真实音频。")
        return self


class MixRequest(Operation):
    name: Label
    tracks: list[Track] = Field(min_length=1, max_length=16)
    ducking: bool = False
    duck_threshold_db: float = Field(default=-32, ge=-48, le=-6)
    duck_ratio: float = Field(default=6, ge=1, le=20)
    attack_ms: int = Field(default=15, ge=1, le=200)
    release_ms: int = Field(default=180, ge=20, le=2000)
    target_lufs: float | None = Field(default=None, ge=-36, le=-8)
    use: Literal["commercial", "editorial", "private"]
    market: Label
    brief: ChosenVersion | None = None
    plan: ChosenVersion | None = None


class AdoptMix(Operation):
    mix: ChosenVersion
    timeline: ChosenVersion
    original_audio: Literal["keep", "mute"]


class InspectOutput(Operation):
    artifact: ChosenVersion


def media_pin(actor, chosen, project, kind):
    row, _, path = resolve_media(get_store(), actor, chosen.id, project_id=project)
    if row["kind"] != kind or row["version"] != chosen.version:
        raise DomainError("SOUNDTRACK_INPUT_CHANGED", "请重新选取当前版本的声音文件。", 409)
    return row, path


def music_market(project, market):
    expected = project["data"].get("market")
    if expected and str(expected).casefold() != market.casefold():
        raise DomainError("MUSIC_MARKET_MISMATCH", "音乐用途与项目当前市场不一致。", 409)


@router.post("/rights")
def review_rights(body: RightsReview, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        project = record(actor, body.project_id, "Project")
        music_market(project, body.market)
        scope = {"market": body.market, "use": body.use, "mode": body.mode}
        refs = [ref(project)]
        if body.mode == "platform_sound":
            if not body.sound_id or not body.account or body.audio:
                raise DomainError(
                    "PLATFORM_SOUND_ID_REQUIRED",
                    "请选账号并填平台曲目 ID，平台附曲不能绑定本地音轨。",
                )
            account = live(actor, body.account, "Account", body.project_id)
            music = save(
                actor,
                "Music",
                body.project_id,
                {
                    "name": body.name,
                    "soundtrack_mode": body.mode,
                    "sound_id": body.sound_id,
                    "scope": scope,
                    "status": "awaiting_channel_verification",
                    "account_ref": ref(account),
                    "evidence": body.evidence,
                    "embedded": False,
                    "platform_effect_verified": False,
                },
                [*refs, ref(account)],
            )
            return {"music": music, "approval": None, "grant": None}
        if body.account or body.sound_id:
            raise DomainError("MUSIC_ROUTE_MISMATCH", "本地混音和无背景音乐路线不能登记平台附曲。")
        grant = None
        audio = None
        if body.mode == "no_music":
            if (
                body.audio
                or project["data"].get("content_mode") == "music"
                or project["data"].get("music_required") is True
            ):
                raise DomainError("MUSIC_REQUIRED", "当前作品要求音乐，或仍选择了音轨。")
        else:
            if not body.audio:
                raise DomainError("STOCK_AUDIO_REQUIRED", "请选择库存音轨。")
            if (
                body.market.casefold() not in {m.casefold() for m in body.allowed_markets}
                and "*" not in body.allowed_markets
            ):
                raise DomainError("MUSIC_LICENSE_SCOPE", "授权市场未包含本项目市场。", 403)
            if body.use not in body.allowed_uses:
                raise DomainError("MUSIC_LICENSE_SCOPE", "授权用途未包含本次用途。", 403)
            if bool(body.expires_at) == body.perpetual:
                raise DomainError("MUSIC_TERM_REQUIRED", "请核实有效截止时间或明确登记长期授权。")
            if body.expires_at and timestamp(body.expires_at) <= datetime.now(timezone.utc):
                raise DomainError("MUSIC_LICENSE_EXPIRED", "曲目许可已过期。", 403)
            audio, _ = media_pin(actor, body.audio, body.project_id, "Audio")
            asset = record(
                actor, audio["data"].get("asset_id") or "", "Asset", project=body.project_id
            )
            source = record(
                actor, asset["data"].get("source_id") or "", "Source", project=body.project_id
            )
            refs += [ref(audio), ref(asset), ref(source)]
            grant = save(
                actor,
                "Grant",
                body.project_id,
                {
                    "name": body.name,
                    "grant_protocol": "soundtrack_license_v1",
                    "status": "active",
                    "bound_audio": ref(audio),
                    "bound_checksum": audio["data"]["content_hash"],
                    "source_ref": ref(source),
                    "licensed_markets": body.allowed_markets,
                    "licensed_uses": body.allowed_uses,
                    "perpetual": body.perpetual,
                    "expires_at": body.expires_at,
                    "evidence": body.evidence,
                    "attested_by": actor.actor_id,
                    "evidence_basis": "human_reviewed_license",
                },
                refs,
            )
            refs.append(ref(grant))
        music = save(
            actor,
            "Music",
            body.project_id,
            {
                "name": body.name,
                "soundtrack_mode": body.mode,
                "scope": scope,
                "audio_ref": ref(audio) if audio else None,
                "stock_grant": ref(grant) if grant else None,
                "status": "rights_checked" if grant else "no_music_selected",
                "analysis_status": "not_run",
                "embedded": False,
            },
            refs,
        )
        approval = save(
            actor,
            "Approval",
            body.project_id,
            {
                "name": body.name,
                "kind": "rights_check",
                "decision": "approved",
                "operation": "local_soundtrack_choice",
                "status": "approved",
                "subject_kind": "Music",
                "subject_id": music["id"],
                "subject_version": music["version"],
                "subject_hash": music["hash"],
                "scope": scope,
                "evidence": body.evidence,
                "actor_role": actor.role,
                "principal": actor.actor_id,
                "expires_at": body.expires_at,
            },
            [ref(music), *refs],
        )
        return {"music": music, "approval": approval, "grant": grant}

    return ok(on_request("soundtrack-rights", body, actor, apply), request)


def exact_receipt(actor, project, subject, kind, operation):
    store = get_store()
    with store.connection() as conn:
        row = conn.execute(
            """SELECT r.id FROM reference_index x
               JOIN records r ON r.tenant_id=x.tenant_id AND r.id=x.source_id AND r.latest_version=x.source_version
               JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
               WHERE x.tenant_id=? AND x.target_id=? AND x.target_version=? AND r.project_id=? AND r.kind=?
                 AND json_extract(v.data_json,'$.operation')=? AND json_extract(v.data_json,'$.subject_hash')=?
                 AND json_extract(v.data_json,'$.subject_id')=? AND json_extract(v.data_json,'$.subject_version')=?
               LIMIT 1""",
            (
                actor.tenant_id,
                subject["id"],
                subject["version"],
                project,
                kind,
                operation,
                subject["hash"],
                subject["id"],
                subject["version"],
            ),
        ).fetchone()
    if not row:
        raise DomainError("SOUNDTRACK_RECEIPT_MISSING", "需要此版本的原始执行或审查回执。", 409)
    return store.get(row["id"], actor.tenant_id)


def resolve_tracks(body, actor):
    project = record(actor, body.project_id, "Project")
    music_market(project, body.market)
    refs = [ref(project)]
    for pin, kind in [(body.brief, "Brief"), (body.plan, "Plan")]:
        if pin:
            refs.append(ref(live(actor, pin, kind, body.project_id)))
    tracks = []
    for spec in body.tracks:
        audio, path = media_pin(actor, spec.audio, body.project_id, "Audio")
        probe = probe_media(path)
        if not probe["has_audio"] or spec.source_out > probe["duration"] + 0.000001:
            raise DomainError("AUDIO_OUT_OF_RANGE", "选择的声音区间超出真实文件。")
        if spec.music:
            music = live(actor, spec.music, "Music", body.project_id)
            data = music["data"]
            scope = data.get("scope") or {}
            if (
                data.get("soundtrack_mode") != "local_stock"
                or data.get("audio_ref") != ref(audio)
                or scope.get("use") != body.use
                or str(scope.get("market")).casefold() != body.market.casefold()
            ):
                raise DomainError(
                    "MUSIC_LICENSE_SCOPE", "所选许可与音频版本、市场或用途不符。", 403
                )
            # Approval records cannot be created or changed via the generic write API.
            grant_ref = data.get("stock_grant") or {}
            if not grant_ref.get("id"):
                raise DomainError("MUSIC_GRANT_MISSING", "当前库存音乐缺少核验记录。", 403)
            grant = record(actor, grant_ref["id"], "Grant", project=body.project_id)
            if (
                grant["version"] != grant_ref.get("version")
                or grant["data"].get("bound_audio") != ref(audio)
                or grant["data"].get("bound_checksum") != audio["data"].get("content_hash")
            ):
                raise DomainError(
                    "MUSIC_LICENSE_CHANGED", "音乐许可或绑定文件已改变，请重新核实。", 409
                )
            check_current_media_rights(get_store(), actor, grant)
            approval = exact_receipt(
                actor, body.project_id, music, "Approval", "local_soundtrack_choice"
            )
            if approval["data"].get("decision") != "approved":
                raise DomainError("MUSIC_CHECK_REQUIRED", "请核对曲目许可。", 403)
            refs.extend([ref(music), ref(grant), ref(approval)])
        tracks.append((spec, audio, path))
        refs.append(ref(audio))
    if body.ducking and not {"music", "voice"}.issubset({s.role for s, _, _ in tracks}):
        raise DomainError("DUCKING_REQUIRES_BUSES", "自动压低需要实际的口播轨和音乐轨。")
    return tracks, list({(r["id"], r["version"]): r for r in refs}.values())


def make_mix(body, tracks, output):
    duration = max(spec.start + spec.source_out - spec.source_in for spec, _, _ in tracks)
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_complex_threads", "1"]
    filters, groups = [], {}
    for index, (spec, _, path) in enumerate(tracks):
        args += ["-i", str(path)]
        span = spec.source_out - spec.source_in
        chain = [
            f"atrim=start={spec.source_in:.9f}:end={spec.source_out:.9f}",
            "asetpts=PTS-STARTPTS",
            "aresample=48000",
            "aformat=sample_fmts=fltp:channel_layouts=stereo",
            f"volume={spec.gain_db:.6f}dB",
        ]
        if spec.fade_in:
            chain.append(f"afade=t=in:st=0:d={spec.fade_in:.8f}")
        if spec.fade_out:
            chain.append(f"afade=t=out:st={span-spec.fade_out:.8f}:d={spec.fade_out:.8f}")
        delay_samples = round(spec.start * 48000)
        chain.extend([f"adelay={delay_samples}S:all=1", f"apad=whole_dur={duration:.9f}"])
        filters.append(f"[{index}:a:0]" + ",".join(chain) + f"[t{index}]")
        groups.setdefault(spec.role, []).append(f"[t{index}]")
    for role, inputs in groups.items():
        filters.append("".join(inputs) + f"amix=inputs={len(inputs)}:normalize=0[{role}]")
    outputs = {role: f"[{role}]" for role in groups}
    if body.ducking:
        filters += [
            "[voice]asplit=2[side][dialogue]",
            "[music][side]sidechaincompress="
            f"threshold={10**(body.duck_threshold_db/20):.9f}:ratio={body.duck_ratio:.6f}:"
            f"attack={body.attack_ms}:release={body.release_ms}:detection=rms:makeup=1[music_ducked]",
        ]
        outputs.update(music="[music_ducked]", voice="[dialogue]")
    master = [f"amix=inputs={len(outputs)}:normalize=0:duration=longest"]
    if body.target_lufs is not None:
        master.append(f"loudnorm=I={body.target_lufs:.3f}:TP=-1.5:LRA=11")
    master += [
        "aresample=48000",
        "alimiter=limit=0.96:level=0:latency=1",
        f"atrim=duration={duration:.9f}",
    ]
    filters.append("".join(outputs.values()) + ",".join(master) + "[master]")
    graph = ";".join(filters)
    run_process(
        args + ["-filter_complex", graph, "-map", "[master]", "-c:a", "pcm_s24le", str(output)], 240
    )
    return graph


def measurements(path, temp):
    probe = probe_media(path)
    if not probe["has_audio"]:
        raise DomainError("NO_DELIVERED_AUDIO", "交付文件中没有可测量的音轨。")
    metadata = temp / "silence.txt"
    _, stderr = run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            f"silencedetect=n=-45dB:d=0.20,ametadata=print:file={metadata},"
            "loudnorm=I=-23:LRA=11:TP=-1.5:print_format=json",
            "-f",
            "null",
            "-",
        ],
        240,
    )
    match = re.findall(r'\{\s*"input_i".*?\}', stderr, re.S)
    if not match:
        raise DomainError("AUDIO_METER_UNAVAILABLE", "响度仪未返回有效读数。")
    stats = json.loads(match[-1])

    def finite(key):
        value = float(stats[key])
        return value if math.isfinite(value) else None

    intervals, begin = [], None
    for line in metadata.read_text().splitlines():
        if line.startswith("lavfi.silence_start="):
            begin = max(0, float(line.split("=", 1)[1]))
        elif line.startswith("lavfi.silence_end=") and begin is not None:
            intervals.append({"start": begin, "end": float(line.split("=", 1)[1])})
            begin = None
    if begin is not None:
        intervals.append({"start": begin, "end": probe["duration"]})
    streams = {
        s["codec_type"]: s for s in probe["streams"] if s["codec_type"] in {"audio", "video"}
    }
    sync = {"status": "audio_only"}
    if "video" in streams:
        try:
            a, v = streams["audio"], streams["video"]
            first = float(a["start_time"]) - float(v["start_time"])
            last = first + float(a["duration"]) - float(v["duration"])
            sync = {
                "status": "measured_stream_boundaries",
                "start_delta_seconds": first,
                "end_delta_seconds": last,
            }
        except (KeyError, ValueError, TypeError):
            sync = {"status": "clock_unavailable"}
    return {
        "meter": "ffmpeg_loudnorm_input",
        "integrated_lufs": finite("input_i"),
        "true_peak_dbtp": finite("input_tp"),
        "loudness_range_lu": finite("input_lra"),
        "duration": probe["duration"],
        "silence_dbfs": -45,
        "silence_min_seconds": 0.2,
        "silence_count": len(intervals),
        "silences": intervals[:200],
        "stream_clock": sync,
        "semantic_lip_sync": "not_measured",
    }


@router.post("/mixes")
def create_mix(body: MixRequest, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    tracks, refs = resolve_tracks(body, actor)
    with TemporaryDirectory(prefix="ai-ship-audio-") as folder:
        temp = Path(folder)
        output = temp / "mix.wav"
        graph = make_mix(body, tracks, output)
        reading = measurements(output, temp)
        probe = probe_media(output)
        blob_fields = persist_blob(output, actor.tenant_id)

    def commit():
        # CPU and file IO above execute without a write transaction. Pin and recheck
        # every rights/source dependency before the first durable domain change.
        if not actor_still_authorized(actor):
            raise DomainError("AUTHORIZATION_REVOKED", "混音期间制作权限发生变化。", 403)
        current_tracks, current_refs = resolve_tracks(body, actor)
        if refs != current_refs:
            raise DomainError("SOUNDTRACK_INPUT_CHANGED", "混音期间输入或许可已变化。", 409)
        blob = save(
            actor,
            "BlobLocation",
            body.project_id,
            {
                **blob_fields,
                "purpose": "audio",
                "mime_type": "audio/wav",
            },
            refs,
        )
        is_stock_arrangement = all(spec.role == "music" for spec, _, _ in current_tracks)
        stock_refs = []
        asset_fields = {}
        if is_stock_arrangement:
            source = save(
                actor,
                "Source",
                body.project_id,
                {
                    "name": body.name,
                    "kind": "licensed_stock_arrangement",
                    "rights_status": "scoped_stock_review",
                    "content_hash": blob_fields["checksum"],
                    "scope": {"market": body.market, "use": body.use},
                    "stock_recipe": [spec.model_dump() for spec, _, _ in current_tracks],
                },
                refs,
            )
            common = {
                "name": body.name,
                "filename": "stock-arrangement.wav",
                "media_type": "audio/wav",
                "source_id": source["id"],
                "blob_location_id": blob["id"],
                "probe": probe,
                "content_hash": blob_fields["checksum"],
                "size_bytes": blob_fields["size_bytes"],
                "status": "available",
            }
            asset = save(actor, "Asset", body.project_id, common, [ref(source), ref(blob)])
            version = save(
                actor,
                "AssetVersion",
                body.project_id,
                {**common, "asset_id": asset["id"], "variant": "stock_arrangement"},
                [ref(asset), ref(source), ref(blob)],
            )
            stock_refs = [ref(asset), ref(version), ref(source)]
            asset_fields = {"asset_id": asset["id"], "asset_version_id": version["id"]}
        audio = save(
            actor,
            "Audio",
            body.project_id,
            {
                "name": body.name,
                "filename": "soundtrack.wav",
                **asset_fields,
                "sample_rate": 48000,
                "blob_location_id": blob["id"],
                "content_hash": blob_fields["checksum"],
                "probe": probe,
                "status": "available",
            },
            [ref(blob), *stock_refs, *refs],
        )
        music = None
        if is_stock_arrangement:
            music = save(
                actor,
                "Music",
                body.project_id,
                {
                    "name": body.name,
                    "soundtrack_mode": "stock_arrangement",
                    "status": "media_received",
                    "audio_ref": ref(audio),
                    "analysis_status": "not_run",
                    "origin": "licensed_stock_edit",
                    "arranged_asset": stock_refs[0],
                    "arranged_version": stock_refs[1],
                    "arranged_source": stock_refs[2],
                    "source_music": [
                        ref(live(actor, spec.music, "Music", body.project_id))
                        for spec, _, _ in tracks
                    ],
                    "stock_recipe": [spec.model_dump() for spec, _, _ in tracks],
                },
                [ref(audio), *stock_refs, *refs],
            )
        mix = save(
            actor,
            "Mix",
            body.project_id,
            {
                "name": body.name,
                "status": "awaiting_listen",
                "mix_protocol": "soundtrack_pcm_v1",
                "rendered_audio": ref(audio),
                "rendered_checksum": blob_fields["checksum"],
                "use": body.use,
                "market": body.market,
                "track_manifest": [spec.model_dump() for spec, _, _ in tracks],
                "bus_order": list(dict.fromkeys(spec.role for spec, _, _ in tracks)),
                "sample_rate_hz": 48000,
                "graph": graph,
                "ducking": body.ducking,
                "target_lufs": body.target_lufs,
                "measurement": reading,
                "stock_arrangement": ref(music) if music else None,
            },
            [ref(audio), *([ref(music)] if music else []), *refs],
        )
        audit = save(
            actor,
            "Audit",
            body.project_id,
            {
                "name": "声音母带执行记录",
                "operation": "soundtrack_pcm_render",
                "status": "measured",
                "subject_id": mix["id"],
                "subject_hash": mix["hash"],
                "subject_version": mix["version"],
                "output_checksum": blob_fields["checksum"],
            },
            [ref(mix), ref(audio)],
        )
        return {"mix": mix, "audio": audio, "music": music, "audit": audit}

    return ok(on_request("soundtrack-mix", body, actor, commit), request)


@router.post("/adopt")
def adopt_mix(body: AdoptMix, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        timeline = live(actor, body.timeline, "Timeline", body.project_id)
        mix = live(actor, body.mix, "Mix", body.project_id)
        project = record(actor, body.project_id, "Project")
        if mix["data"].get("mix_protocol") != "soundtrack_pcm_v1":
            raise DomainError("SOUNDTRACK_MIX_REQUIRED", "请先生成有实测结果的混音。")
        exact_receipt(actor, body.project_id, mix, "Audit", "soundtrack_pcm_render")
        music_market(project, mix["data"]["market"])
        if timeline["data"].get("locked") or timeline["data"].get("status") in {
            "frozen",
            "locked",
            "approved",
        }:
            raise DomainError("TIMELINE_LOCKED", "时间线已锁定，请先选用可编辑版本。", 409)
        audio, path = media_pin(
            actor,
            ChosenVersion(**{k: mix["data"]["rendered_audio"][k] for k in ("id", "version")}),
            body.project_id,
            "Audio",
        )
        if audio["data"]["content_hash"] != mix["data"]["rendered_checksum"]:
            raise DomainError("SOUNDTRACK_INPUT_CHANGED", "混音文件已改变。", 409)
        clips = timeline["data"].get("clips") or []
        if not clips:
            raise DomainError("PICTURE_REQUIRED", "请先采用镜头方案。")
        normalized = [
            normalize_clip(get_store(), actor, body.project_id, clip, i)
            for i, clip in enumerate(clips)
        ]
        end = max(c["timeline_start"] + c["duration"] for c in normalized)
        duration = probe_media(path)["duration"]
        if duration > end + 0.001:
            raise DomainError("MIX_LONGER_THAN_PICTURE", "混音长于现有画面，请在混音中调整源区间。")
        tracks = list(timeline["data"].get("audio_tracks") or [])
        prior = timeline["data"].get("soundtrack_use_id")
        if prior:
            if any(t.get("id") == prior and t.get("locked") for t in tracks):
                raise DomainError("AUDIO_TRACK_LOCKED", "已采用的声音轨被锁定。", 409)
            tracks = [t for t in tracks if t.get("id") != prior]
        if len(tracks) >= 16:
            raise DomainError("AUDIO_TRACK_LIMIT", "时间线独立音轨已满。")
        if body.original_audio == "mute":
            if any(c.get("locked") and c.get("volume", 1) != 0 for c in clips):
                raise DomainError("AUDIO_TRACK_LOCKED", "锁定画面片段的原声音量不能覆盖。", 409)
            clips = [{**c, "volume": 0} for c in clips]
        use_id = "soundtrack-" + mix["id"]
        tracks.append(
            {
                "id": use_id,
                "asset_id": audio["id"],
                "source_in": 0,
                "source_out": min(duration, end),
                "timeline_start": 0,
                "track": 1,
                "volume": 1,
                "speed": 1,
            }
        )
        old_mix_ref = timeline["data"].get("soundtrack_mix")
        deps = [r for r in timeline["refs"] if not old_mix_ref or r["id"] != old_mix_ref["id"]]
        updated = revise(
            actor,
            timeline,
            {
                "clips": clips,
                "audio_tracks": tracks,
                "soundtrack_mix": ref(mix),
                "soundtrack_use_id": use_id,
            },
            "试听后采用混音",
            [*deps, ref(mix)],
        )
        return {"timeline": updated, "mix": mix}

    return ok(on_request("soundtrack-adopt", body, actor, apply), request)


@router.post("/inspect-output")
def inspect_output(body: InspectOutput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    artifact, path = media_pin(actor, body.artifact, body.project_id, "Artifact")
    with TemporaryDirectory(prefix="ai-ship-meter-") as folder:
        result = measurements(path, Path(folder))

    def commit():
        if not actor_still_authorized(actor):
            raise DomainError("AUTHORIZATION_REVOKED", "检测期间制作权限发生变化。", 403)
        row, _ = media_pin(actor, body.artifact, body.project_id, "Artifact")
        audit = save(
            actor,
            "Audit",
            body.project_id,
            {
                "name": "成片声音实测",
                "status": "measured",
                "subject_hash": row["hash"],
                "content_hash": row["data"].get("content_hash"),
                "operation": "encoded_audio_meter",
                "subject_id": row["id"],
                "subject_version": row["version"],
                "audio_measurement": result,
                "semantic_review": "awaiting_listen",
            },
            [ref(artifact)],
        )
        return {"audit": audit, "measurement": result}

    return ok(on_request("soundtrack-output", body, actor, commit), request)


class RemoveMusic(Operation):
    music: ChosenVersion
    timeline: ChosenVersion


def editable_timeline(actor, body):
    timeline = live(actor, body.timeline, "Timeline", body.project_id)
    if timeline["data"].get("locked") or timeline["data"].get("status") in {
        "approved",
        "locked",
        "frozen",
    }:
        raise DomainError("TIMELINE_LOCKED", "时间线已锁定，请选可编辑版本。", 409)
    return timeline


@router.post("/remove-music")
def remove_music(body: RemoveMusic, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    choice = live(actor, body.music, "Music", body.project_id)
    if choice["data"].get("soundtrack_mode") != "no_music":
        raise DomainError("NO_MUSIC_CHOICE_REQUIRED", "请选择已核对的无背景音乐方案。")
    exact_receipt(actor, body.project_id, choice, "Approval", "local_soundtrack_choice")
    project = record(actor, body.project_id, "Project")
    music_market(project, choice["data"]["scope"]["market"])
    if (
        project["data"].get("content_mode") == "music"
        or project["data"].get("music_required") is True
    ):
        raise DomainError("MUSIC_REQUIRED", "当前作品要求音乐。")
    timeline = editable_timeline(actor, body)
    old = timeline["data"].get("soundtrack_mix")
    use_id = timeline["data"].get("soundtrack_use_id")
    tracks = list(timeline["data"].get("audio_tracks") or [])
    target = next((track for track in tracks if track.get("id") == use_id), None)
    if target and target.get("locked"):
        raise DomainError("AUDIO_TRACK_LOCKED", "已采用的声音轨被锁定。", 409)
    replacement = None
    if target and old:
        mix = record(actor, old["id"], "Mix", old["version"], body.project_id)
        exact_receipt(actor, body.project_id, mix, "Audit", "soundtrack_pcm_render")
        if (target.get("asset_id") or target.get("source_id")) != mix["data"]["rendered_audio"][
            "id"
        ]:
            raise DomainError(
                "AUDIO_MANUAL_EDIT", "声轨来源已被精剪替换，请在当前时间线核对。", 409
            )
        manifest = mix["data"]["track_manifest"]
        retained = [t for t in manifest if t["role"] != "music"]
        if len(retained) == len(manifest):
            target = None
        elif retained:
            replacement = create_mix(
                MixRequest(
                    project_id=body.project_id,
                    request_id="strip-" + body.request_id[:190],
                    name=(mix["data"]["name"][:175] + " · 无音乐"),
                    tracks=retained,
                    market=choice["data"]["scope"]["market"],
                    use=choice["data"]["scope"]["use"],
                    target_lufs=mix["data"].get("target_lufs"),
                ),
                request,
                actor,
            )["data"]

    def commit():
        current = editable_timeline(actor, body)
        live(actor, body.music, "Music", body.project_id)
        current_project = record(actor, body.project_id, "Project")
        if current_project["version"] != project["version"]:
            raise DomainError("PROJECT_CHANGED", "项目用途发生变化，请重新核实。", 409)
        includes = list(current["data"].get("audio_tracks") or [])
        reference = current["data"].get("soundtrack_mix")
        chosen_use = current["data"].get("soundtrack_use_id")
        deps = list(current["refs"])
        if target:
            includes = [t for t in includes if t.get("id") != use_id]
            deps = [r for r in deps if r["id"] != old["id"]]
            reference = None
            chosen_use = None
            if replacement:
                audio = replacement["audio"]
                check_current_media_rights(get_store(), actor, audio)
                duration = audio["data"]["probe"]["duration"]
                if (
                    abs(float(target.get("speed", 1)) - 1) > 1e-9
                    or float(target.get("source_in", 0)) != 0
                    or abs(float(target.get("timeline_start", 0))) > 1e-9
                ):
                    raise DomainError(
                        "AUDIO_MANUAL_EDIT", "混音片段位置或速度已改变，请先核对人工剪辑。", 409
                    )
                chosen_use = "soundtrack-" + replacement["mix"]["id"]
                includes.append(
                    {
                        **target,
                        "id": chosen_use,
                        "asset_id": audio["id"],
                        "source_out": min(duration, target["source_out"]),
                    }
                )
                reference = ref(replacement["mix"])
                deps.append(reference)
        previous = current["data"].get("no_music_choice") or {}
        deps = [r for r in deps if r["id"] != previous.get("id")]
        updated = revise(
            actor,
            current,
            {
                "audio_tracks": includes,
                "soundtrack_mix": reference,
                "soundtrack_use_id": chosen_use,
                "no_music_choice": ref(choice),
            },
            "移除已标注的音乐总线，保留其他声音",
            [*deps, ref(choice)],
        )
        return {
            "timeline": updated,
            "removed_managed_music": bool(target),
            "retained_remix": reference,
            "embedded_or_unclassified_audio": "requires_listening",
        }

    return ok(on_request("soundtrack-remove", body, actor, commit), request)


class ListeningFinding(SelectionBody):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    category: Literal["silence", "peak", "sync", "intelligibility", "seam", "other"]
    observation: Note


class ListeningReview(Operation):
    artifact: ChosenVersion
    meter: ChosenVersion
    decision: Literal["accepted", "rework"]
    findings: list[ListeningFinding] = Field(min_length=1, max_length=128)


@router.post("/listen-review")
def listen_review(body: ListeningReview, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def commit():
        artifact, path = media_pin(actor, body.artifact, body.project_id, "Artifact")
        audit = live(actor, body.meter, "Audit", body.project_id)
        data = audit["data"]
        if (
            data.get("operation") != "encoded_audio_meter"
            or data.get("subject_hash") != artifact["hash"]
            or data.get("subject_id") != artifact["id"]
            or data.get("subject_version") != artifact["version"]
        ):
            raise DomainError("AUDIO_METER_MISMATCH", "检测回执不属于这份成片。", 409)
        end = probe_media(path)["duration"]
        for finding in body.findings:
            if not finding.start < finding.end <= end + 0.000001:
                raise DomainError("LISTEN_RANGE_INVALID", "试听区间超出实际成片时长。")
        approval = save(
            actor,
            "Approval",
            body.project_id,
            {
                "name": "成片局部试听",
                "kind": "review_finding",
                "operation": "timecoded_audio_listen",
                "subject_kind": "Artifact",
                "subject_id": artifact["id"],
                "subject_version": artifact["version"],
                "subject_hash": artifact["hash"],
                "decision": body.decision,
                "status": "approved" if body.decision == "accepted" else "rejected",
                "principal": actor.actor_id,
                "actor_role": actor.role,
                "audio_meter": ref(audit),
                "findings": [f.model_dump() for f in body.findings],
            },
            [ref(artifact), ref(audit)],
        )
        return {"review": approval}

    return ok(on_request("soundtrack-listen", body, actor, commit), request)
