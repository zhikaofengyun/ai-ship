# 公开检出后可选还原原件字节 · Optional checkout byte restoration

公开提交 [a29463c](https://github.com/zhikaofengyun/ai-ship/commit/a29463c1a9e55ffb6686b38cec0c54b57fc58e20) 中，下列四个文件各在原件末尾多出一个换行字节（`0A`）。如需复核原始需求包及其派生文件的 SHA-256，可在仓库根目录运行下方代码。还原会在工作树中留下四项可查看的修改，应用启动无需此步骤。

In the public commit linked above, each listed file includes one extra terminal LF. To reproduce the frozen source digests, run the block below at the repository root. The optional operation creates four local working-tree changes. It first verifies the patch and every input in full, checks applicability with Git, and verifies all four outputs. An already restored tree is left untouched. Any other input or a mixture of states aborts before writing.

```sh
python3 - <<'PYRESTORE'
from hashlib import sha256
from pathlib import Path
import subprocess

# path -> (public SHA-256, frozen original SHA-256)
expected = {
    "contracts/atlas-integrity.json": (
        "88b0db731546b9a3aa2b32105e6aa24847d6abfb408bf3b131bb2681112e7ccf",
        "4b2525de94430ecec903119630587784a63c9276801c5c2bf21e3ebf07359724",
    ),
    "contracts/atlas.json": (
        "2352f7501a144ffda65c56f66e4a27c05c200c78815d693bf41b8d5150f42917",
        "37740087c289d8b2e90f2bc4b5176565cbd688b50f9f9674f625cbefa612c695",
    ),
    "contracts/feature-catalog.json": (
        "8d1dabd4f284ca0b7908b684980999622f6cbe64cfc16ced946e9c7da5c62aeb",
        "13feba0c8666edaea48e88f2229629d913dbd27a73cd16345f5af8083cd26884",
    ),
    "docs/AI_Video_Agent_Atlas.html": (
        "7ee79208ac15626261a3538f787bce286f2b6acf0516bb5ce5fefc53fa25912a",
        "65a0e6e7f19b0e087be4ff0adcd1166c7073211caa5988f739df05b47971dee4",
    ),
}

def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()

current = {path: digest(path) for path in expected}
if all(current[path] == original for path, (_, original) in expected.items()):
    print("已是原件 · Already restored")
    raise SystemExit(0)
if any(current[path] != published for path, (published, _) in expected.items()):
    raise SystemExit("输入与已知公开版本不一致，已停止 · Unexpected input, stopped")

patch = "docs/github-web-checkout.patch"
if digest(patch) != "2dd30e9cf6e549037e36659b88aade98fcfc8c649b5e365f67ebaec4f1680159":
    raise SystemExit("补丁 SHA-256 不匹配，已停止 · Patch digest mismatch, stopped")

subprocess.run(["git", "apply", "--check", patch], check=True)
subprocess.run(["git", "apply", patch], check=True)
for path, (_, original) in expected.items():
    actual = digest(path)
    if actual != original:
        raise SystemExit("还原校验失败 · Verification failed: " + path)
    print(actual, path)
PYRESTORE
```

[标准 Git 补丁 / Unified diff](github-web-checkout.patch) · [实测回执 / Recorded verification](../evidence/ai-production/source-checks/public-checkout-byte-restore.json)
