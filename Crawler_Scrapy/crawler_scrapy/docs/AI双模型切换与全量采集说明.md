# AI 双模型切换与全量采集说明

## 1. 适用范围

目前 Qianji、SXJM、Bitbid、Trade365、SXZWFW 共用同一套 C 方案混合提取 Pipeline。站点规则和可信 API 先解析，只有缺失且有明确标签、HTML 残留、长章节越界或名单/报价错位的字段才调用模型。模型候选必须通过窗口、原文行号、字段语义、格式和冲突裁决后才能写入。

模型提供方彼此独立：

| 提供方 | 模型 | 密钥环境变量 | 输出约束 |
| --- | --- | --- | --- |
| 智谱 | `glm-5.2` | `ZHIPUAI_API_KEY` | JSON Object，保持原有行为 |
| 硅基流动 | `Qwen/Qwen3-8B` | `SILICONFLOW_API_KEY` | 字段级 JSON Schema、非思考模式 |

切换 Qwen 不会修改、覆盖或删除 GLM 配置。

## 2. 配置密钥

在项目 `.env` 中分别配置，需要哪个模型就填写哪个密钥；不要提交 `.env`：

```dotenv
ZHIPUAI_API_KEY=你的智谱密钥
SILICONFLOW_API_KEY=你的硅基流动密钥
```

运行脚本只把密钥环境变量名称传给 Scrapy，真实密钥不会出现在进程命令、日志或 JSON 中。

## 3. 使用 Qwen3-8B 全量采集

以 SXJM 为例，同时采集公告并下载附件：

```bash
./run_sxjm.sh --all --phase all \
  --ai-extract \
  --ai-provider siliconflow \
  --ai-model Qwen/Qwen3-8B \
  --ai-max-calls 0
```

其余四站只需要替换脚本名称：

```bash
./run_qianji.sh --all --phase all --ai-extract --ai-provider siliconflow --ai-model Qwen/Qwen3-8B --ai-max-calls 0
./run_bitbid.sh --all --phase all --ai-extract --ai-provider siliconflow --ai-model Qwen/Qwen3-8B --ai-max-calls 0
./run_trade365.sh --all --phase all --ai-extract --ai-provider siliconflow --ai-model Qwen/Qwen3-8B --ai-max-calls 0
./run_sxzwfw.sh --all --phase all --ai-extract --ai-provider siliconflow --ai-model Qwen/Qwen3-8B --ai-max-calls 0
```

`--ai-provider` 可以省略：当 `--ai-model` 为 `Qwen/Qwen3-8B` 时，`auto` 会自动选择硅基流动。`--ai-max-calls 0` 表示本次进程不设置模型调用总数上限；模型仍只处理异常字段，不会对每条公告无差别调用。

免费模型可能出现 429、503 或短时过载。Qwen 配置只重试一次，并继续遵守全局调用间隔；重试仍失败时保留规则结果，除非以后显式启用 `NOTICE_AI_FAIL_ON_ERROR`。

## 4. 继续使用原 GLM-5.2

原命令保持有效：

```bash
./run_sxjm.sh --all --phase all \
  --ai-extract \
  --ai-model glm-5.2 \
  --ai-max-calls 0
```

也可以显式指定：

```bash
./run_sxjm.sh --all --phase all \
  --ai-extract \
  --ai-provider zhipu \
  --ai-model glm-5.2 \
  --ai-max-calls 0
```

GLM 继续使用原来的智谱地址、`ZHIPUAI_API_KEY`、关闭思考和 JSON Object，不受 Qwen 配置影响。

## 5. Qwen3-8B 专项适配

- 关闭思考模式，避免字段抽取产生无用 reasoning token。
- 只发送字段标签附近的局部窗口；长字段最多发送对应章节，不发送整篇 HTML。
- 使用字段级 JSON Schema，禁止模型增加未请求字段。
- 无证据字段必须返回 `null`；长文本只返回原文行范围，由程序切片，模型不能摘要或重写。
- 使用零随机性、`top_p=0.8`、`top_k=20`，提高抽取结果的可复现性。
- 单次输出最多 2,200 token，单次请求超时 120 秒；免费端点超时时不立即
  重放相同请求，其他可恢复错误仍保留一次有限重试。
- 将字符串 `"null"` 等伪空值规范为真正空值；递交方法必须保留正文明确的
  平台、线上/线下、现场或邮寄渠道。
- 规则/AI 冲突必须经过扩大章节的第二阶段；两个阶段一致且证据通过后才允许覆盖。
- 中标人/候选人名称与报价继续执行同索引校验。

## 6. 断点、去重和历史数据

- AI 只处理本次实际输出的新公告；已有 JSON 不会因为更换模型而自动重算。
- 相同采集范围已经完成时，运行器会直接提示完成；要进行 GLM/Qwen A/B 测试，应使用新的 `--output-root`，避免历史去重索引跳过公告。
- 公告和附件仍按原两阶段逻辑执行；模型失败不会破坏快照、payload、附件对应关系或断点续爬。
- 每条发生模型处理的记录会保存模型名、候选窗口、证据 SHA256、调用次数、token、应用字段和拒绝原因，便于后续比较 GLM 与 Qwen。

## 7. 推荐验证命令

在全量采集前，建议先用独立输出目录做少量真实样本验证：

```bash
./run_sxjm.sh --phase notices --max-records 20 \
  --output-root new_output/qwen_validation \
  --ai-extract --ai-provider siliconflow \
  --ai-model Qwen/Qwen3-8B --ai-max-calls 50
```

确认日志中没有鉴权、JSON Schema、429/503 持续失败，并对照快照检查 AI 实际应用字段后，再启动全量任务。

千极数同一批公告的 GLM/Qwen 实测数据见
[`qianji/GLM-5.2与Qwen3-8B同公告实测对比报告_20260818.md`](qianji/GLM-5.2与Qwen3-8B同公告实测对比报告_20260818.md)。
