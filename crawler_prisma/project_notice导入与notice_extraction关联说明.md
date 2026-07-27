# project_notice 导入与 notice_extraction 关联说明

## 1. 目标

本文档对应脚本：`scripts/import_project_notices.js`。

脚本完成两项工作：

1. 将已经匹配到正式项目编号的公告写入 `project_notice`；
2. 将每条 `notice_extraction.project_notice_id` 更新为新建或已存在的 `project_notice.id`。

导入关系如下：

```text
notice_extraction
    + raw_notice
    + data_source
    + project_identity_mapping
             ↓
      project_notice
             ↓
notice_extraction.project_notice_id = project_notice.id
```

## 2. 前置条件和执行顺序

首次导入时必须先导入重新合并后的`project`：

```bash
cd /home/intsig/crawler_prisma
npm run import:projects -- --commit --replace
```

项目导入完成后，`project` 应有928条数据，其中905条有项目编号、23条为`project_code=NULL`的独立招标计划。之后才能导入`project_notice`，因为`project_notice.project_id`是不可为空的外键。

如果数据库已经存在引用旧项目ID的`project_notice`，重置`project.id`前必须按以下顺序重建：

```bash
# 1. 只清除旧公告和notice_extraction中的公告外键
npm run import:project-notices -- --commit --replace --clear-only

# 2. 重建project，id从1开始
npm run import:projects -- --commit --replace

# 3. 按新project.id重建公告并回填project_notice_id
npm run import:project-notices -- --commit --replace
```

项目关系映射文件必须存在：

```text
/home/intsig/Crawler_Scrapy/output/project_identity_mapping/huaxin_project_mapping.json
/home/intsig/Crawler_Scrapy/output/project_identity_mapping/jiubang_project_mapping.json
```

映射文件由 `scripts/import_projects.js` 生成，`project_notice` 导入脚本不会自行重新判断项目编号，确保两张表采用同一套项目关系。

## 3. 导入范围

当前关系映射共有 3,844 条公告：

| 处理结果 | 数量 | 是否写入 project_notice |
|---|---:|---|
| 已匹配到项目编号 | 3,820 | 是 |
| 无同名项目、对应独立计划项目 | 23 | 是 |
| 需要人工确认 | 1 | 否 |

预计写入的3,843条公告类型分布：

| notice_type | 数量 |
|---|---:|
| 招标计划 | 25 |
| 招标公告 | 1,359 |
| 中标候选人公示 | 942 |
| 中标结果公示 | 1,516 |
| 更正结果公示 | 1 |

## 4. project_notice 字段对应关系

| project_notice 字段 | 数据来源 | 写入规则 |
|---|---|---|
| `id` | 数据库 | 自增生成 |
| `project_id` | 关系映射的`项目编号` → `project.project_code` | 查询对应的`project.id`后写入；项目编号不存在或重复时停止导入 |
| `notice_type` | `notice_extraction.notice_type` | 保存标准中文公告类型 |
| `title` | `raw_notice.title` | 对应JSON的`公告标题`；为空或超过512字符时停止导入 |
| `content` | `raw_notice.raw_text` | 对应JSON的`公告正文/公告内容` |
| `structured_data` | `notice_extraction.extracted_fields` | 完整保存该公告的结构化抽取JSON |
| `publish_date` | `raw_notice.publish_date` | 原始导入时优先取JSON的`发布时间`，其次`发布日期` |
| `source_site` | `data_source.name` | 通过`raw_notice.data_source_id`关联取得；不写`short_code` |
| `source_url` | `raw_notice.source_url` | 对应JSON的`详情页链接` |
| `source_notice_id` | `raw_notice.source_notice_id` | 对应JSON的`公告ID` |
| `crawl_time` | `raw_notice.crawl_time` | 对应JSON的`爬虫时间` |
| `created_at` | 数据库 | 使用数据库当前时间自动生成 |

同一来源公告的应用级唯一键为：

```text
source_site + source_notice_id
```

当前表结构没有对应的唯一约束，因此脚本会在写入前检查导入集合和数据库现有数据是否重复，发现重复时停止执行。

## 5. project_id 的确定方式

关系映射文件包含：

```text
notice_extraction_id
公告ID
项目编号
匹配状态
匹配方式
```

脚本读取`匹配状态=MATCHED`和`匹配状态=STANDALONE_PROJECT`的记录。

有项目编号的公告执行：

```sql
project.project_code = 关系映射中的项目编号
```

匹配成功后：

```text
project_notice.project_id = project.id
```

独立招标计划没有项目编号，使用映射文件的`独立项目名称`查找：

```text
project.project_code IS NULL
+ project.project_name = 独立项目名称
```

同样要求只能找到一个`project.id`。

脚本要求每个项目编号在 `project` 表中恰好对应一条数据：

- 找不到项目编号：停止导入，提示先重新导入 `project`；
- 同一项目编号对应多个 `project.id`：停止导入，避免公告关联错误项目；
- 唯一匹配：继续写入。

## 6. notice_extraction.project_notice_id 回填

