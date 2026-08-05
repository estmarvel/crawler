# 重构数据库 JSON 导入脚本

这些脚本以 `crawler_prisma/scripts` 中的旧脚本和当前爬虫 JSON 为基础，但按新存储边界写入：

| JSON 数据 | 新存储位置 |
|---|---|
| 公告标题、来源、状态、时间、指纹 | MySQL `raw_notice` |
| 完整原始 JSON、公告正文 | MongoDB `raw_notices` |
| 附件文件本体 | MinIO `notice-attachments` |
| 附件名称、哈希、桶名、对象键 | MySQL `raw_notice_attachment` |
| 抽取模型、版本、核验状态 | MySQL `notice_extraction` |
| 完整抽取字段 | MongoDB `notice_extractions` |

## 新爬虫溯源字段

新版 `Crawler_Scrapy` 不改变原有公告主字段和 CSV 表头，只在每条 JSON 记录顶层增加
`_trace`。导入器会把它拆分到现有的新数据库字段中：

| `_trace` 字段 | 保存位置 |
|---|---|
| `payload` | MongoDB `raw_notices.payload` |
| `rawHtml` / `rawText` | MongoDB `raw_notices.rawHtml/rawText` |
| `responseMetadata` | MongoDB `raw_notices.responseMetadata` |
| `crawlerVersion` | MongoDB `raw_notices.crawlerVersion` |
| `fieldMeta` | MongoDB `raw_notices.responseMetadata.trace.fieldMeta`，并作为一条诊断证据写入 `notice_extractions.evidence` |
| `exportMetadata.noticeSubtype/missingFields` | MongoDB `raw_notices.responseMetadata.trace.exportMetadata`，同时写入 `notice_extractions.evidence` 的诊断条目 |
| `integrity` | MongoDB `raw_notices.responseMetadata.trace.integrity` |

以上全部复用现有 MongoDB 文档字段；没有新增集合字段、MySQL 列、索引或迁移。

`responseMetadata` 只包含请求方法、URL、响应状态、编码、少量公开响应头、延迟、重试和
重定向信息，不保存 Cookie、Authorization、代理密码或 API 密钥。没有 `_trace` 的历史
JSON 仍按原有回退规则导入，因此无需重写以前的采集文件。

脚本不会再向已经删除的 MySQL 字段 `raw_html`、`raw_text`、`storage_path`、`extracted_fields` 写数据。

## 运行前提

1. `ProjectRecommendationSystem/api/.env` 已正确配置 MySQL、MongoDB、MinIO。
2. API 项目已经执行 `npm --prefix api ci` 和 `npm --prefix api run prisma:generate`。
3. MySQL 已迁移完成，MongoDB 三个集合已初始化，MinIO 桶可用。
4. `data_source` 已导入。华新优先使用 ID 6，玖邦优先使用 ID 14；其他站点按 `short_code` 查找。

脚本默认从以下路径读取环境变量和依赖：

```text
/home/intsig/ProjectRecommendationSystem/api
```

如目录不同，使用 `--api-root=<path>`。

## 先执行 dry-run

```bash
cd /home/intsig/crawler_prisma/new_scripts

npm run import:all
```

dry-run 只解析并验证 JSON 和附件文件，不连接或修改数据库。

只检查一个站点：

```bash
npm run import:all -- --site=huaxin
npm run import:all -- --site=sxzwfw
```

## 正式导入

建议先停止 API，避免导入期间采集任务同时写入：

```bash
sudo systemctl stop project-recommendation-api
```

然后按顺序执行：

```bash
npm run import:raw-notices -- --commit
npm run import:raw-notice-attachments -- --commit
npm run import:notice-extractions -- --commit
```

也可以使用总入口：

```bash
npm run import:all -- --commit
```

完成后再启动 API 并检查健康状态：

```bash
sudo systemctl start project-recommendation-api
curl http://127.0.0.1:3001/api/health
```

## 缺失附件

默认情况下，只要 JSON 声明了附件但本地文件不存在，附件阶段就会终止，防止产生“看似已迁移但没有文件”的记录。

确认某些文件本来就下载失败、只希望保留来源元数据时，可显式使用：

```bash
npm run import:raw-notice-attachments -- --commit --allow-missing-files
```

这类记录使用 `storage_provider=SOURCE`，且 `bucket_name/object_key` 为空，不会伪装成已存入 MinIO。

## 重复执行规则

- `raw_notice` 按 `data_source_id + source_notice_id` 定位。
- 指纹变化时，MongoDB 新增内容版本，MySQL 指向最新文档。
- 附件优先按哈希，其次按来源 URL、文件名定位；已存在且大小相同的 MinIO 对象不会重复上传。
- 抽取记录按 `raw_notice_id + extraction_model + extraction_version` 定位。
- MongoDB 写入成功、MySQL 写入失败时，新脚本会尽可能撤销刚写入的 MongoDB 文档或 MinIO 新对象。

## 导入派生业务表

旧目录中的以下脚本依赖了已经从 MySQL 删除的字段，因此不能直接对新库执行：

```text
scripts/import_projects.js
scripts/import_project_notices.js
scripts/import_project_notice_attachments.js
```

本目录已经提供对应的新版脚本。必须按顺序先 dry-run：

```bash
npm run import:projects
npm run import:project-notices
npm run import:project-notice-attachments
```

预期分别得到：

```text
project: 928
project_notice: 3843
project_notice_attachment: 34
```

确认后正式写入：

```bash
npm run import:projects -- --commit
npm run import:project-notices -- --commit
npm run import:project-notice-attachments -- --commit
```

新版 `project_notice` 脚本从 MongoDB 读取 `extractedFields/rawText`，将其作为经过处理的正式业务结果写入 MySQL 的 `project_notice.structured_data/content`。原始内容仍以 MongoDB 为准，不会恢复已经从 `raw_notice` 和 `notice_extraction` 删除的大字段。

映射目录中有 9 条已经不在当前 JSON/MySQL 原始公告集合中的历史记录；新版脚本按当前数据交集自动忽略，因此结果是 3843 条公告和 928 个项目。另有 1 条 `REVIEW_REQUIRED` 映射不会自动进入项目表。
