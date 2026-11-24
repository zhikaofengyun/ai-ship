# 浏览器回归运行环境

本项目的浏览器回归对实际 FastAPI 服务、隔离 SQLite 工作区与真实媒体文件运行。测试浏览器是独立进程，不接管用户浏览器、不借用用户会话，也不访问真实发布账号。截图与 DOM 断言需要分别检查；页面有文字不能证明布局正确。

## Chromium 运行包

| 项目 | 记录 |
| --- | --- |
| 发行版 | Debian GNU/Linux 13（trixie），amd64 |
| 浏览器 | Debian `chromium-headless-shell` |
| 实际执行版本 | `Chromium 156.0.8078.108` |
| 软件包版本 | `156.0.8078.108-1~deb13u1` |
| 浏览器包大小 | 69,342,228 字节 |
| 浏览器包 SHA-256 | `7e062463d4db328a3d543445451fc18cf5a1555486caa5fb8f80f550873232cd` |
| 配套资源 | 同版本 `chromium-common`，31,706,952 字节 |
| 资源包 SHA-256 | `f829dd3e11eca4b9163af4fd134003a056ba37ee9dccd282bdd480a9a83040dc` |
| 获取方式 | Debian 官方安全更新 HTTPS，下载后对照官方包页面 SHA-256；仅在临时测试目录提取 |
| 启动方式 | Playwright 指定测试可执行文件，`headless=True, chromium_sandbox=True` |

官方来源：

