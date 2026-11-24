"""Bounded, range-specific comparison of decoded pictures, on-screen words and sound."""

from array import array
import hashlib
import math
import re
import sys
from statistics import fmean
import unicodedata

from .errors import DomainError
from .media import run_process
from .visual_candidates import recognize_text

W, H = 24, 16
BANDS = (80, 125, 200, 315, 500, 800, 1250, 2000, 3150)
DIMENSIONS = ("picture", "text", "sound", "structure")


def tokens(words):
    """Make independent Han characters and Latin word/number tokens comparable."""
    text = unicodedata.normalize("NFKC", " ".join(w["text"] for w in words)).casefold()
    return re.findall(r"[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+", text)


def decode(path, at, args):
    return run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-ss",
            str(at),
            "-i",
            str(path),
            *args,
            "-threads",
            "1",
            "pipe:1",
        ],
        timeout=12,
        binary_output=True,
    )[0]


def scene(path, at):
    pixels = decode(
        path,
        at,
        [
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            f"scale={W}:{H}:flags=area",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
        ],
    )
    if len(pixels) != W * H:
        raise DomainError("VARIANT_FRAME_UNAVAILABLE", "选段无法解码为可复核画面。", 409)
    mean = fmean(pixels)
    scale = max(10.0, math.sqrt(fmean((p - mean) ** 2 for p in pixels)))
    # Normalization suppresses global recolouring. Compare both horizontal directions below.
    shape = [max(-63, min(63, round(18 * (p - mean) / scale))) for p in pixels]
    return {"at": at, "sha256": hashlib.sha256(pixels).hexdigest(), "shape": shape}


def sound(path, at, duration, has_audio):
    if not has_audio:
        return {"status": "no_stream"}
    raw = decode(
        path,
        at,
        ["-map", "0:a:0", "-t", str(duration), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le"],
    )
    waveform = array("h")
    waveform.frombytes(raw)
    if sys.byteorder != "little":
        waveform.byteswap()
    if len(waveform) < 32:
        return {"status": "unavailable"}
    energy = fmean(float(x) ** 2 for x in waveform)
    if energy < 16:
        return {"status": "silence", "sha256": hashlib.sha256(raw).hexdigest()}
    # Several short Hann windows; spectral power is normalized for volume changes.
    length = min(256, len(waveform))
    window = [0.5 - 0.5 * math.cos(2 * math.pi * n / (length - 1)) for n in range(length)]
    powers = [0.0] * len(BANDS)
    for start in sorted({round(i * (len(waveform) - length) / 3) for i in range(4)}):
        signal = [waveform[start + j] * window[j] for j in range(length)]
        for i, hz in enumerate(BANDS):
            c = 2 * math.cos(2 * math.pi * hz / 8000)
            prev = prev2 = 0.0
            for value in signal:
                prev2, prev = prev, value + c * prev - prev2
            powers[i] += max(0, prev**2 + prev2**2 - c * prev * prev2)
    magnitudes = [p**0.5 for p in powers]
    total = sum(magnitudes) or 1.0
    return {
        "status": "measured",
        "at": at,
        "duration": len(waveform) / 8000,
        "bands": [round(x / total, 5) for x in magnitudes],
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def fingerprint(path, start, end, probe):
    frame = 1 / max(1.0, float(probe["frame_rate"]))
    last = max(start, end - frame)
    instants = sorted({round(start + (last - start) * t, 6) for t in (0, 0.5, 1.0)})
    shots = [scene(path, t) for t in instants]
    middle = instants[len(instants) // 2]
    ocr = recognize_text(path, middle, middle, frame_receipt=True)
    text = tokens(ocr.get("words", [])) if ocr.get("status") == "read" else None
    length = min(end - start, 0.75)
    audio_at = round(start + (end - start - length) / 2, 6)
    return {
        "range": [start, end],
        "frames": shots,
        "words": text,
        "ocr_status": ocr["status"],
        "ocr_at": ocr.get("at"),
        "audio": sound(path, audio_at, length, probe["has_audio"]),
    }


def picture_difference(left, right):
    if left["sha256"] == right["sha256"]:
        return 0.0
    a, b = left["shape"], right["shape"]
    direct = sum(abs(x - y) for x, y in zip(a, b))
    mirrored = sum(abs(a[y * W + x] - b[y * W + W - 1 - x]) for y in range(H) for x in range(W))
    return min(1.0, min(direct, mirrored) / (W * H * 25))


def text_difference(a, b):
    if a is None or b is None or not (a or b):
        return None
    first, second = set(a), set(b)
    return 1 - len(first & second) / len(first | second)


def sound_difference(a, b):
    if "unavailable" in {a["status"], b["status"]}:
        return None
    if a["status"] != "measured" and b["status"] != "measured":
        return None
    if a["status"] != b["status"]:
        return 1.0
    return sum(abs(x - y) for x, y in zip(a["bands"], b["bands"])) / 2


def difference(a, b, same_family):
    frames = []
    for i in range(3):
        left = a["frames"][round(i * (len(a["frames"]) - 1) / 2)]
        right = b["frames"][round(i * (len(b["frames"]) - 1) / 2)]
        frames.append(picture_difference(left, right))
    return {
        "picture": round(fmean(frames), 4),
        "text": text_difference(a["words"], b["words"]),
        "sound": sound_difference(a["audio"], b["audio"]),
        "structure": 0.0 if same_family else 1.0,
    }
