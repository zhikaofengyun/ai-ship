# 制作台验证记录

五轮均使用同一版应用、测试、接口及依赖定义。每轮各执行 26 组检查与 171 个独立测试用例，并重新运行两种浏览器、语言模型和扩散模型。每个轮次报告均列出执行命令、通过状态、起止时间、已覆盖范围、未验项目及原始产物摘要。

[完整源码还原与启动](../RELEASE-SOURCE.md) · [原文 178 项功能核对](../docs/feature-workflows.md) · [接口合同](../contracts/openapi.json)

| 轮次 | 本地完整检查 | 个案 | PNG / 视频 | 文件与摘要 |
| --- | ---: | ---: | ---: | --- |
| 1 | 26 | 171 | 81 / 16 | [逐组报告](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/evidence/ai-production/round-1/round-report.json) · [个案与画面读数](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/verification/round-1-results.json)，169 份原始产物 |
| 2 | 26 | 171 | 81 / 16 | [逐组报告](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/evidence/ai-production/round-2/round-report.json) · [个案与画面读数](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/verification/round-2-results.json)，169 份原始产物 |
| 3 | 26 | 171 | 81 / 16 | [逐组报告](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/evidence/ai-production/round-3/round-report.json) · [个案与画面读数](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/verification/round-3-results.json)，169 份原始产物 |
| 4 | 26 | 171 | 81 / 16 | [逐组报告](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/evidence/ai-production/round-4/round-report.json) · [个案与画面读数](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/verification/round-4-results.json)，169 份原始产物 |
| 5 | 26 | 171 | 81 / 16 | [逐组报告](https://github.com/zhikaofengyun/ai-ship/blob/cc8b6f2aeee9cd47ec57fe122c34c3077d806ab7/evidence/ai-production/round-5/round-report.json) · [个案与画面读数](round-5-results.json)，169 份原始产物 |

## 核对实际结果

- 在制作台拖动导航和方案栏、播放或缩放原片，空项目导入两段素材。采用前比较 AI 方案与实际首帧，保存局部修正。编码后，从下载件重新读取首帧。
- 三角色模型各自调用真实权重，保存选择、引用和裁决。没有通过证据核对的方案会停在审阅。
- 各轮对实际生成的图片、文生视频及参考图驱动的视频解码。镜头文字、编码和可播放性有机器证据，叙事、观感和商业用途需另行验收。
- 完整工程包包含原始 PNG、MP4、WebM、逐例状态和日志，位于相同的 `evidence/ai-production/round-1` 至 `round-5` 目录。

## 从 GitHub 取出实际录屏和成片

公开仓库的 [样片包](review-films.patch) 包含 33.43 秒连续实操录屏、该次实际导出的 6.4 秒成片、首镜自主命中与保留双素材的两个样片、五轮扩散各自导出的 MP4，以及对应的六列画面对照图。下面的命令在新的临时目录还原 **9 份视频和 1 张对照图**，核对整棵媒体目录的文件名、权限和内容后打印位置。所有视频都曾从这一公开文件重新解码。[独立公开回读记录](../evidence/ai-production/source-checks/public-review-films.json)

```sh
python verification/extract_review_media.py
```

完整验收包另含五轮全部 405 张截图、80 份视频、逐例状态和测量原件。原生 iPhone／macOS Safari、付费云端生成、外部平台 OAuth、自动发布、真实平台回执以及分布式生产负载尚待实测。原文业务条件的证据等级另有逐条记录。
