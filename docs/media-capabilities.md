# 真实媒体执行范围与接口

本文件描述 `app/media.py` 的当前实现。原始需求是 `contracts/atlas.json`，完整范围不可用本表替代。测试使用 FFmpeg `testsrc2` 和 `sine` 产生的明确合成样本，没有真实商品、授权账号或运营指标。

## 已实现、部分实现与未实现

| 原文功能 | 当前可运行行为 | 等级与边界 |
|---|---|---|
| A01、A05、A07 上传、精确去重、断点导入 | 原始字节上传；SHA-256；逐文件条目；4 MiB 分块；重复块幂等；数据库重开后继续；整体校验才入库 | **实际执行**。浏览器批次选择可逐文件使用接口；不是任意后台磁盘扫描。单条文件形成批次记录，多文件父批次需要前端进一步编组 |
| I01、I02 媒体探测与原件归档 | FFprobe 读取真实流；租户独立寻址；不可覆盖的内容字节；同租户同项目同字节复用 Asset，同时保留每次来源 | **实际执行**。探测失败留下失败条目，不创建可用素材；不代表媒体内容/来源已经获批 |
| I03、I04、I07 衍生与处理配方 | 裁切时间、恒定变速、90 度倍数旋转、翻转、尺寸与中心缩放、亮度/对比度/饱和度/gamma、锐化、增益、响度归一；保存实际参数、版本、输出 hash 与 TimeMap | **部分实现**。每项实际调用 FFmpeg。没有 AI 去水印、修补、插帧、蒙版跟踪、自动冗余帧判断、随机图层配方或任意算子排序。未知参数明确拒绝 |
| L01 镜头切分与采样 | 基于 FFmpeg scene difference 的真实解码 PTS 生成 Shot 候选；指定时刻返回真实 PNG | **部分实现**。这是画面差异检测；没有假称主体、商品数量、动作或语义已识别 |
| L07 原声提取 | 从真实音轨提取 48 kHz PCM WAV；建立 Audio 与来源/版本关系 | **实际执行**。不填造 ASR/词级时码 |
| B01、B02 音乐理解与节拍 | 2 kHz 单声道、20 ms 窗口 RMS 正向变化产生起音候选；波形导航及真实音轨试听。人工拍点/强拍/段落可分开标记、保存、再编辑及投射到剪辑时间线 | **部分实现**。人工标记按真实采样率存储；模型节拍、音乐情绪与音乐许可未验证 |
| B05、B06 混音与技术同步 | 音轨剪裁、恒定变速、增益、延时、混合、峰值限制；有目标时运行 loudnorm 并保留其测量报告 | **部分实现**。不宣称响度以外的听感、语义同步或音乐权利通过 |
| E01、E02、E03 时间线与基础编辑 | 统一 JSON 时间线；真实原件/衍生/成片 Range 播放；剪裁、排序、分轨、恒定变速、音量、静态位置与透明度 | **实际执行**的指定子集。不得把复杂专业编辑器完整能力算在内 |
| E04 字幕与文字 | 导入与导出 SRT/WebVTT 的逐句纯文本；实际文字文件+FFmpeg drawtext，指定起止时间、字号、颜色；中文字体在运行环境存在时使用 | **部分实现**。字幕翻译、ASR、逐词时码、复杂排版和语义可读性审阅没有自动完成 |
| E05 画面与关键帧 | 多轨叠层、位置、透明度；clip-local output 秒上的 x/y/opacity 线性关键帧，首尾保持 | **部分实现**。其他属性、非线性缓动、复杂转场、蒙版、速度坡道等明确拒绝；不会仅保存参数然后宣称已渲染 |
| Y01、Y03 渲染冻结与后端 | 冻结 Timeline revision、配置与真实媒体引用；FFmpeg H.264/AAC MP4；宽高、整数或有理数 fps 可选 | **实际执行**的本地后端。没有已接通的 HyperFrames/其他渲染后端 |
| Y04 恢复 | 任务与实际错误落账；原件及之前通过的产物保留 | **部分实现**。当前媒体函数是有界同步进程，不是分布式媒体 worker；没有进程崩溃后的编码段级续算 |
| Y05 全片质检 | 实际文件存在、输出尺寸、时长容差、音轨存在、全片严格解码 | **部分实现**。输出为 `technical_passed`，`content_review_required=true`、`publish_ready=false`。商品事实、许可、审美、字幕语义、关键可读性和语义音画对齐要另有审阅证据 |
| Y06、Y08、Y09 交付与谱系 | 成片、RenderManifest、Usage、TimeMap；ZIP 包含实际视频、冻结时间线、渲染清单、时码映射和缺口说明 | **部分实现**。可用于 AI-ship JSON 回导；没有专有编辑器格式适配，ZIP 未嵌入原始素材 |
| D03、D08 真实对象与映射 | 双向事实引用、租户独立 BlobLocation、SHA-256、实际变换/渲染输入输出版本 | **部分实现**。TimeMap 是实际操作的区间映射，标 approximate；源 VFR 逐帧 PTS 对应、逐像素遮挡和音频限制器后贡献不声明完全已核 |
| G02/G03、V03/V04/V06、自动内容理解 | 接口可由其他 provider adapter 接入 | **本模块未实现**。不会生成假图片/假视频/假词时码/假内容审核 |

