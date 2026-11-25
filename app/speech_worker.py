"""Isolated, local Vosk worker. Never resolves a model name through a network service."""

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import wave


def model_digest(root):
    manifest = sha256()
    for relative in sorted(p.relative_to(root) for p in root.rglob("*")):
        path = root / relative
        if path.is_symlink():
            raise ValueError("Model must contain regular local files")
        if path.is_file():
            h = sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            manifest.update(relative.as_posix().encode() + b"\0" + h.digest())
    return manifest.hexdigest()


def transcribe(model_path, pin, wav_path, language):
    import vosk

    if version("vosk") != "0.3.45" or model_digest(model_path) != pin:
        raise ValueError("Configured model or engine version mismatch")
    vosk.SetLogLevel(-1)
    model = vosk.Model(str(model_path))
    recognizer = vosk.KaldiRecognizer(model, 16000)
    recognizer.SetWords(True)
    groups = []

    def append(response):
        words = json.loads(response).get("result", [])
        # Cap individual cue length for legibility; all retained bounds come from decoded words.
        group = []
        for word in words:
            if word["word"] in {"<unk>", "[unk]"}:
                continue
            if group and (
                word["end"] - group[0]["start"] > 6
                or word["start"] - group[-1]["end"] > 0.7
                or len(group) >= 15
            ):
                groups.append(group)
                group = []
            group.append(word)
        if group:
            groups.append(group)

    with wave.open(str(wav_path)) as sound:
        if (sound.getnchannels(), sound.getsampwidth(), sound.getframerate()) != (1, 2, 16000):
            raise ValueError("Expected mono PCM16, 16000 Hz")
        while chunk := sound.readframes(4000):
            if recognizer.AcceptWaveform(chunk):
                append(recognizer.Result())
        append(recognizer.FinalResult())
    if model_digest(model_path) != pin:
        raise ValueError("Model changed during inference")
    joiner = "" if language.lower().startswith("zh") else " "
    return [{"text": joiner.join(w["word"] for w in words), "words": words} for words in groups]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("model", type=Path)
    p.add_argument("--pin")
    p.add_argument("--wav", type=Path)
    p.add_argument("--language")
    a = p.parse_args()
    if a.wav is None:
        print(model_digest(a.model))
    else:
        print(json.dumps(transcribe(a.model, a.pin, a.wav, a.language), ensure_ascii=False))
