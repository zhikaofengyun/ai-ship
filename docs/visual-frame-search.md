# 本地视频画面检索

在「索引与检索 → 检索素材」展开「索引版本」，选择「提取画面语义索引」。界面每批处理最多四份视频或图片。需要下一批时继续点击。检索方式选择「画面语义（CLIP）」，输入画面描述，例如 `a bee visiting a flower`。本次画面实测使用三条英文查询。

选择「预览」会打开匹配帧的实际源时码。可在「登记场景或动作标注」中填写自己观察到的标签、核对依据和起止时间。只有明确提交后才会写入人工标注与源帧凭证。进入剪辑台时保留同一素材版本与时码。

## 自动入库分析

上传文件、采集目录、连接器和生成任务产生的图片或视频会与其分析队列项在同一事务落库。应用启动后，一个本地后台线程按当前权限读取实际字节，保存旋转后的画幅、采样颜色、源时码，以及可用的 Tesseract OCR 文字和指纹。配置下方 CLIP 权重后，同一任务会写入图片首帧或视频最多三帧的向量，并按有限的场景、主体词表给出带来源和分数的候选。相似度小于 0.23 或与次选相差不足 0.02 时保留空白。阈值为当前实现参数，准确率需要在业务素材上另行标定。

工作台「检索素材 → 索引版本 → 批量分析素材」可扫描历史素材和重做缺失部分，停止后已完成的数据可复用。API 为 `POST /api/v1/production/asset-index/analyze`，输入 `project_id`、`limit` 和上页的 `next_after` 作为 `after`。设置 `refresh: true` 可在修复 OCR 语言包后重新提取实帧与文字。每个上传素材的后台状态在索引历史响应的 `analysis_queue` 中汇总。需要单独控制资源的服务可设置 `AI_SHIP_MATERIAL_WORKER=0` 暂停后台领取，新素材仍登记队列，人工批量分析仍可用。

首次进入自动剪辑也会按需读取实帧。导演从同一媒体落点复用采样与 OCR，重新核验当前版本、加工权限和人工标签。命中人工锁定的标签区间时，导演优先采用对应区间。操作员在检索结果中可定位到词条的来源时码。模型候选保留原模型空间与相似度。该分类词表不识别品牌 SKU、数量、人物身份或转化率。

## 安装

应用只读取服务器目录中的本地模型文件。使用 Python 3.12、可运行的 FFmpeg 及仓库的可选语义检索依赖。

```sh
python -m pip install -r requirements-semantic.txt
export AI_SHIP_CLIP_MODEL_DIR=/absolute/path/to/clip-b32
mkdir -p "$AI_SHIP_CLIP_MODEL_DIR"
clip_revision=6b7c523703ae26e39be4b03774966f8571c7f523
clip_root="https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/$clip_revision"
for clip_file in vision_model_quantized.onnx text_model_quantized.onnx; do
  curl -fL "$clip_root/onnx/$clip_file" -o "$AI_SHIP_CLIP_MODEL_DIR/$clip_file"
done
curl -fL "$clip_root/tokenizer.json" -o "$AI_SHIP_CLIP_MODEL_DIR/tokenizer.json"
sha256sum "$AI_SHIP_CLIP_MODEL_DIR"/*
```

| 文件 | 验证的 SHA-256 |
| --- | --- |
| `vision_model_quantized.onnx` | `583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299` |
| `text_model_quantized.onnx` | `73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a` |
| `tokenizer.json` | `f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd` |

将目录及环境变量提供给运行 API 的进程。容器部署时把模型目录只读挂载到容器，并在服务镜像内安装相同可选依赖。服务启动和普通检索不下载模型。原始模型和 ONNX 导出分别见 [OpenAI CLIP](https://github.com/openai/CLIP) 与 [Xenova 固定版本](https://huggingface.co/Xenova/clip-vit-base-patch32/tree/6b7c523703ae26e39be4b03774966f8571c7f523)。

## 采样与权限

每份视频尝试在开头、二分之一及六分之五处精确解码，保存实际取得的帧 PTS（微秒）、用于编码的 224×224 RGB 图像 SHA-256 及单位化的 512 维向量。解码器会去除重复时码。模型文件、分词器和预处理约定共同决定向量空间。更换任何权重后需要重新建立对应空间的索引。

`POST /api/v1/production/asset-index/frames/build` 支持 `project_id`、`limit`（1–12）和 `after`，响应包含成功视频与帧数、错误及 `next_after`。每个进程同时只接受一个构建请求。已建索引且版本相同的素材可跳过，单份解码失败会返回错误，后续页面仍可继续构建。`POST /api/v1/production/asset-index/query` 设置 `channel: "visual"`，可通过 `visual_threshold` 调整最低余弦相似度，默认 0.20。画面命中会返回 `frame_match.source_seconds`、`sample_rgb_sha256` 和 `space`。同一素材返回采样帧中的最高分。界面可以调整最低画面相似度。超过画面阈值的命中会提供源时码，只有名称或标签相符的结果也可能保留。余弦分数表示图文接近程度。稀疏取样可能漏掉瞬时动作或没有进入中央裁剪区域的物体。

构建和查询都核对当前项目、素材版本与实际加工授权。失去权限、隔离或版本变更的素材不能凭旧向量命中。没有模型、未建索引或模型运行失败时，仍能按名称和已有标注查找。标注提交时再次核对源版本、帧时码、RGB 指纹、模型空间及所填区间。

## 已核对的结果

| 英文查询 | 排名首位的视频 | 最佳采样帧源时码 | 余弦相似度 |
| --- | --- | ---: | ---: |
| `an astronaut wearing a space suit` | `001.mp4`，宇航员照片所在段 | 0.000 秒 | 0.267106 |
| `a cup of coffee` | `001.mp4`，咖啡杯照片所在段 | 3.400 秒 | 0.286806 |
| `a bee visiting a flower` | `002.mp4`，蜜蜂访花摄影视频 | 2.535867 秒 | 0.229304 |

两份视频共采六帧。三个首位结果均未命中名称或标签。Chromium 158 和 WebKit 26.5 的预览与剪辑台都停在咖啡杯画面的 3.400 秒，人工标注保存的时码均为 3,400,000 微秒。验证还覆盖跨项目隔离、加工授权撤销、伪造帧指纹被拒以及模型更换后的旧向量隔离。[完整实测记录](../evidence/batch-production/visual-search/result.json) · [检索界面](../evidence/batch-production/visual-search/frame-search.png)

该对照的两个样本分别覆盖已知画面切换和真实摄影。对短暂动作、多对象遮挡、音频内容、中文画面查询和生产规模的检索精度，尚无对应基准数据。

## 可复现验证

```sh
python -m pip install -r requirements-dev.txt -r requirements-semantic.txt
python scripts/verify_visual_search.py --fixture-dir /tmp/clip-fixtures --output /tmp/clip-result
python scripts/verify_material_intake.py --fixture-dir /tmp/clip-fixtures --output /tmp/intake-result
```

可额外传入 `--browser chromium` 或 `--browser webkit` 验证真实页面、人工标注与剪辑台定位。脚本使用 MDN 的 CC0 摄影视频、scikit-image 的两张样例照片以及 FFmpeg 编码的双场景对照片段。首次下载会核对固定 SHA-256，运行结果记录来源、实际时码、RGB 指纹、余弦分数和授权验证。模型权重和参考媒体不随仓库分发。

自动入库用例核对后台队列、批量回填复用数、候选标签与源时码，以及浏览器图片预览。验证使用上方固定权重与三份固定媒体。[自动入库实测结果](../evidence/material-intake/result.json)
