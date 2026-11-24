# AI 制作工作区的运行合同

制作任务从项目目标和用户勾选的有时长视频开始。原 HTML 的 N01/N02/N03 对应结构与选片，C01/H01/H03/H12 对应角色、输入输出和路由，G02/G03/G04/G05/G06 对应缺口生成、核对与局部重做，E01/E08/Y01/Y05 对应可编辑时间线、锁版、渲染和质检。所有专用接口均复用项目身份校验、不可变版本和持久事件。前缀为 `/api/v1/production`，具体字段见 [OpenAPI](../contracts/openapi.json)。

| 操作 | 实际写入和后继 |
| --- | --- |
| `POST /auto-edits` | 保存制作请求、目标准入、真实视频采样和模型配置版本。形成待审 Plan。失败时保存原因与现有调用收据。相同请求键返回原任务。 |
| `GET /auto-edits?project_id=…` 与 `GET /auto-edits/{id}` | 恢复同一项目的阶段、制作提案、当前可编辑时间线、成片和技术质检。 |
| `POST /auto-edits/{id}/replace-shot` | 明确目标镜头、来源、区间和原因。新提案固定旧 Plan 修订，保留其他镜头的素材及区间，只重新计算后续时间位置。前一提案、已采用的工程和成片都可继续查阅。 |
| `POST /auto-edits/{id}/approve` | 有审阅权限的用户确认当前版本。服务端重新核实素材状态并原子创建绑定精确 Plan 修订的 Approval 与标准 Timeline。可交给剪辑室接管。 |
| `POST /auto-edits/{id}/render` | 接受明确的运行与时间线版本。冻结画布和时间线后由 FFmpeg 真实编码、解码，提交 Artifact 与 Audit。重复请求可关联已完成的同一锁版成片。旧修订冲突保留草稿。 |
| `GET /generation/profiles` 与 `GET /generation?project_id=…` | 返回管理员登记的本地图片、视频生成配置或本项目的真实请求。登记状态和运行成功分开显示。 |
| `POST /generation` | 绑定可选父制作任务的当前 Run、Plan 修订，把文字、种子及要求参考图的工作流绑定到登记节点。发送一次，记录实际提示词、种子、模型声明、流程摘要与供应商任务 ID。 |
| `POST /generation/{id}/refresh` · `POST /generation/{id}/cancel` | 按原任务 ID 核对队列与历史。接收指定输出节点的文件，核对视频轨、时长、对象字节和全片解码。入库后生成事实可作为该项目下一次提案或单镜头替换的输入。 |

## 导演与独立审阅

**本地画面分析**使用 FFmpeg 从项目内真实、校验过的素材读取最多三处原片画面。每帧记录实际 PTS、请求定位值、分区颜色、亮度、对比度和边缘差异。对本批素材的实测特征拟合最多四个构图聚类，再依据时长、相邻画面差别和目标可见词选择区间。可选 Tesseract LSTM 从每个素材中间采样处实际识别文字，记录读取时刻、边框、置信度、分析尺寸及可取得的语言模型文件摘要。英文和已安装的简繁中文识别数据按可用性使用。缺少识别程序或语言包会保留 `unavailable` 或 `language_unavailable`。这条路线没有语言生成模型；OCR 读数也不能单独证明商品事实。

**单模型导演**把明确选择的文件名、测量结果和候选文字发给服务器登记的模型。模型可以给出原片入出点、顺序和选用原因。服务端拒绝项目外素材、虚构来源、超出实际媒体边界的区间、缺失镜头字段和超过目标上限的方案。短于目标时保存具体差额，用户可以决定是否采用。模型不会获得任意工具执行权。

**多角色制作**以三个角色依次工作。每份复核和裁决都须引用本次实测证据 ID、抄录观测值，并在理由中实际使用该数值或识别词。数值与原片不一致、理由没有使用已引证据、或对同长原片作相反比较时，会保留方案供返工并阻止直接采用。这个校验不证明所有自然语言陈述都正确。导演提案后，复核模型只读来源、时段及顺序，自行给出接受或返工意见。裁决模型检查提案、证据和具体异议。复核时原样复制已有模型的一段理由也会触发返工。原案持久保留。审阅者可在独立修订中人工指定一个镜头的证据、素材和区间，再明确采用。程序核对管理员登记的供应商身份与模型身份，裁决必须独立于导演和复核。三个角色共享此次费用上限，每个角色另有单次上限。计价币种必须一致。结果未知时保留费用与调用收据，只核对原任务；裁决要求返工时不产生可采用时间线。真实模型的内容判断仍需人工或其他证据复核。

