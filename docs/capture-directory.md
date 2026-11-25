# 实拍目录与离线交付清单

在素材库或 A01、A05、A06 功能页打开「采集目录」。管理员先在服务环境配置 `AI_SHIP_CAPTURE_ROOTS`，按租户、项目和逻辑名授权一个本机目录。例如以下配置只向指定项目开放一个机位的采集目录。使用容器部署时，需要把来源目录以只读卷挂到对应位置。

```json
[{"tenant_id":"local","project_id":"project_actual_id","alias":"camera-a","path":"/mnt/camera-a","stable_seconds":10}]
```

默认配置为空。服务运行身份必须有目录遍历和文件读取权限。仅在打算授权租户内所有项目读取同一目录时使用显式的 `project_id: "*"`。授权根及其祖先目录须为真实目录。浏览器只收到别名和稳定等待时间。Linux/POSIX 实现按组件用目录文件描述符打开文件，不跟随软链接，跳过硬链接、设备及不支持的扩展名。单件上限沿用媒体层的 256 MiB。

## 交付与恢复

留空清单会递归观察整个根目录。也可每行粘贴一个相对路径，或在路径后加制表符和已交付文件的 SHA-256，以固定应接收的完整内容。建议采集端写入临时名，关闭后原子改名为成品，或交付预期摘要。稳定间隔判断文件属性和读取期间的字节变化。写入端长时间暂停又不持排他文件锁时，稳定间隔本身不能证明拍摄已经结束。

点击「观察并导入」后，队列持久记录每个文件的设备、inode、大小、修改时间和元数据变更时间，经过稳定窗口后重新核对。支持协作式文件锁的写入端可持 `flock(LOCK_EX)`。系统对读出的私有快照计算 SHA-256、核对可选清单、执行真实 FFprobe，并在素材、来源和接收回执提交时重新验证物理文件和根目录。链接变化、坏文件、摘要错误均逐项显示。其他条目继续处理。已知摘要命中时复用项目素材，同一内容的不同原始路径各自保留来源。原路径换入新内容会创建新素材，原素材与既有使用引用均保留。文件不再出现在目录中不会自动撤销素材权利。

浏览器每次推进最多 16 件。关闭弹窗或按「停止观察」后，当前已受理的一小组可继续完成。下次打开相同目录和清单可以查看旧队列、重试失败项或再次观察。当前窗口处理一次清单，并不常驻监视之后才出现的文件；拍摄端持续交付时可按需调用 API 定时重新观察。

## API

| 调用 | 用途 |
| --- | --- |
| GET `/api/v1/production/directory-imports/roots?project_id=…` | 列举当前项目获授权的采集目录 |
| POST `/api/v1/production/directory-imports/observe` | 提交 `project_id`、`root` 和可选 `manifest: [{path,sha256}]`。返回持久化的 `snapshot`、全量计数及首页 |
| POST `/api/v1/production/directory-imports/advance` | 提交 `project_id`、`root`、`snapshot`，以及可选 `limit`（1–64）和 `retry_failed`。中断或并发重放复用已经登记的接收回执 |
| POST `/api/v1/production/directory-imports/items?after=…&limit=…` | 用相同根和快照分页读取。每项提供相对路径、状态、原始字节摘要、素材、来源和接收条目 ID，或失败原因 |

离线清单单次最多 50,000 个路径。完成后重新发现目录变化需再次提交原清单或全目录观察。查看和推进使用短快照标识即可。来源的权利状态仍按既有媒体审查流程核对。

本地验证运行 `python -m unittest tests.test_directory_import tests.test_media`；真实界面验证运行 `python scripts/browser_directory_intake.py --output /tmp/directory-check`。后者用 FFmpeg 生成一段媒体，复制为第二个物理文件，在 Chromium 中从素材库导入并核实一件素材和两个独立来源。
