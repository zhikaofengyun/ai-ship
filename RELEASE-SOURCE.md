# AI Ship 源码交付 · Source release

从公开基准提交 [a29463c](https://github.com/zhikaofengyun/ai-ship/commit/a29463c1a9e55ffb6686b38cec0c54b57fc58e20) 可还原冻结版本 `531c292692a7431d762ac7964a1592156bacd4e5` 除 `evidence/` 外的全部 213 个跟踪文件。新增 100 个、修改 6 个、删除 1 个误放路径，107 个原样保留。补丁同时还原了原始 HTML 和三个合同 JSON 的文件尾字节。[下载标准 Git 补丁](https://raw.githubusercontent.com/zhikaofengyun/ai-ship/main/verified-source.patch)

The patch reconstructs every tracked source, configuration, test and documentation file outside `evidence/` at the target revision. The complete Git bundle is a separate delivery for regression evidence and final quality-audit material. The isolated checkout below keeps your current working tree intact.

| 核验项 / Verification | 值 / Value |
| --- | --- |
| 补丁字节 / Patch bytes | 2,897,639 |
| 补丁 / Patch SHA-256 | `2391360e928ee750700500de2c7046af68831bb393ae03de7ded704b72be8be8` |
| 还原后源码树 / Restored source Git tree | `f12dd83aedff417a63a70c626e6b635b6e77b2b6` |
| 还原后源码 / Restored source | 213 files, 29,467,122 bytes |

在有 Git、curl 和 Python 3 的终端中整段执行。命令新建独立目录、取回指定公开提交、核验补丁 SHA-256，先干跑，再写入该目录的工作树及索引，最后复核整棵树。若已下载补丁，可在执行前设置 `PATCH=/绝对路径/verified-source.patch`。当前发布位置使用 `main`，摘要将下载内容锁定到本版。

Run this block in a POSIX shell with Git, curl and Python 3. It fetches the immutable base commit into a new repository, checks the downloaded bytes, stages the patch in a separate worktree and prints the resulting source location. Set `PATCH` to an existing local copy to skip downloading the patch.

```sh
set -eu
base=a29463c1a9e55ffb6686b38cec0c54b57fc58e20
stage=$(mktemp -d)
patch_path=${PATCH:-"$stage/verified-source.patch"}
if [ -z "${PATCH:-}" ]; then
  curl --fail --location --silent --show-error --retry 2 \
    https://raw.githubusercontent.com/zhikaofengyun/ai-ship/main/verified-source.patch \
    --output "$patch_path"
fi
patch_path=$(python3 - "$patch_path" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys
path = Path(sys.argv[1]).resolve()
data = path.read_bytes()
if len(data) != 2897639 or sha256(data).hexdigest() != "2391360e928ee750700500de2c7046af68831bb393ae03de7ded704b72be8be8":
    raise SystemExit("补丁校验失败 · Patch checksum mismatch")
print(path)
PY
)
git init --quiet "$stage/repository"
git -C "$stage/repository" config core.autocrlf false
git -C "$stage/repository" fetch --quiet --no-tags --depth=1 \
  https://github.com/zhikaofengyun/ai-ship.git "$base"
git -C "$stage/repository" worktree add --quiet --detach "$stage/source" "$base"
test "$(git -C "$stage/source" rev-parse HEAD)" = "$base"
git -C "$stage/source" apply --index --check --whitespace=nowarn "$patch_path"
git -C "$stage/source" apply --index --whitespace=nowarn "$patch_path"
tree=$(git -C "$stage/source" write-tree)
test "$tree" = f12dd83aedff417a63a70c626e6b635b6e77b2b6
printf '源码位置 / Source directory: %s\nGit tree: %s\n' "$stage/source" "$tree"
```
