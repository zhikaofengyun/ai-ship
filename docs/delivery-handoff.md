# 仓库交接与远端状态

目标仓库：<https://github.com/zhikaofengyun/ai-ship>。本次读取时远端分支列表为空。本版再次尝试写入 `.gitignore` 时，GitHub Contents API 返回 `403 Resource not accessible by integration`。没有任何文件因此进入远端，也没有运行该仓库的 GitHub Actions。本地 Git 推送也因缺少当前登录身份而终止。两条路径的返回值见 `evidence/redesign/github-write.json`。

本地 Git 提交与完整交接包可以离线打开。源码、合同、原始 HTML、测试与证据均随提交保存。请使用包外提供的 SHA-256 和本地 commit ID 核对具体版本；不要把本地 SHA 当作已存在于 GitHub 的远端提交链接。

## 恢复当前连接的写权限

在 [GitHub 已安装应用](https://github.com/settings/installations) 中选择用于当前连接的应用，核对安装账号与 `zhikaofengyun/ai-ship` 的仓库选择。应用需要对该仓库有 Contents 写权限；本次提交包含 `.github/workflows/ci.yml`，使用 GitHub App 写入该目录还需要 Workflows 权限。应用未请求相应权限时，仓库管理员单纯选择仓库并不会为它创造这些权限。不要把个人访问令牌贴在聊天中。

GitHub 官方说明：[API 权限不足的错误](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)、[调整安装范围](https://docs.github.com/en/apps/using-github-apps/reviewing-and-modifying-installed-github-apps)、[Contents / Workflows 权限](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app)。

## 已有授权开发机从完整提交推送

交接包中包含普通源码目录以及保留精确 Git 提交的 `ai-ship.bundle`。查看源码不需要 Git；希望原样推送时使用 bundle：

```bash
git clone --branch main ai-ship.bundle ai-ship-checkout
cd ai-ship-checkout
git remote set-url origin https://github.com/zhikaofengyun/ai-ship.git
git ls-remote --heads origin
git push --set-upstream origin main
```

这些命令使用开发机自身已有的 GitHub 身份。`git push` 不带 `--force`；如果远端 main 已有不同提交，应先拉取、比较并合并或改走审阅分支，不能覆盖其他人的工作。

推送后使用 `git ls-remote origin refs/heads/main` 比对本地 `git rev-parse HEAD`，并单独检查目标仓库 Actions 的 regression/container 两项结果。CI 配方在本次本地已有，但未经实际 GitHub 运行前，CI 与容器构建状态均是**未验证**。
