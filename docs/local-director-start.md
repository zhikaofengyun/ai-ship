# 用已有 GGUF 开启本机导演

安装项目依赖、FFmpeg 和支持结构化输出的 llama.cpp 后，可以从同一个入口启动工作台与本机模型。将两个路径换成当前机器上的文件。

```bash
python scripts/run.py --director-model /models/director.gguf --llama-server /opt/llama/llama-server
```

打开终端显示的工作台地址，选择或创建项目，进入制作台的模型配置。在服务器已登记的模型中选取 `local-director / atlas-local-director`，保存到当前项目。此操作只写入模型绑定；按下生成方案才向模型提交所选素材的证据。客户端无需填写服务地址、密钥或内部端口。在本机模式下使用回环地址，可直接返回当前任务继续编排。

启动器从指定文件加载实际权重，等待认证的模型清单返回正确模型，确认匿名访问被拒绝，然后启动工作台。每次运行使用独立的本机密钥和端口，退出时关闭子进程。模型配置在项目中用稳定引用保存，多次启动后仍然可用。基础模型与硬件由运行方提供。默认价表为每百万输入、输出 token 零美元，本机消耗的电力与运行时间另计。若配置了商业供应商，沿用管理员价格和项目费用上限。

安装包不包含权重。实测用 llama.cpp b12671、Qwen2.5 0.5B Instruct GGUF；小型语言模型在真实任务中出现过选错镜头顺序的情况。选择其他 GGUF 时需确认模型支持所用模板和结构化输出。已有远程模型、三角色配置和 ComfyUI 的接入方式见[模型与生成运行合同](ai-production-runtime.md)。[llama.cpp 官方服务](https://github.com/ggml-org/llama.cpp/tree/master/tools/server) · [模型发布页](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF)

## 在同一制作台提供图片与动态镜头

在本机启动已装对应权重及节点的 ComfyUI，默认回环端口是 8188。工作台启动前可以登记仓库中三张可执行图。在项目内，文生图片、文生短片和参考图生成短片会出现在同一组生成操作中。选择参考模式时，先选用项目内的图片修订，或在面板中导入自己的图。

```bash
export AI_SHIP_COMFY_PROFILES="$(cat contracts/examples/comfy-animatediff.json)"
python scripts/run.py --director-model /models/director.gguf --llama-server /opt/llama/llama-server
```

两套服务需要能在同一网络命名空间通过回环互访。容器中的 127.0.0.1 对应该容器。模型权重位置、实际资源使用和失败样本见[本机实推记录](../evidence/ai-production/live-generation/README.md)。[工作台正式启动、绑定和无推理副作用的实测](../evidence/ai-production/normal-startup/report.json)

## 与成功样本使用相同的权重

在已安装 Ollama 的机器可以用 `ollama pull qwen2.5:1.5b` 获取登记的模型。要把完全相同的 GGUF 直接交给 llama-server，可以从 Ollama 的内容寻址仓库下载。先核验权重，再调用上面的启动命令。工作台不会把模型权重上传到外部服务。

```bash
curl --fail --location --output qwen2.5-1.5b.gguf https://registry.ollama.ai/v2/library/qwen2.5/blobs/sha256:183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4
python -c "import hashlib; print(hashlib.file_digest(open('qwen2.5-1.5b.gguf','rb'), 'sha256').hexdigest())"
python scripts/run.py --director-model ./qwen2.5-1.5b.gguf --llama-server /opt/llama/llama-server
```

预期 SHA-256 即 URL 尾部的 64 位摘要，文件应有 986,048,512 字节。在已知输入顺序的对照试验中，该模型在受约束候选中选对两段素材的排列；自由填写的裁剪时点超出了原片范围。更大的 4B 模型在同一任务选反了镜头。[模型比较及成片](../evidence/ai-production/director-model-comparison/README.md) · [Ollama 模型页](https://ollama.com/library/qwen2.5:1.5b) · [本次 llama.cpp 发布版](https://github.com/ggml-org/llama.cpp/releases/tag/b12671)