## 配置实际语言模型

沿用 `AI_SHIP_PROVIDERS` 和项目中的 ModelProfile。远程服务使用管理员登记的 HTTPS 源站、模型允许清单及环境变量凭据；本地服务器可以按下面的配置接受回环请求。已启用鉴权的 llama-server 可在对应配置内设置专用的 `api_key_env`；服务端从该环境变量读取 Bearer 密钥，浏览器不能指定凭据。模型名要与服务真实返回的模型名一致，也可由管理员登记已确认的别名。以下名称和端口为配置示例。

```json
{
  "local-film": {
    "transport": "loopback_chat",
    "base_url": "http://127.0.0.1:8080/v1",
    "models": ["local-director"],
    "provider_identity": "local-cpu-server",
    "json_output": true,
    "structured_schema": true,
    "candidate_menu": true,
    "input_price_per_million": 0,
    "output_price_per_million": 0,
    "currency": "USD"
  }
}
```

项目 ModelProfile 的 `endpoint_ref` 选择 `local-film`，`model_id` 填模型名，`structured_output` 选择 true。零价格表示没有按 token 计费的供应商报价；硬件、电力和运维成本仍取决于实际部署。`candidate_menu` 适合本地小模型。服务端依据真实时长形成若干可执行安排，在候选中让模型真实选择，保留选项 ID、候选数、理由和原响应。画面区间可能相同的候选会去重。关闭此项后模型可提交任意符合合同的合法区间。`structured_schema` 只应对支持 JSON Schema 约束的服务启用。[Ollama 官方兼容接口](https://docs.ollama.com/api/openai-compatibility) · [llama.cpp 官方服务器](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)

## 配置本地图片或视频生成

`AI_SHIP_COMFY_PROFILES` 是配置对象。通过 `output_kind` 明确选择 `image` 或 `video`，既有视频配置仍可省略。每项须有 `base_url`、`model`、完整的 API 版 `workflow`、`text_input`、`seed_input` 和 `output_node`。可加 `label`。例如，将已在 ComfyUI 中成功运行的 API 图的文字输入定位为 `["6","text"]`，种子定位为 `["3","seed"]`，将 `output_node` 指定为保存动态视频的节点 ID。编号以实际图为准。API 校验节点存在、字段名和旧值类型后，只将用户的提示词与种子写入这些位置；前端不能提供服务器地址、脚本或自定义节点。当前允许 `http://127.0.0.1` 和 `http://[::1]` 上由管理员登记的本地服务。本地生成图及其权重、插件、显存和启动环境由部署方提供。

工作流若需要原图，在管理员配置中增加 `"image_input": ["14", "image"]`。节点 14 必须是 `LoadImage`，其输出经图中的真实连接到达本次登记的输出节点。API 返回 `requires_reference: true`。提交时同时给出本项目 `reference_asset_id` 与 `reference_asset_version`，服务读取该版本实际对象，复核项目、媒体类型、字节摘要、来源和当前准入。输入只接受一帧可解码的 PNG/JPEG/WebP/BMP，最大 64 兆像素。FFmpeg 去除原文件元数据，按原纵横比限制最长边 4096 像素，编码成单张 RGBA PNG，最多 50 MiB。用户原图保留原样。

服务向登记的 ComfyUI `/upload/image` 发送 PNG，文件名来自项目范围和规范化字节摘要。它严格核对返回的名称、目录及输入类型，并从该 worker 的 `/view` 读回全部字节核对 SHA-256。ToolCall 保存 `reference_asset_ref`、`reference_input`、`reference_upload`，包括原图修订、原件摘要、送入图的像素尺寸、实际上传摘要、复核时刻和实际图摘要。发出 `/prompt` 前重验权利。原图在生成中撤回时，结果不能采纳入库。上传错误和参考错位会保留失败原因；推理提交后的未知回执循原任务核对。ComfyUI 的 input 目录会保留规范化的参考图副本。当前撤权能阻止新提交及结果入库，工作器已有输入副本的保留期、清除回执和运行中硬件计算停止需要部署方另行处理。独立的分镜外观、角色一致性和用途审阅仍需逐项完成。[ComfyUI 官方上传与读回实现](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py)

生成器返回排队回执时保存其真实 `prompt_id`。连接中断时保留原 `client_id`、请求 ID、工作流和种子，通过队列或历史查找已有任务。相同项目、工作流、提示词及种子还有待核对结果时，换请求键也不会重复提交。取消排队中的任务只删除对应的 ID。运行中取消会抑制本地接收并追踪终态；不会向共享服务发送可能中止其他任务的全局中断。已经完成的删除、取消或校验不会伪造供应商退款或 GPU 停机。

视频配置只有在指定输出节点产生一个可识别的 MP4、MOV、MKV 或 WebM 文件，且实际存在视频流、时长及完整解码输出，才形成可选素材。多个输出、越界文件、损坏文件和静态图片不会被接纳为成片。生成来源与真实 ToolCall、原件版本、批次、账本和文件摘要关联。已存在的相同字节复用原件，独立保留本次生成调用与获取事实。识别模型、拍摄真实性、权利、商品外观和数量需要分别审阅。[ComfyUI 官方服务路由](https://docs.comfy.org/development/comfyui-server/comms_routes) · [ComfyUI 上游源码](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py)

## 静态候选和定格镜头

图片配置接收单个 PNG、JPEG 或 WebP，并检查由文件字节探测出的编码与扩展名一致，独立解码确认为一帧。原始图片保存实际媒体类型和生成链路，可在剪辑室中直接作为静态素材放置。自动选片使用实测时长。如果确实想要静帧镜头，可在原任务上调用 `POST /generation/{id}/hold`，传入 1 至 60 秒的 `duration`、24/25/30/50/60 中的一个 `fps`。处理器以最近的整帧数生成可播放的 H.264，记录用户输入时长、实际时长、帧率、帧数、原图修订及真实技术质检。在同一 Run 中再次请求相同设置可取回原衍生文件。原图与定格视频分别保存；定格输出不具有 AI 生成的画面运动。为了适配本地处理能力，定格时最长边限制为 1920 像素并使用偶数尺寸。

## 可重跑的验证

`python -m unittest -v tests.test_auto_edits tests.test_generation` 使用实际服务接口、真实解码媒体、数据库和 FFmpeg 文件，检查错版本、越界来源、撤权前置、重复提交、多角色独立性、模型无效结果、分镜局部重做、异步生成取消和真实产物接回剪辑。测试中的语言模型与生成服务 HTTP 夹具仅验证协议，夹具返回的 MP4 是明确合成的媒体。真实模型和硬件实测见该版本的独立运行证据。原文工作流缺口与功能完整度需结合逐功能验收。[FFmpeg 官方滤镜](https://ffmpeg.org/ffmpeg-filters.html) · [Tesseract 官方手册](https://tesseract-ocr.github.io/tessdoc/) · [K-means 方法说明](https://scikit-learn.org/stable/modules/clustering.html#k-means)

### 实际三模型检验

在同一项目中分别启动三个带鉴权的本地模型服务器。导演使用 Qwen2.5 1.5B，复核使用 Qwen2.5 0.5B，裁决使用 SmolLM2 360M。真实运行揭示了两个小模型均接受错误证据的情况。服务保留其原始回复，拒绝原方案。测试用的已授权编辑者随后只修正第一个镜头，审阅身份明确采用新案，FFmpeg 导出可完整解码的成片。可在证据目录中核对原任务、新任务、原始词框、用量、模型权重摘要、独立服务日志和视频。[查看三次实际测量及复现参数](../evidence/ai-production/ensemble/README.md)

## 用本机权重直接启动

[本地启动和项目绑定](local-director-start.md)说明如何从正常入口运行已安装的 llama.cpp 与 GGUF，并在当前项目保存可选模型。


### 参考图的使用与拒绝

参考图协议测试从真实 JPEG 开始，经项目导入后转成 PNG，上传至测试工作器并从输入目录读回。入库动作能沿 `生成原件 → 生成来源 → ToolCall → 参考原图及修订` 查回。源图要求与工作流配置不符、图节点未连接、素材属于另一项目、修订已改变、上传回执目录错位、读回内容变化、推理已发送但回执丢失时，都有独立断言。测试还在上传后和推理完成后撤销实际 Source，分别核对停止提交和阻止入库。另将已采用的生成素材实际渲染为 MP4，再撤销原始参考图的 Source。原图、生成素材及已经渲染的成片三个文件接口均从 200 变为 403 / MEDIA_REVOKED。[派生后撤权实测](../evidence/ai-production/source-checks/reference-derived-revocation.json)。该组协议测试使用合成的上游返回。独立的真实工作器脚本会重新生成静帧、无参考视频和带参考视频，保留原片、逐帧摘要和回执。每轮的 `diffusion-model/report.json` 记录实际结果。[真实工作器验证器](../scripts/verify_generation_worker.py)。片中物体与参考图的身份、颜色、比例及运动是否符合目标，要查看相应模型实片。[生成专项 10 项实测](../evidence/ai-production/source-checks/reference-generation-tests.log) · [与制作、预算联测 29 项](../evidence/ai-production/source-checks/production-reference-suite.log)

测试团队登录与服务端模型凭据使用不同的随机值。独立传输检验为两类未授权请求得到 401，经指定模型代理的调用成功。读取上传到模型的 JSON、业务响应、实际日志、所有项目记录以及 SQLite 文件后，均未发现两个随机值的原文。发布证据与分发配置另外进行常见供应商凭据格式、私钥、会话和令牌字面值扫描。报告内的时间、范围与文件数固定，不能据此声称所有后续第三方日志或所有未知凭据格式均安全。[独立核验原始记录](../evidence/ai-production/independent-release-audit.json)

### 实际参考图视频的逐帧结果

在本地 SD1.5 / AnimateDiff-Lightning 工作器中，使用同一提示词、种子和一张 512 × 512 的生成杯图，分别取得纯文字视频和带参考图的视频。纯文字样本的八帧呈彩色网格，未见杯形。带参考的八帧均可辨认蓝杯，构图接近原图。末段杯口、手柄与背景形状出现干扰，当前图像证据不能确认稳定的蒸汽或可控推进，仍需内容审阅。两个样本的八帧分别有八个不同解码摘要。

逐帧将原图及视频统一解码为 64 × 64 RGB，按 0–255 的颜色通道计算平均绝对差。带参考的第一帧为 15.876，末帧为 25.522。纯文字样本分别为 83.400 和 81.989。此测量量化了颜色和构图的接近程度，判断杯形、运动及噪声仍需查看实帧。该次探索用到了真实参考原件与加载节点，但启动早于 worker 输入读回收据字段定稿。在冻结构建第一轮，工作器实际读回 531,381 字节，完整 SHA-256 与服务端规范化 PNG 相同。另分别读回交付的参考原件与生成视频，摘要均与任务回执一致。[冻结第一轮真实模型与读回证据](../evidence/ai-production/round-1/diffusion-model/report.json)。其余轮次可逐项核对各自的 `conditioned_job.reference_upload`。[全部帧对照](../evidence/ai-production/conditioned-pixel-audit/frame-comparison.jpg) · [逐帧数值及两个视频的 SHA-256](../evidence/ai-production/conditioned-pixel-audit/pixel-qa.json)

### 保留模型选片的成片

在同一份 FRESH LIME 简报上，Qwen2.5 1.5B 从两个真实候选中选择了先播目标标题卡的版本。获准的测试客户端明确采用这份未经改剪的提案，服务按锁版时间线渲染。对下载 MP4 首帧、第 0.8 秒和第 2.4 秒的源画面分别做 OCR，读到 FRESH LIME、FRESH LIME 和 SAMPLE。完整画面与原片逐像素比较亦吻合。Qwen2.5 的自由入出点提案超出原片范围，被拒绝。另一套真实权重 Qwen3 4B 选择了相反顺序，尽管其理由声称已让目标卡在前。三次原始结果、整帧 OCR 的黑边误识别以及正确与错误的原版成片均可检查。[两套导演模型的实际选择与成片](../evidence/ai-production/director-model-comparison/README.md)