## 状态与来源边界

原件、实际衍生、人工声明和机器推断分开保存。上传的来源备注仅为声明，空备注为 `unverified`。它不证明权利有效。成片技术验证通过也不自动生成发布许可；最终发布必须由控制面校验对应事实、授权与确认范围。

TimeMap 采用整数微秒区间和真实输入版本引用。恒定速度与剪裁、不同图层和音轨分别留下贡献。透明度曲线记录为 layer weight before compositing；音量记录为 gain before master limiter。它不把全片播放/销售归因给某个片段，不把层透明度误称最终逐像素可见权重。未完成全量 VFR PTS 校验时 `tolerance_seconds=null`。

## 接口

所有路径以 `/api/v1` 开头，JSON 返回 `{data,trace_id}`，错误为规范 DomainError。写入需要 owner/editor/worker 身份。租户与角色来自服务端身份，不来自请求中的任意租户参数。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/media/capabilities` | 实际 adapter 能力、未实现项、资源限制、二进制版本 |
| POST | `/media/import?project_id=...&filename=...` | 原始字节导入；`X-Source-Note` 可选声明 |
| POST | `/media/uploads` | `{project_id,filename,size_bytes,content_hash?,source_note?}` 建立续传条目 |
| GET | `/media/uploads/{entry_id}` | 查看持久化 offset 与逐块校验 |
| PUT | `/media/uploads/{entry_id}?offset=N` | 原始分块字节；可带 `X-Chunk-Sha256`；同偏移同内容返回幂等结果 |
| POST | `/media/uploads/{entry_id}/complete` | 完整 hash、媒体探测、真正入库；已完成重复请求返回原素材 |
| DELETE | `/media/uploads/{entry_id}` | 取消未完成上传，清理临时字节，保留状态事实；已入库不会被当作未发生 |
| GET | `/media/{id}/file` | 支持 Range 的真实字节；MIME 来自探测，不信任文件名后缀 |
| GET | `/media/{id}/frame?time=0.8&width=640` | 指定实际时刻的 PNG 帧 |
| GET | `/media/{id}/waveform?buckets=180` | 真实采样波形，明确仅供导航 |
| POST | `/media/{id}/detect-shots` | `{threshold:0.3}`；真实 PTS 的差异镜头候选 |
| POST | `/media/{id}/analyze-audio` | 实际短时能量变化候选；不推断 BPM |
| POST | `/media/{id}/extract-audio` | `{project_id,expected_revision?}`；真实 PCM 音轨 |
| POST | `/media/{id}/transform` | `{project_id,expected_revision,recipe}`；保原件并产生衍生版本 |
| POST | `/media/render` | `{project_id,timeline_id,expected_revision,profile?}`；真实输出+清单 |
| GET | `/media/artifacts/{id}/file?download=true` | 下载真实成片 |
| GET | `/media/artifacts/{id}/package` | 创建并下载可核验交付 ZIP，需要写权限登记交付事实 |

## 时间线与配方格式

时间线使用 `clips` 或兼容字段 `clip_uses`，二者不能同时出现。下面是真实支持的格式；它没有声明语义事实已经通过。

```json
{
  "name": "产品展示候选",
  "locked": true,
  "format": {"width": 1080, "height": 1920, "fps": {"num": 30000, "den": 1001}},
  "clips": [
    {
      "id": "clip-1",
      "asset_id": "actual-asset-id",
      "source_in": 0,
      "source_out": 5,
      "timeline_start": 0,
      "track": 0,
      "speed": 1,
      "volume": 1,
      "opacity": 1,
      "x": 0,
      "y": 0,
      "fit": "contain",
      "keyframes": {"opacity": [{"time": 0, "value": 0}, {"time": 0.4, "value": 1}]}
    }
  ],
  "captions": [{"text": "以实际商品事实为准", "start": 0.5, "end": 3, "font_size": 48, "color": "#ffffff"}],
  "audio_tracks": []
}
```

`source_in/source_out` 是源秒，`timeline_start` 是输出秒，关键帧的 `time` 是片段局部输出秒。速度变化会改变输出时长；明确的 `duration` 必须与实际覆盖一致。关键帧要求有限数值、范围有效、同曲线同时间无冲突。人工锁与其他变更权限仍由控制面的版本事务处理。

配方支持 `trim_start/trim_end/scale/width/height/brightness/contrast/saturation/gamma/sharpness/rotation/flip_horizontal/flip_vertical/speed/volume/loudness_lufs/true_peak_db`。原文预处理模板须显式设 `template=atlas_preprocess`，该模板 scale 限 1.011–1.022；自由变换可选择非模板 scale 1–4。未提供的值只采用恒等操作或明确执行器默认，并逐项记录 `parameter_origins`，不伪称测量推荐。现有固定滤镜顺序存入实际运行记录，不把它冒充原文全部智能预处理算子。

## 资源与故障边界

- 单文件 256 MiB；最长 3600 秒；输出最多 4096×2160 总像素；最多 64 个画面片段与 16 个额外音轨。
- 每媒体进程最多 300 秒，最多两个并行子进程，每编码进程两个线程。超时杀掉整个进程组。
- 不使用 shell；用户不能提交任意 FFmpeg filter expression、绝对路径或 `..` 跳转。媒体读取协议限定为本地 file/pipe，FFprobe 只接受明确媒体格式。
- 所有素材、版本和对象位置都验证当前租户。授权检查沿实际冻结输入递归追溯，读取每个来源和 Grant 的当前撤权/到期状态；不能靠旧清单绕开后来新挂接的授权。最多检查 1000 个版本、10000 条引用、64 层；超限明确阻塞，不把未检查部分算作通过。
- `Source.expires_at`、`Grant.expires_at`、`valid_until`、结构化 `validity.valid_until/valid_from` 使用带时区的 ISO 时刻比较。已到期、尚未生效、日期无法确认、明确禁止处理时拒绝新制作/导出；撤权、删除或受限拒绝所有新字节读取。原件/历史成片的检查预览和波形可以审计已到期素材，不创建新的授权。下载与交付包走导出检查。
- 上传说明只保留来源声明，`Source.rights_status=unverified`、`source_note_is_evidence=false`；没有到期字段既不代表过期，也不代表权利已经成立。允许本地技术草稿处理不等于获准商业使用。所有输出仍须精确内容/用途/权利审阅，不能从技术 QC 推导为 `publish_ready=true`。用途、市场和分发范围的完整权利政策引擎尚未实现；当前只实施明确限制与日期守卫。
- 转换、抽音、镜头检测、音频分析、渲染与打包落库前再次检查当前授权；处理期间撤权/到期不会留下可用产物事实。已经开始的文件响应不是远程撤回机制。
- 上传原子完成使用 Linux 文件锁。Windows 运行服务建议 Docker/WSL；缺文件锁时明确报不可用，不声称具有跨进程一致性。
- 分块字节已落盘但事务未提交时，恢复会丢弃未确认尾部。分块 manifest 和进度只在真实字节 fsync 后推进。完整校验之前不会产生可用 Asset。
- 这不是容器/VM 强隔离实现，也不是横向分布式媒体调度器。单机资源上限不等于完整生产沙箱。服务器硬中断、目录断电一致性、孤立对象回收、租户存储配额与分布式恢复仍需对应运维验收。

## 可复现证据

运行：

```bash
python -m unittest tests.test_media -v
AI_SHIP_MEDIA_EVIDENCE_DIR=docs/evidence/round-1/media python -m unittest tests.test_media -v
```

设置证据目录后会保存 `synthetic-source.mp4`、`synthetic-render.mp4`、`synthetic-frame.png`、`render-evidence.json`。JSON 包含真实文件 hash、工具版本、冻结时间线、渲染状态、TimeMap、Manifest 与 QC。关键帧测试检查实际解码帧像素在透明度变化前后的差异，避免只检查参数被保存。

媒体专项目前有 18 项测试，新增覆盖带时区授权边界、深层衍生成片的当前 Grant 核验、未确认许可证不升格、授权图上限、冻结依赖与后续无关编辑的隔离，以及实际解码后权限过期的提交拒绝。

这些是媒体子系统测试，不能独立称为用户要求的一轮“整个产品全量回归”。完整轮次必须同时覆盖其他模块、接口、前端与未测外部边界。

## 官方依据

- [FFprobe 官方文档](https://ffmpeg.org/ffprobe.html)：使用结构化 stream/format 输出读取实际媒体元数据。
- [FFmpeg Filters 官方文档](https://ffmpeg.org/ffmpeg-filters.html)：trim/atrim、setpts/asetpts、overlay、drawtext、loudnorm、scene select 和滤镜表达式均按实际安装版本执行并通过测试。loudnorm 的测量/目标独立于人工内容审阅。

查阅日期：2026-11-23。支持与否以 `/media/capabilities`、实际工具版本和测试结果为准；没有因为官网最新文档包含某滤镜就声称当前执行器已实现该功能。


### 人工音乐打点与字幕交换

音乐模块 B02 通过 `POST /api/v1/production/music/markers` 保存真实音轨的人工标记。普通拍点、强拍和段落各有独立结果与来源。标记以实际播放起点为零，保留秒值及就近采样点。使用者可以选择关闭卡点，也可以在剪辑室选择同一音轨，把源时刻按已剪区间、起点和恒速映射到项目。音轨版本、项目归属与音频字节在服务端核实。来源文件仅含平台引用时不能打点。浏览器播放时钟依据 [HTML Standard](https://html.spec.whatwg.org/multipage/media.html)。

字幕编辑 E04 的 `POST /api/v1/media/captions/parse` 和 `/export` 接受 UTF-8 纯文本 SRT 与 WebVTT。两种格式保持分句时钟，不推导词级声学时码，复杂样式/定位在导入时给出原文件行号。WebVTT 语法依据 [W3C WebVTT](https://www.w3.org/TR/webvtt1/)。
