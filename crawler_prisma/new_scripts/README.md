# 新爬虫数据导入说明

本目录负责把 `Crawler_Scrapy/new_output` 中的新框架 JSON 导入当前数据库，支持：

- `sxjm`：山西焦煤电子招采平台
- `sxzwfw`：山西省公共资源交易平台
- `bitbid`：比比网
- `huaxin`：华新阳光采购平台
- `jiubang`：玖邦招标采购电子交易平台
- `sxjkzcpt`：山西交控招投标采购服务平台
- `trade365`：中招联合（山西）招标采购网
- `sxxindian`：山西新点招投标交易平台
- `sxbid`：山西省招标投标公共服务平台
- `qianji`：千极数采电子交易平台

导入器不修改数据库结构、不执行迁移，目标结构以
`crawler_prisma/prisma/schema.prisma` 为准。运行时使用本目录的 `.env`、
`@prisma/client`、`mongodb` 和 `minio` 依赖，不修改也不依赖 recommendation 源码。

## 存储对应关系

| 数据 | 保存位置 |
|---|---|
| 公告来源、标题、时间、指纹、版本索引 | MySQL `raw_notice` |
| 源站 payload、正文、原始 HTML、请求溯源 | MongoDB `raw_notices` |
| 附件文件 | MinIO `notice-attachments` |
| 附件名称、URL、哈希、对象键和状态 | MySQL `raw_notice_attachment` |
| 抽取模型、版本、核验状态 | MySQL `notice_extraction` |
| 完整抽取字段、字段证据 | MongoDB `notice_extractions` |
| 项目主数据 | MySQL `project` |
| 项目公告及结构化字段 | MySQL `project_notice` |
| 项目公告附件引用 | MySQL `project_notice_attachment` |
| 公告资格要求原子条件 | MySQL `project_requirement` |

## 历史资格要求提取

脚本读取 `project_notice.structured_data["申请人资格要求/投标人资格要求"]`，默认只生成报告：

```bash
npm run import:project-requirements -- --limit=20
```

审核报告后再定向写入：

```bash
npm run import:project-requirements -- \
  --commit \
  --notice-ids=29516,29517,29518,29520 \
  --batch-id=qualification-reviewed-001
```

不要在未复核 `OTHER` 和低置信度结果前使用 `--allow-all` 全量写入。

启用 Qwen 复核时只需在 `.env` 配置 `QUALIFICATION_AI_API_KEY`。默认服务地址为
`https://api.siliconflow.cn/v1`，默认模型为 `Qwen/Qwen3-8B`；仅在切换服务商或模型时
才需要配置 `QUALIFICATION_AI_BASE_URL` 或 `QUALIFICATION_AI_MODEL`。

Qwen 默认关闭思考模式并使用 60 秒超时，必要时可用 `QUALIFICATION_AI_TIMEOUT_MS`
覆盖。模型结果只有在原文证据、V1 字段、枚举和最低 0.6 置信度全部通过时才会
替换 `OTHER`，否则继续保留原规则结果。

HTML 快照不会重复写入结果 JSON 或 MySQL。JSON 业务字段保持各公告类型的完整固定
Schema（空值也保留），顶层保存正文、HTML快照路径和
SHA256；`_trace.payloadSnapshot` 保存源站 payload 快照引用。导入器校验快照哈希后，
把 payload、HTML和正文写入 MongoDB，能够从业务公告反查原始公告。旧版 `_trace`
1.0 内嵌结构仍可导入。

## 项目与公告关联

关联严格按以下优先级执行：

1. `项目编号`；
2. 没有项目编号时使用 `招标编号`；
3. 两者都没有时使用规范化后的项目名称。

如果一条缺少项目编号的公告，其招标编号在其他公告中只对应一个项目编号，公告会合并到该项目。
同一个招标编号对应多个不同项目编号时不会强行合并，防止标段间串项。

数据库当前没有独立的招标编号列。仅以招标编号创建的项目使用
`TENDER:<site>:<招标编号>` 作为可重复执行的内部 `project_code`；后续同组公告出现真实项目编号时，
导入器会通过该别名找到原项目并更新为真实项目编号。

JSON 顶层的 `TENDER/CANDIDATE/AWARD` 是传输编码。写入
`notice_extraction.notice_type` 和 `project_notice.notice_type` 时转换为当前数据库使用的
`招标公告/中标候选人公示/中标结果公示`。采购、成交等源站原始类型继续保存在
`公告子类型` 和 MongoDB 溯源数据中。

## 默认路径

```text
爬虫输出：/home/intsig/Crawler_Scrapy/new_output
环境文件：/home/intsig/crawler_prisma/.env
```

可用 `--output-root`、`--env-file` 或 `CRAWLER_PRISMA_ENV` 调整。为防止误连旧库，
本地 `.env` 中已核验的 MySQL 配置优先；`--env-file` 用于补充缺失的 MongoDB/MinIO
变量，进程显式环境变量优先级最高。配置内容不会复制到代码或提交到 Git。

## 准备 Prisma Client

该操作只生成客户端，不迁移数据库：

```bash
cd /home/intsig/crawler_prisma
npm install
npx prisma generate
```

## Dry-run

建议等对应爬虫公告和附件阶段全部结束后执行。先做不连接数据库的公告/项目校验：

```bash
cd /home/intsig/crawler_prisma

npm run import:raw-notices -- --site=sxjm
npm run import:notice-extractions -- --site=sxjm
npm run import:projects -- --site=sxjm
npm run import:project-notices -- --site=sxjm
```

附件已经下载完成时再校验：

```bash
npm run import:raw-notice-attachments -- --site=sxjm
```

JSON 使用流式解析，数百 MB 或更大的数组文件不会被一次性读入内存。

## 正式导入

必须按以下顺序执行：

```bash
cd /home/intsig/crawler_prisma

npm run import:raw-notices -- --site=sxjm --commit
npm run import:raw-notice-attachments -- --site=sxjm --commit
npm run import:notice-extractions -- --site=sxjm --commit
npm run import:projects -- --site=sxjm --commit
npm run import:project-notices -- --site=sxjm --commit
npm run import:project-notice-attachments -- --site=sxjm --commit
```

也可以在附件已经完整下载后执行总入口：

```bash
npm run import:all -- --site=sxjm --commit
```

`--site=all` 会导入 `new_output` 下存在的全部受支持站点。

## 缺失附件

默认情况下，JSON 声明了附件但本地文件不存在会直接报错，防止把未下载文件误标为已存入 MinIO。
确认只需要保留来源元数据时才使用：

```bash
npm run import:raw-notice-attachments -- \
  --site=sxjm --commit --allow-missing-files
```

此时记录使用 `storage_provider=SOURCE`，不会伪装成 MinIO 文件。

## 重复执行

- 原始公告按 `data_source_id + source_notice_id` 幂等更新；
- 内容指纹变化时增加 MongoDB 内容版本；
- 抽取结果按公告、模型和版本幂等更新；
- 项目优先按真实项目编号更新，其次识别招标编号别名；
- 项目公告按 `source_site + source_notice_id` 幂等更新；
- 附件按哈希、来源文件 ID、URL或文件名匹配，已有相同 MinIO 对象不会重复上传。

如果爬虫仍在运行，可以先反复导入原始公告；但完整 `import:all` 应等附件阶段完成后执行。
