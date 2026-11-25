"""Local ViT-B/32 CLIP projections for decoded video frames and text queries."""

from functools import lru_cache
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import threading

from .errors import DomainError

_FILES = ("vision_model_quantized.onnx", "text_model_quantized.onnx", "tokenizer.json")
_LOAD = threading.Lock()
_WORK = threading.BoundedSemaphore(2)
_PIXELS = 224 * 224 * 3
_PTS = re.compile(rb"\bn:\s*0\s+pts:\s*-?\d+\s+pts_time:([\d.eE+-]+)")


def local_model():
    directory = os.getenv("AI_SHIP_CLIP_MODEL_DIR")
    if not directory:
        return None
    try:
        paths = tuple(Path(directory) / n for n in _FILES)
        signature = tuple((p.stat().st_ino, p.stat().st_size, p.stat().st_ctime_ns) for p in paths)
        with _LOAD:
            return _load(paths, signature)
    except Exception as exc:
        raise DomainError(
            "CLIP_UNAVAILABLE", "本机 CLIP 权重、分词器或 ONNX 运行环境未就绪。", 503
        ) from exc


@lru_cache(maxsize=1)
def _load(paths, signature):
    import onnxruntime as ort
    from tokenizers import Tokenizer

    fingerprint = hashlib.sha256(b"clip-b32-v1/rgb-bicubic-center224/pts-3/eot77")
    for p in paths:
        with p.open("rb") as f:
            fingerprint.update(hashlib.file_digest(f, "sha256").digest())
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    vision, text = (
        ort.InferenceSession(str(p), sess_options=options, providers=["CPUExecutionProvider"])
        for p in paths[:2]
    )
    tokenizer = Tokenizer.from_file(str(paths[2]))
    tokenizer.enable_truncation(max_length=77)
    if {v.name for v in vision.get_inputs()} != {"pixel_values"} or {
        v.name for v in text.get_inputs()
    } - {"input_ids", "attention_mask"}:
        raise ValueError("unsupported CLIP input signature")
    return "clip-b32/" + fingerprint.hexdigest(), vision, text, tokenizer


def _encode(session, output, inputs):
    import numpy as np

    if not _WORK.acquire(timeout=5):
        raise DomainError("CLIP_BUSY", "本机视觉模型正在处理其他请求。", 429)
    try:
        result = session.run([output], inputs)[0]
        if result.shape != (1, 512):
            raise ValueError("projection shape")
        norm = float(np.linalg.norm(result))
        if not math.isfinite(norm) or norm < 1e-8:
            raise ValueError("projection norm")
        return (result[0] / norm).astype("<f4")
    except Exception as exc:
        raise DomainError("CLIP_FAILED", "视觉模型输出未通过校验。", 503) from exc
    finally:
        _WORK.release()


def encode_query(model, query):
    import numpy as np

    _, _, session, tokenizer = model
    tokens = tokenizer.encode(query)
    values = {"input_ids": tokens.ids, "attention_mask": tokens.attention_mask}
    return _encode(
        session,
        "text_embeds",
        {p.name: np.asarray([values[p.name]], dtype="int64") for p in session.get_inputs()},
    )


def decode_frame(path, at):
    # -copyts/-start_at_zero retains actual presentation times relative to the source.
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-copyts",
            "-start_at_zero",
            "-ss",
            f"{at:.6f}",
            "-protocol_whitelist",
            "file,pipe",
            "-threads",
            "1",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-filter_threads",
            "1",
            "-vf",
            "showinfo,scale=224:224:force_original_aspect_ratio=increase:flags=bicubic,crop=224:224",
            "-threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode:
        raise DomainError("FRAME_DECODE_FAILED", "无法解码此素材的画面。", 422)
    if not result.stdout:
        return None
    pts = _PTS.search(result.stderr)
    if not pts or len(result.stdout) != _PIXELS:
        raise DomainError("FRAME_TIMING_MISSING", "无法核对视频帧或源时码。", 422)
    seconds = float(pts[1])
    if not math.isfinite(seconds) or seconds < 0:
        raise DomainError("FRAME_TIMING_MISSING", "源帧时码无效。", 422)
    return round(seconds * 1_000_000), result.stdout


def video_frames(model, path, duration):
    import numpy as np

    if not duration or not math.isfinite(duration) or duration <= 0:
        raise DomainError("FRAME_DURATION_MISSING", "缺少有效的视频时长。", 422)
    frames = {}
    try:
        for at in [0, duration / 2, duration * 5 / 6]:
            decoded = decode_frame(path, at)
            if not decoded:
                continue
            pts, rgb = decoded
            if pts in frames:
                continue
            pixels = np.frombuffer(rgb, dtype="uint8").reshape(224, 224, 3).astype("float32") / 255
            pixels = (
                pixels - np.array([0.48145466, 0.4578275, 0.40821073], dtype="float32")
            ) / np.array([0.26862954, 0.26130258, 0.27577711], dtype="float32")
            vector = _encode(
                model[1], "image_embeds", {"pixel_values": pixels.transpose(2, 0, 1)[None]}
            )
            frames[pts] = (hashlib.sha256(rgb).hexdigest(), vector.tobytes())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DomainError("FRAME_DECODE_FAILED", "本机解码器未就绪或解码超时。", 422) from exc
    if not frames:
        raise DomainError("FRAME_DECODE_FAILED", "未取得带源时码的画面。", 422)
    return frames