- [Chromium Headless Shell 包说明](https://packages.debian.org/trixie/chromium-headless-shell)
- [此架构下载页及 SHA-256](https://packages.debian.org/trixie/amd64/chromium-headless-shell/download)
- [同版本公共资源下载页及 SHA-256](https://packages.debian.org/trixie/amd64/chromium-common/download)
- [浏览器包](https://security.debian.org/debian-security/pool/updates/main/c/chromium/chromium-headless-shell_156.0.8078.108-1~deb13u1_amd64.deb)
- [资源包](https://security.debian.org/debian-security/pool/updates/main/c/chromium/chromium-common_156.0.8078.108-1~deb13u1_amd64.deb)
- [Chrome 官方 Headless 使用说明](https://developer.chrome.com/docs/automation-and-testing/headless)
- [Chrome 官方独立 Headless Shell 说明](https://developer.chrome.com/docs/automation-and-testing/headless-chrome-shell)

包版本会更新。上述表格是本次证据，不要求未来开发者回退到这一个版本。更新版本时记录实际版本与来源，重新运行回归即可。浏览器二进制、临时用户数据和软件包不进入 Git 仓库。

## 正常复现路径

Debian CI 优先通过发行版已签名的包源安装 `chromium-headless-shell` 及匹配依赖；也可使用 Playwright 官方安装器提供的 Chromium Headless Shell。安装失败应记录基础设施原因，不修改安全策略、不关闭浏览器沙箱、不重写受限网络规则。

测试脚本优先读取可选的 `AI_SHIP_CHROMIUM`，然后查找 PATH 中的 `chromium-headless-shell` 或 `chromium`。该变量由开发环境配置，不在代码或 CI 中硬编码本次机器的绝对路径。测试依赖和运行命令以仓库的 CI 与测试脚本为准。

```bash
# 已安装发行版浏览器时
python scripts/browser_regression.py --output evidence/browser-local --round local

# 仅开发检查：不计为全量回归轮次
python scripts/browser_regression.py --output evidence/browser-quick --round development --quick
```

脚本在同一进程环境启动独立服务，等待真实 `/api/v1/health`，再让浏览器访问该服务。每次使用隔离目录，结束后回收服务与浏览器；没有用固定演示数据替换生产 API。

## 已观察的环境问题与处置

1. 环境已有的完整 Chromium `156.0.8078.79` 在创建 ProcessSingleton Unix socket 时收到 `Operation not permitted`，无法正常启动该路径。没有关闭这项限制。
2. 本次 Playwright CDN 下载 Headless Shell 返回 HTTP 403；没有反复更换代理或规避网络控制。
3. 独立 Debian 官方 Headless Shell 使用默认启动参数成功输出测试 HTML DOM，退出码为 0；随后以 `chromium_sandbox=True` 供真实项目回归使用。DBus、NETLINK 和硬件设备缺失提示是已观察到的非致命告警，不能用来推定相关硬件能力已受测。
4. 云浏览器与普通测试服务的 localhost 不是同一个网络命名空间。平台支持的预览应使用 `sites-preview` 返回的原始 URL；不将无法访问的 localhost 页面当作产品错误，也不操作已被策略拒绝的浏览器页面。

## WebKit 剪辑回归

Linux 上使用 Playwright 官方 WebKit 26.5（构建号 2336，WPE MiniBrowser）。与 Chromium 共用 `scripts/browser_edit_session.py`，两种引擎各建独立项目、数据库及临时媒体目录。每次运行在同一项目内从导入走到下载 MP4，再通过独立的手机模拟上下文添加字幕，从服务端核实写入结果。Chromium 使用 Pixel 7 配置，WebKit 使用 iPhone 15 配置。测试记录浏览器分发的可信触摸事件数量、视口、用户代理、真实帧像素与产物哈希。

安装项目测试依赖后，在支持的 Linux CI 环境执行以下命令。Playwright 的系统依赖安装需要相应的系统权限。

```sh
python -m playwright install --with-deps webkit
python scripts/browser_edit_session.py --engine webkit --round development --output evidence/development-webkit-editor
```

在 Debian 上验证 AAC/H.264 时，GStreamer 也需要能解码这些输入。本次使用 Debian trixie 的 `gstreamer1.0-libav` 1.26.2-1+deb13u1。测试环境缺少的 GTK、Graphene、HarfBuzz ICU、Manette、Hyphen、GLES、Cairo、HID 等运行库均从 Debian 官方软件包解至临时目录。Playwright 的首个下载源请求失败，随后其官方后备下载源返回了对应构建。启动使用发行包内的 `pw_run.sh` 和默认安全设置。

已安装系统依赖的开发机可直接使用上述命令。自定义运行目录可按实际绝对路径设置以下环境变量。

| 变量 | 何时设置 |
| --- | --- |
| `PLAYWRIGHT_BROWSERS_PATH` | 浏览器安装在自定义缓存目录时 |
| `AI_SHIP_WEBKIT` | 使用指定的 Playwright WebKit `pw_run.sh` 时 |
| `GST_PLUGIN_PATH_1_0` | GStreamer 插件位于私有运行目录时，指向 `gstreamer-1.0` 插件目录 |
| `LD_LIBRARY_PATH` | 测试运行需要额外共享库目录时。若使用临时解包的 WPE/GTK，还要保证包内启动器的共享库搜索路径能够找到这些库 |

[Playwright 官方浏览器与依赖安装说明](https://playwright.dev/python/docs/browsers) · [WebKitGTK 项目](https://webkitgtk.org/) · [Debian GStreamer libav 软件包](https://packages.debian.org/trixie/gstreamer1.0-libav)

### 黑色缩略图的定位与验证

同一条彩色测试片在 WebKit 中曾出现主监视器有画面、时间线缩略图全黑。对同一源文件与相同取样位置做了直接比较。在尚未挂载到页面的 `video` 上，即使 `loadeddata` 和 `seeked` 都已触发，Canvas 采样的红绿蓝三通道仍全为 0。将该解码元素挂载到页面后，0.3 秒和 1.5 秒处测得通道最大值 244 和 243。应用现在为取帧临时挂载一个透明、不可聚焦且不接收指针事件的解码元素，取样结束即移除。画面来自所选素材版本的实际三个位置。

共享回归对专门生成的彩色测试片检查可见预览，并解码每段时间线缩略图的 JPEG，分别验证三个颜色通道的变化范围。检查覆盖桌面、小屏布局和独立触控会话。只确认浏览器成功产生 JPEG，无法发现这个问题。此处的颜色阈值只用于已知彩色色卡，用户素材中的真实黑场应保留。用户浏览器暂时无法生成缩略图时，时间线仍展示素材名称、片段位置和轨道颜色，剪辑和服务端渲染可继续使用。

## 证据解释边界

截图证明的是特定浏览器版本、视口和当时页面状态的实际渲染。真实媒体渲染、媒体解码、接口权限、版本冲突、任务状态和原文覆盖检查需要对应的独立证据。旧版有问题的开发截图应保留为缺陷发现记录，不能被计为通过。

剪辑链路覆盖 Linux Chromium 与 Linux WPE WebKit。macOS Safari、物理 iPhone/iPad、Firefox、原生 Liquid Glass 光学等效、系统读屏和平台外发另需对应环境验证。其余模块及五轮正式回归范围以各轮报告为准。
