"""Own an authenticated loopback llama.cpp worker for the life of the local workbench."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time

import httpx


@contextmanager
def local_director(executable: Path, weights: Path, *, log=None):
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("找不到可执行的 llama-server。")
    if not weights.is_file():
        raise ValueError("找不到选定的 GGUF 模型文件。")
    key = "local-director"
    alias = "atlas-local-director"
    secret_var = "AI_SHIP_EPHEMERAL_DIRECTOR_KEY"
    original = os.getenv("AI_SHIP_PROVIDERS")
    configs = json.loads(original or "{}")
    if not isinstance(configs, dict) or key in configs or secret_var in os.environ:
        raise ValueError("本地导演配置名已占用，或已有供应商配置无效。")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/v1"
    with tempfile.TemporaryDirectory() as private:
        token = secrets.token_urlsafe(32)
        credentials = Path(private) / "worker.key"
        credentials.write_text(token + "\n")
        credentials.chmod(0o600)
        proc = subprocess.Popen(
            [
                str(executable.resolve()),
                "-m",
                str(weights.resolve()),
                "--alias",
                alias,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--threads",
                "3",
                "--ctx-size",
                "8192",
                "--parallel",
                "1",
                "--seed",
                "42",
                "--no-webui",
                "--api-key-file",
                str(credentials),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError("本地模型进程已退出，请检查模型兼容性和可用内存。")
                    try:
                        response = client.get(
                            url + "/models", headers={"Authorization": "Bearer " + token}
                        )
                        if response.is_success and any(
                            m.get("id") == alias for m in response.json()["data"]
                        ):
                            if client.get(url + "/models").status_code != 401:
                                raise RuntimeError("本地模型未按设置拒绝匿名访问。")
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.25)
                else:
                    raise TimeoutError("模型未在四分钟内就绪。")
            configs[key] = {
                "base_url": url,
                "transport": "loopback_chat",
                "models": [alias],
                "provider_identity": "local-director",
                "api_key_env": secret_var,
                "currency": "USD",
                "json_output": True,
                "structured_schema": True,
                "candidate_menu": True,
                "input_price_per_million": 0,
                "output_price_per_million": 0,
                "context_window": 8192,
                "timeout_seconds": 120,
            }
            os.environ[secret_var] = token
            os.environ["AI_SHIP_PROVIDERS"] = json.dumps(configs)
            yield key, alias
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            os.environ.pop(secret_var, None)
            if original is None:
                os.environ.pop("AI_SHIP_PROVIDERS", None)
            else:
                os.environ["AI_SHIP_PROVIDERS"] = original
