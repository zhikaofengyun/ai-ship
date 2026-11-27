"""One local worker turns newly admitted media into reusable, source-pinned observations."""

from contextlib import contextmanager
import logging
import os
import sqlite3
import threading
import time

from . import frame_vectors, visual_candidates, visual_index
from .auth import Actor, actor_still_authorized
from .errors import DomainError
from .store import get_store

logger = logging.getLogger(__name__)


def enqueue(store, actor, project, version):
    with store.connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO material_analysis_jobs "
            "(tenant,project,version_id,actor,role,credential_digest,auth_mode) VALUES(?,?,?,?,?,?,?)",
            (
                actor.tenant_id,
                project,
                version["id"],
                actor.actor_id,
                actor.role,
                actor.credential_digest,
                os.getenv("AI_SHIP_MODE", "local"),
            ),
        )


def claim(store):
    stamp = time.time()
    with store.connection() as db:
        if not db.execute(
            "SELECT 1 FROM material_analysis_jobs WHERE state IN ('queued','working') AND due<=? LIMIT 1",
            (stamp,),
        ).fetchone():
            return None
        # A single UPDATE compare-and-claim works across API processes. A crashed
        # process loses its lease; its previously completed observations are reusable.
        return db.execute(
            """UPDATE material_analysis_jobs SET state='working', due=?, attempt=attempt+1
            WHERE rowid=(SELECT rowid FROM material_analysis_jobs
            WHERE state IN ('queued','working') AND due<=? ORDER BY due,rowid LIMIT 1)
            RETURNING *""",
            (stamp + 300, stamp),
        ).fetchone()


def work_once(store):
    row = claim(store)
    if row is None:
        return False
    actor = Actor(row["tenant"], row["actor"], row["role"], row["credential_digest"])
    state, code, delay = "completed", None, 0
    try:
        if row["auth_mode"] != os.getenv("AI_SHIP_MODE", "local") or not actor_still_authorized(
            actor
        ):
            raise DomainError("IDENTITY_REVOKED", "素材分析身份已变化。", 403)
        source, observation, _ = visual_candidates.observe_source(
            store, actor, row["project"], row["version_id"]
        )
        try:
            model = frame_vectors.local_model()
            if model:
                visual_index.index_source(
                    store,
                    actor,
                    row["project"],
                    model,
                    source["id"],
                    source["hash"],
                    observation["duration"],
                    observation["is_still"],
                )
        except DomainError as exc:
            if exc.status not in (429, 503):
                raise
            state, code = "measured", exc.code
            if exc.status == 429 and row["attempt"] < 4:
                state, delay = "queued", 5 * row["attempt"]
        if observation["ocr"].get("retryable"):
            code = observation["ocr"].get("code", "OCR_FRAME_UNAVAILABLE")
            state = "measured"
            if row["attempt"] < 4:
                state, delay = "queued", 5 * row["attempt"]
    except DomainError as exc:
        state, code = "failed", exc.code
        if exc.status in (429, 503) and row["attempt"] < 4:
            state, delay = "queued", 5 * row["attempt"]
    except Exception:
        logger.exception("素材分析中断")
        state, code = "failed", "ANALYSIS_FAILED"
    with store.connection() as db:
        db.execute(
            """UPDATE material_analysis_jobs SET state=?,code=?,due=? WHERE tenant=? AND project=?
            AND version_id=? AND state='working' AND attempt=?""",
            (
                state,
                code,
                time.time() + delay,
                row["tenant"],
                row["project"],
                row["version_id"],
                row["attempt"],
            ),
        )
    return True


@contextmanager
def material_worker():
    if os.getenv("AI_SHIP_MATERIAL_WORKER", "1") == "0":
        yield
        return
    store = get_store()
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                if work_once(store):
                    continue
            except sqlite3.Error:
                logger.exception("素材分析队列存储暂不可用")
            stop.wait(1)

    thread = threading.Thread(target=loop, name="material-analysis", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
