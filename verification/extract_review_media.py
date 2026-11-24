"""Extract the nine recorded and rendered films plus the five-round contact sheet."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile


PUBLIC_SHA256 = "08b35048737627e0ef0572b40d46a3921de5c06610fc90335aa83e0b91ef524d"
PATCH_SHA256 = "67e01f74572874afd69dc99c12ac5b15f3700bb0621858a0556059693017fa6e"
MEDIA_TREE = "5d1d4d28e8df34ba97b4a91e9e65874072fcaf30"


def extract(destination: Path) -> None:
    patch = Path(__file__).with_name("review-films.patch").read_bytes()
    if hashlib.sha256(patch).hexdigest() == PUBLIC_SHA256:
        # GitHub's editor removed the final blank line after the reverse binary hunk.
        patch += b"\n"
    if hashlib.sha256(patch).hexdigest() != PATCH_SHA256:
        raise SystemExit("样片补丁的内容与已验证版本不一致。")

    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
    for check_flag in (["--check"], []):
        subprocess.run(
            ["git", "-C", str(destination), "apply", "--binary", "--index", *check_flag, "-"],
            input=patch,
            check=True,
        )
    tree = subprocess.check_output(["git", "-C", str(destination), "write-tree"], text=True).strip()
    if tree != MEDIA_TREE:
        raise SystemExit("解包后的文件内容或权限与发布清单不符。")
    print(destination.resolve() / "evidence" / "ai-production")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path, help="new output directory")
    args = parser.parse_args()
    if args.destination is None:
        output = Path(tempfile.mkdtemp(prefix="ai-ship-review-"))
    else:
        output = args.destination
        output.mkdir(parents=True, exist_ok=False)
    extract(output)
