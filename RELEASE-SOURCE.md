# AI Ship 制作台源码交付

本版在同一项目中完成 AI 编排、实帧审阅、局部改镜、人工接管与导出。制作台新增可选的首镜原片文字核验，窄屏控件和横向音量操作也已修正。

从公开仓库固定基准提交 `a29463c1a9e55ffb6686b38cec0c54b57fc58e20` 顺次应用仓内两份标准 Git 补丁，可以还原开发提交 `0a919ab5765c72065cef83f1d683bc41ab83440f` 的 **220 个源码、配置、测试和开发文档文件**。工程树为 `afaa47ec9fdeede665373dfcf52f227db0688f82`。完整工程压缩包还含逐轮视频、截图、逐例结果与最终审计文档。

| 仓库文件 | 用途 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| [verified-source.patch](verified-source.patch) | 还原原始 HTML、全量合同、基础实现和测试 | 2,897,639 | `2391360e928ee750700500de2c7046af68831bb393ae03de7ded704b72be8be8` |
| [production-update.patch](production-update.patch) | 应用首帧核验、触控调整、配套测试和文档 | 246,801 | `5a1b68d127f3e7f826fa05495a227fd7ab599c169d57deb6336a28e57e315e4b` |

下面的命令用 Git 和 Python 3 创建独立工作目录，校验两份输入，逐份预检并应用补丁，最后比对整棵源码树。目录由终端最后一行给出。

```sh
set -eu
work_dir=$(mktemp -d)
base_commit=a29463c1a9e55ffb6686b38cec0c54b57fc58e20
git clone --quiet --no-checkout --single-branch https://github.com/zhikaofengyun/ai-ship.git "$work_dir/github"
git -C "$work_dir/github" config core.autocrlf false
git -C "$work_dir/github" worktree add --quiet --detach "$work_dir/source" "$base_commit"
for patch_name in verified-source.patch production-update.patch
do
  git -C "$work_dir/github" show "origin/main:$patch_name" > "$work_dir/$patch_name"
done
python3 - "$work_dir" <<'PY'
import hashlib
import sys
from pathlib import Path
root = Path(sys.argv[1])
expected = {
    "verified-source.patch": "2391360e928ee750700500de2c7046af68831bb393ae03de7ded704b72be8be8",
    "production-update.patch": "5a1b68d127f3e7f826fa05495a227fd7ab599c169d57deb6336a28e57e315e4b",
}
for name, digest in expected.items():
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
        raise SystemExit(f"文件校验失败 · {name}")
PY
for patch_name in verified-source.patch production-update.patch
do
  git -C "$work_dir/source" apply --index --check --whitespace=nowarn "$work_dir/$patch_name"
  git -C "$work_dir/source" apply --index --whitespace=nowarn "$work_dir/$patch_name"
done
test "$(git -C "$work_dir/source" write-tree)" = afaa47ec9fdeede665373dfcf52f227db0688f82
printf '%s\n' "$work_dir/source"
```

进入输出的路径后，按 [工作台启动说明](README.md) 安装项目依赖并运行 `python scripts/run.py`。本机导演和图生视频服务各有独立接入步骤。[本机导演启动](docs/local-director-start.md) · [制作与生成接口](docs/ai-production-runtime.md)

The two checked patches reconstruct the application source, configuration, tests and development documentation from the public Git baseline. All 220 paths, file modes and Git blobs were compared to the frozen revision. Regression media, delivery packaging and subsequent audit reports are provided in the complete Git bundle.
