"""Optional local mean-pooled transformer for names and verified segment tags."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import threading

from .errors import DomainError

_GATE = threading.BoundedSemaphore(2)


def local_model():
    directory = os.getenv("AI_SHIP_TEXT_MODEL_DIR")
    if not directory:
        return None
    root = Path(directory)
    try:
        files = [root / "model.onnx", root / "tokenizer.json"]
        return _load(*(str(p) for p in files), *(p.stat().st_mtime_ns for p in files))
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        raise DomainError(
            "TEXT_MODEL_UNAVAILABLE", "本机语义模型、分词器或可选依赖未就绪。", 503
        ) from exc


@lru_cache(maxsize=1)
def _load(graph, tokenizer, graph_time, tokenizer_time):
    import onnxruntime as ort
    from tokenizers import Tokenizer

    with open(graph, "rb") as f, open(tokenizer, "rb") as t:
        identity = (
            "text-mean-v1/"
            + hashlib.file_digest(f, "sha256").hexdigest()
            + "/"
            + hashlib.file_digest(t, "sha256").hexdigest()
        )
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    try:
        session = ort.InferenceSession(
            graph, sess_options=options, providers=["CPUExecutionProvider"]
        )
        tokens = Tokenizer.from_file(tokenizer)
        tokens.enable_truncation(max_length=128)
        tokens.enable_padding(
            pad_id=(
                tokens.padding["pad_id"]
                if tokens.padding
                else (tokens.token_to_id("<pad>") or tokens.token_to_id("[PAD]") or 0)
            )
        )
    except Exception as exc:
        raise ValueError("invalid local embedding graph") from exc
    return identity, session, tokens


def encode(model, texts):
    import numpy as np

    _, session, tokenizer = model
    if not _GATE.acquire(timeout=10):
        raise DomainError("TEXT_MODEL_BUSY", "本机语义索引正在处理其他分段。", 429)
    try:
        enc = tokenizer.encode_batch([s[:3000] for s in texts])
        ids = np.asarray([t.ids for t in enc], dtype=np.int64)
        mask = np.asarray([t.attention_mask for t in enc], dtype=np.int64)
        tensors = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
        outputs = session.run(None, {p.name: tensors[p.name] for p in session.get_inputs()})[0]
        if outputs.ndim != 3 or outputs.shape[:2] != ids.shape:
            raise ValueError("token output shape mismatch")
        pooled = (outputs * mask[:, :, None]).sum(axis=1) / np.maximum(mask.sum(axis=1)[:, None], 1)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        if not np.isfinite(norms).all() or np.any(norms < 1e-9):
            raise ValueError("invalid embedding")
        return (pooled / norms).astype("<f4")
    except Exception as exc:
        raise DomainError("TEXT_MODEL_FAILED", "本机模型的向量输出未通过校验。", 503) from exc
    finally:
        _GATE.release()
