# 从源音识别并审核字幕

在「脚本与 Voice Map → 转录与词句时码」或「标注 → 文字对白与声音」选择素材、已安装的模型和源文件区间。一次识别最长十分钟。引擎读取文件的第一条音轨，输出基于音源播放开始位置的秒数。每个候选保留词级起止、模型给出的置信度、音源版本、原件与解码 PCM WAV 的 SHA-256、模型目录指纹及 Vosk 版本。无音轨会提示无法识别。数字静音保留空候选记录。

在候选中试听、修改文字、取消不采用的句子，填写审核依据。项目审阅人或所有者保存后可以下载 SRT，并从「镜头编排 → 导入字幕文件」载入。SRT 记录逐句起止，默认文件名包含审核记录 ID。时间轴导入保留该文件名，原始词级证据仍保存在候选。素材在成片中的起点或速度改变时，导入前需要换算到成片时间。中英文示例用词、标点仍需人审，证据中的英文样本把 “ask not” 识别成了 “as not”。未进行口音、说话人分离、混合语种或长片噪声条件的准确率研究。

## 显式启用

安装可选依赖，手动准备下表中的官方模型目录。默认启动不会联网下载引擎或权重。只解压模型实际内容到本机受控目录。哈希针对解压后的相对路径及每个普通文件内容。用 `python -m app.speech_worker /absolute/model/directory` 计算，再和表中的指纹核对。

| 模型 | 发布包 | 解压树 SHA-256 |
|---|---|---|
| 英文小模型 0.15 | [vosk-model-small-en-us-0.15](https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip) | `b42e54229873c6a3a59875cefb7fafa2d8a7d5aeabbaa82b7572fa897e0fb19b` |
| 中文小模型 0.22 | [vosk-model-small-cn-0.22](https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip) | `a313b87cc3c904138a1830886dcc6b6cdcbbd21c7f95edb72b8d6b751fad03cc` |

```sh
python -m pip install -r requirements-asr.txt
export AI_SHIP_ASR_MODELS='{"en":{"path":"/models/vosk-model-small-en-us-0.15","language":"en-US","sha256":"b42e54229873c6a3a59875cefb7fafa2d8a7d5aeabbaa82b7572fa897e0fb19b"},"zh":{"path":"/models/vosk-model-small-cn-0.22","language":"zh-CN","sha256":"a313b87cc3c904138a1830886dcc6b6cdcbbd21c7f95edb72b8d6b751fad03cc"}}'
```

配置好后重启 FastAPI。模型路径、版本或内容不匹配时识别失败。候选、审核、导出时都会重新检查源版本、当前用途及原件校验状态。端点定义见 OpenAPI 的 speech recognition 分组。

## 复现

准备本地 FFmpeg、ffprobe、项目依赖、可选 ASR 包、Playwright 及 Chromium。取以下两段真实录音，独立运行验收脚本。测试自建临时项目，在机器上生成静音、无音轨和晚 1.5 秒起音的对照文件。

| 录音 | 内容 SHA-256 |
|---|---|
| [whisper.cpp 固定版本英文录音](https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.7.6/samples/jfk.wav) | `59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e` |
| [WeNet AISHELL 中文录音](https://raw.githubusercontent.com/wenet-e2e/wenet/main/test/resources/aishell-BAC009S0724W0121.wav) | `2f9fc9c912bb71c85fb286cb88b599c81efb8f727c727a5ea8f6d1c89c55ac13` |

```sh
python scripts/verify_local_speech.py \
  --english-model /models/vosk-model-small-en-us-0.15 \
  --chinese-model /models/vosk-model-small-cn-0.22 \
  --english-audio /samples/jfk.wav \
  --chinese-audio /samples/aishell-BAC009S0724W0121.wav \
  --chromium /usr/bin/chromium \
  --output /tmp/local-speech-verification
```

[逐词原始结果及审核记录](../evidence/local-speech/report.json) · [浏览器审核画面](../evidence/local-speech/human-review.png) · [剪辑器导入后保存并重新打开的字幕](../evidence/local-speech/timeline-import.png) · [Vosk 官方模型及语言说明](https://alphacephei.com/vosk/models) · [Vosk 项目](https://github.com/alphacep/vosk-api)