`project_notice`写入完成后，脚本使用以下对应关系回查ID：

```text
project_notice.source_site
+ project_notice.source_notice_id
        ↕
data_source.name
+ raw_notice.source_notice_id
```

然后批量更新：

```text
notice_extraction.project_notice_id = project_notice.id
```

全部操作位于同一个数据库事务中。只有3,843条目标抽取记录全部关联成功，事务才会提交；任何一条关联失败都会整体回滚。

## 7. 运行方式

### 7.1 预演

预演只读取和验证，不写数据库：

```bash
cd /home/intsig/crawler_prisma
npm run import:project-notices -- --replace
```

预期关键输出：

```text
Mode: DRY RUN (no database writes)
Matched mapping rows: 3843
Referenced project codes: 905
Referenced standalone plan projects: 23
Validated project_notice rows: 3843
Replace blockers: none
```

### 7.2 重置项目ID前只清除旧公告

先预演：

```bash
npm run import:project-notices -- --replace --clear-only
```

确认没有下游阻塞后正式清除：

```bash
npm run import:project-notices -- --commit --replace --clear-only
```

该模式只执行两项操作：

1. 将已有`notice_extraction.project_notice_id`置为`NULL`；
2. 删除旧`project_notice`。

不会修改`project`，也不会立即重新写入公告。

### 7.3 正式替换写入

```bash
npm run import:project-notices -- --commit --replace
```

该命令会：

1. 检查`project_notice_attachment`、`project_company_relation.notice_id`、`project_requirement.notice_id`和`contract.notice_id`；
2. 如果这些表已经引用`project_notice`，拒绝替换；
3. 将已有`notice_extraction.project_notice_id`临时置为`NULL`；
4. 删除已有`project_notice`；
5. 写入3,843条公告；
6. 回填3,843条`notice_extraction.project_notice_id`；
7. 新公告显式使用连续的`id=1..3843`；
8. 校验全部关联成功后提交事务，并把下一次`AUTO_INCREMENT`设置为3844。

### 7.4 不删除已有公告

```bash
npm run import:project-notices -- --commit
```

不带`--replace`时，脚本按照`source_site + source_notice_id`复用已有公告，只插入缺失公告，并重新建立`notice_extraction.project_notice_id`关联。如果已有公告关联到了不同项目，脚本会停止并要求人工检查或使用`--replace`。

## 8. 写入后验证SQL

### 8.1 project_notice总数

```sql
SELECT COUNT(*) AS project_notice_count
FROM project_notice;
```

本批数据预期为3,843。

同时验证主键范围：

```sql
SELECT
    COUNT(*) AS notice_count,
    MIN(id) AS min_id,
    MAX(id) AS max_id
FROM project_notice;
```

预期结果：

```text
notice_count = 3843
min_id       = 1
max_id       = 3843
```

验证下一自增值：

```sql
SELECT AUTO_INCREMENT
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'crawler'
  AND TABLE_NAME = 'project_notice';
```

预期为3844。

### 8.2 notice_extraction关联情况

```sql
SELECT
    COUNT(*) AS extraction_count,
    SUM(project_notice_id IS NOT NULL) AS linked_count,
    SUM(project_notice_id IS NULL) AS unlinked_count
FROM notice_extraction;
```

本批数据预期：

```text
extraction_count = 3844
linked_count     = 3843
unlinked_count   = 1
```

唯一未关联记录是需要人工确认项目关系的公告。

### 8.3 检查公告与抽取记录是否错连

```sql
SELECT COUNT(*) AS wrong_notice_links
FROM notice_extraction ne
JOIN raw_notice rn ON rn.id = ne.raw_notice_id
JOIN data_source ds ON ds.id = rn.data_source_id
JOIN project_notice pn ON pn.id = ne.project_notice_id
WHERE ne.project_notice_id IS NOT NULL
  AND (
      pn.source_notice_id <> rn.source_notice_id
      OR pn.source_site <> ds.name
  );
```

预期结果为0。

### 8.4 查询前5条及对应抽取记录

```sql
SELECT
    pn.id,
    pn.project_id,
    p.project_code,
    pn.notice_type,
    pn.title,
    pn.content,
    pn.structured_data,
    pn.publish_date,
    pn.source_site,
    pn.source_url,
    pn.source_notice_id,
    pn.crawl_time,
    pn.created_at,
    ne.id AS notice_extraction_id,
    ne.project_notice_id
FROM project_notice pn
JOIN project p ON p.id = pn.project_id
LEFT JOIN notice_extraction ne ON ne.project_notice_id = pn.id
ORDER BY pn.id
LIMIT 5;
```

## 9. 后续导入顺序

推荐顺序：

```text
project
  ↓
project_notice + 回填 notice_extraction.project_notice_id
  ↓
project_notice_attachment
  ↓
project_requirement / project_company_relation / contract
```

一旦后续表已经引用 `project_notice`，不要直接使用 `--replace`。应先明确下游数据的重建方案，避免公告ID变化导致关联丢失。
