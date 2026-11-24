"""Portable launch with explicit single-node storage. Does not auto-install tools."""

import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="AI Ship local production workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-dir", default=str(ROOT / ".data"))
    parser.add_argument("--director-model", type=Path, help="使用本机 GGUF 启动可绑定的 AI 导演")
    parser.add_argument("--llama-server", type=Path, help="本机 llama-server 程序路径")
    args = parser.parse_args()
    if args.llama_server and not args.director_model:
        parser.error("--llama-server 需要 --director-model。")
    if (
        args.host not in {"127.0.0.1", "localhost", "::1"}
        and os.getenv("AI_SHIP_MODE", "local") != "team"
    ):
        parser.error("非回环地址需 AI_SHIP_MODE=team 并配置服务端身份。")
    if os.getenv("AI_SHIP_MODE") == "team" and not os.getenv("AI_SHIP_API_KEYS"):
        parser.error("团队模式缺少 AI_SHIP_API_KEYS；请先按 operations.md 配置身份。")
    os.environ.setdefault("AI_SHIP_DATA_DIR", str(Path(args.data_dir).resolve()))
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        print(
            "缺少 " + ", ".join(missing) + "。工作台可启动，媒体处理将明确拒绝，安装后重启。",
            file=sys.stderr,
        )
    import uvicorn

    worker = nullcontext()
    if args.director_model:
        from app.local_director import local_director

        binary = args.llama_server or shutil.which("llama-server")
        if not binary:
            parser.error("请用 --llama-server 指定已安装的 llama.cpp 程序。")
        worker = local_director(Path(binary), args.director_model)
    with worker:
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
