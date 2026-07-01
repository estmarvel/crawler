# crawler 数据库表结构说明

> 数据库名：`crawler`  
> 数据库类型：MySQL 8.0  
> 设计目标：支撑招投标/政府采购公告采集、解析、项目事实建模、企业画像、项目推荐、竞争分析、中标概率分析、用户收藏与报告生成等业务。

------

## 1. 数据库整体说明

`crawler` 数据库按业务职责可以分为六层：

| 层级   | 作用                                                         | 代表表                                                       |
| ------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 采集层 | 保存爬虫数据源、采集任务、原始公告和原始附件                 | `data_source`、`crawl_task`、`raw_notice`、`raw_notice_attachment` |
| 解析层 | 保存公告字段抽取结果和项目资格/技术/业绩要求                 | `notice_extraction`、`project_requirement`                   |
| 事实层 | 保存企业、项目、公告、企业参与关系、联合体、项目人员、合同等客观事实 | `company`、`project`、`project_notice`、`project_company_relation`、`contract` |
| 画像层 | 保存企业资质、企业人员、企业画像快照                         | `company_qualification`、`company_personnel`、`company_profile_snapshot` |
| 结果层 | 保存推荐结果、竞争分析、中标概率、报告等计算结果             | `recommendation_result`、`competition_analysis`、`win_probability_analysis`、`report` |
| 基础层 | 保存字典、用户收藏、用户反馈等基础业务数据                   | `sys_dict`、`user_favorite`、`user_feedback`                 |

核心原则是：**事实和结论分离**。项目、企业、公告等客观信息放在事实层；推荐分、中标概率、竞争强度、策略建议等算法结果放在结果层。

------

## 2. 表结构总览

| 序号 | 表名                           | 所属层级 | 作用                         |
| ---: | ------------------------------ | -------- | ---------------------------- |
|    1 | `company`                      | 事实层   | 企业主数据表                 |
|    2 | `company_alias`                | 事实层   | 企业别名表，用于企业名称归一 |
|    3 | `project`                      | 事实层   | 项目主数据表                 |
|    4 | `user_favorite`                | 基础层   | 用户收藏项目表               |
|    5 | `project_notice`               | 事实层   | 项目公告表                   |
|    6 | `project_notice_attachment`    | 事实层   | 公告附件表                   |
|    7 | `project_company_relation`     | 事实层   | 项目和企业参与关系表         |
|    8 | `project_consortium_member`    | 事实层   | 联合体成员表                 |
|    9 | `project_company_person`       | 事实层   | 项目级人员表                 |
|   10 | `company_profile_snapshot`     | 画像层   | 企业画像快照表               |
|   11 | `data_source`                  | 采集层   | 数据源配置表                 |
|   12 | `crawl_task`                   | 采集层   | 采集任务表                   |
|   13 | `raw_notice`                   | 采集层   | 原始公告归档表               |
|   14 | `raw_notice_attachment`        | 采集层   | 原始公告附件归档表           |
|   15 | `notice_extraction`            | 解析层   | 公告字段抽取结果表           |
|   16 | `project_requirement`          | 解析层   | 项目资格/技术/业绩要求表     |
|   17 | `recommendation_result`        | 结果层   | 推荐结果表                   |
|   18 | `user_feedback`                | 基础层   | 用户反馈表                   |
|   19 | `recommendation_model_version` | 结果层   | 推荐模型版本表               |
|   20 | `competition_analysis`         | 结果层   | 竞争分析结果表               |
|   21 | `win_probability_analysis`     | 结果层   | 中标概率分析表               |
|   22 | `company_qualification`        | 画像层   | 企业资质证书表               |
|   23 | `company_personnel`            | 画像层   | 企业技术人员表               |
|   24 | `contract`                     | 事实层   | 合同和履约信息表             |
|   25 | `report`                       | 结果层   | 报告表                       |
|   26 | `sys_dict`                     | 基础层   | 系统字典表                   |

------

# 3. 各表详细说明

## 3.1 `company` 企业表

### 表作用

保存系统中的企业主数据，包括供应商、采购方、招标人、代理机构、竞争企业等。后续企业画像、项目关系、推荐分析都依赖这张表。

### 字段说明

| 字段                  | 类型          | 约束                                | 说明                                   |
| --------------------- | ------------- | ----------------------------------- | -------------------------------------- |
| `id`                  | INT           | PK, AUTO_INCREMENT                  | 企业 ID                                |
| `company_name`        | VARCHAR(191)  | NOT NULL                            | 标准企业名称                           |
| `credit_code`         | VARCHAR(191)  | UNIQUE, NULL                        | 统一社会信用代码，用于企业唯一识别     |
| `province`            | VARCHAR(191)  | NULL                                | 企业所在省份                           |
| `city`                | VARCHAR(191)  | NULL                                | 企业所在城市                           |
| `company_type`        | VARCHAR(191)  | NULL                                | 企业类型，例如供应商、采购方、代理机构 |
| `qualification_level` | VARCHAR(191)  | NULL                                | 当前简化资质等级                       |
| `business_scope`      | TEXT          | NULL                                | 企业经营范围                           |
| `registered_capital`  | DECIMAL(18,2) | NULL                                | 注册资本                               |
| `established_date`    | DATE          | NULL                                | 成立日期                               |
| `legal_person`        | VARCHAR(64)   | NULL                                | 法定代表人                             |
| `employee_count`      | INT           | NULL                                | 员工人数                               |
| `created_at`          | DATETIME(3)   | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间                               |
| `updated_at`          | DATETIME(3)   | NOT NULL                            | 更新时间                               |

### 主要索引

| 索引   | 字段           | 说明                     |
| ------ | -------------- | ------------------------ |
| UNIQUE | `credit_code`  | 根据统一社会信用代码去重 |
| INDEX  | `company_name` | 按企业名称查询           |

------

## 3.2 `company_alias` 企业别名表

### 表作用

处理同一企业在不同公告、不同平台中名称不一致的问题。例如“山西某某建设有限公司”和“山西某某建设有限责任公司”可能指向同一家企业。

### 字段说明

| 字段         | 类型         | 约束                                | 说明                           |
| ------------ | ------------ | ----------------------------------- | ------------------------------ |
| `id`         | INT          | PK, AUTO_INCREMENT                  | 主键 ID                        |
| `company_id` | INT          | FK -> `company.id`                  | 对应的标准企业 ID              |
| `alias_name` | VARCHAR(191) | NOT NULL                            | 企业别名或公告中的原始企业名称 |
| `source`     | VARCHAR(191) | NULL                                | 来源站点或来源公告             |
| `created_at` | DATETIME(3)  | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间                       |

### 主要索引

| 索引  | 字段         | 说明               |
| ----- | ------------ | ------------------ |
| INDEX | `company_id` | 查询某企业所有别名 |
| INDEX | `alias_name` | 通过别名反查企业   |

------

## 3.3 `project` 项目表

### 表作用

保存跨公告稳定的项目主数据。一个项目可能有招标公告、变更公告、中标候选人公示、中标结果公告、合同公告等多条公告，但这些公告都应归并到同一个项目下。

### 字段说明

| 字段                    | 类型          | 约束                                | 说明                           |
| ----------------------- | ------------- | ----------------------------------- | ------------------------------ |
| `id`                    | INT           | PK, AUTO_INCREMENT                  | 项目 ID                        |
| `project_code`          | VARCHAR(191)  | NULL                                | 项目编号、招标编号或采购编号   |
| `project_name`          | VARCHAR(191)  | NOT NULL                            | 项目名称                       |
| `project_nature`        | VARCHAR(191)  | NULL                                | 项目性质                       |
| `industry`              | VARCHAR(191)  | NULL                                | 所属行业                       |
| `project_type`          | VARCHAR(191)  | NULL                                | 项目类型，例如工程、货物、服务 |
| `tender_method`         | VARCHAR(191)  | NULL                                | 招标/采购方式                  |
| `organization_form`     | VARCHAR(191)  | NULL                                | 组织形式                       |
| `province`              | VARCHAR(191)  | NULL                                | 项目所在省份                   |
| `city`                  | VARCHAR(191)  | NULL                                | 项目所在城市                   |
| `location_text`         | TEXT          | NULL                                | 项目地点原文                   |
| `owner_company_id`      | INT           | FK -> `company.id`, NULL            | 招标人/采购人企业 ID           |
| `owner_company_name`    | VARCHAR(191)  | NULL                                | 招标人/采购人名称原文          |
| `agency_company_name`   | VARCHAR(191)  | NULL                                | 招标代理机构名称               |
| `estimated_amount`      | DECIMAL(18,2) | NULL                                | 估算金额或总投资               |
| `tender_amount`         | DECIMAL(18,2) | NULL                                | 招标金额、预算金额或最高限价   |
| `fund_source`           | VARCHAR(191)  | NULL                                | 资金来源                       |
| `bid_open_time`         | DATETIME(3)   | NULL                                | 开标时间                       |
| `duration`              | VARCHAR(191)  | NULL                                | 工期、服务期或合同期限         |
| `quality_requirement`   | TEXT          | NULL                                | 质量要求                       |
| `supervisor_department` | VARCHAR(191)  | NULL                                | 行政监督部门                   |
| `current_status`        | VARCHAR(191)  | NOT NULL, DEFAULT 'TENDER'          | 当前项目状态                   |
| `first_publish_date`    | DATETIME(3)   | NULL                                | 首次公告发布日期               |
| `created_at`            | DATETIME(3)   | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间                       |
| `updated_at`            | DATETIME(3)   | NOT NULL                            | 更新时间                       |

### 主要索引

| 索引  | 字段                                       | 说明                       |
| ----- | ------------------------------------------ | -------------------------- |
| INDEX | `project_code`                             | 按项目编号查询             |
| INDEX | `project_name`                             | 按项目名称查询             |
| INDEX | `current_status, province, city, industry` | 按状态、地区、行业筛选项目 |

------

## 3.4 `user_favorite` 用户收藏表

### 表作用

保存用户对项目的收藏和跟进状态，用于“我的关注项目”“待处理项目”“已投标项目”等功能。

### 字段说明

| 字段         | 类型           | 约束                                | 说明          |
| ------------ | -------------- | ----------------------------------- | ------------- |
| `id`         | INT            | PK, AUTO_INCREMENT                  | 收藏 ID       |
| `user_id`    | INT            | NOT NULL                            | 用户 ID       |
| `project_id` | INT            | FK -> `project.id`                  | 被收藏项目 ID |
| `status`     | ENUM / VARCHAR | NOT NULL, DEFAULT 'pending'         | 收藏跟进状态  |
| `note`       | TEXT           | NULL                                | 用户备注      |
| `created_at` | DATETIME(3)    | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 收藏时间      |
| `updated_at` | DATETIME(3)    | NOT NULL                            | 更新时间      |

### 状态值说明

| 值          | 说明   |
| ----------- | ------ |
| `pending`   | 待处理 |
| `following` | 跟进中 |
| `bid`       | 已投标 |
| `abandoned` | 已放弃 |

### 主要索引

| 索引   | 字段                  | 说明                         |
| ------ | --------------------- | ---------------------------- |
| UNIQUE | `user_id, project_id` | 同一用户不能重复收藏同一项目 |
| INDEX  | `user_id`             | 查询用户收藏列表             |
| INDEX  | `project_id`          | 查询项目被收藏情况           |
| INDEX  | `status`              | 按跟进状态筛选               |
| INDEX  | `created_at`          | 按收藏时间排序               |

------

## 3.5 `project_notice` 公告表

### 表作用

保存项目下的各类公告，包括公告标题、正文、公告类型、发布时间、来源站点、原始链接等。一个项目可以对应多条公告。

### 字段说明

| 字段               | 类型          | 约束                                | 说明                                 |
| ------------------ | ------------- | ----------------------------------- | ------------------------------------ |
| `id`               | INT           | PK, AUTO_INCREMENT                  | 公告 ID                              |
| `project_id`       | INT           | FK -> `project.id`                  | 所属项目 ID                          |
| `notice_type`      | VARCHAR(191)  | NOT NULL                            | 公告类型，例如招标公告、中标结果公告 |
| `title`            | VARCHAR(512)  | NOT NULL                            | 公告标题                             |
| `content`          | LONGTEXT      | NULL                                | 公告正文全文                         |
| `structured_data`  | JSON          | NULL                                | 公告结构化字段或临时扩展数据         |
| `publish_date`     | DATETIME(3)   | NULL                                | 公告发布时间                         |
| `source_site`      | VARCHAR(191)  | NULL                                | 来源网站名称                         |
| `source_url`       | VARCHAR(1024) | NULL                                | 源站公告链接                         |
| `source_notice_id` | VARCHAR(191)  | NULL                                | 源站公告 ID                          |
| `crawl_time`       | DATETIME(3)   | NULL                                | 爬取时间                             |
| `created_at`       | DATETIME(3)   | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 入库时间                             |

### 主要索引

| 索引  | 字段                        | 说明                       |
| ----- | --------------------------- | -------------------------- |
| INDEX | `project_id`                | 查询项目下的公告           |
| INDEX | `notice_type`               | 按公告类型筛选             |
| INDEX | `publish_date`              | 按发布时间排序或筛选       |
| INDEX | `source_notice_id`          | 根据源站公告 ID 去重或追踪 |
| INDEX | `notice_type, publish_date` | 按类型和时间联合查询       |

### 注意事项

`structured_data` 可以保存临时 JSON 扩展字段，但正式推荐分、风险、理由、中标概率等结果应迁移到 `recommendation_result`、`win_probability_analysis` 等结果表中。

------

## 3.6 `project_notice_attachment` 公告附件表

### 表作用

保存结构化公告对应的附件信息，例如招标文件、采购文件、PDF 附件、结果公告附件等。

### 字段说明

| 字段              | 类型          | 约束                                | 说明                           |
| ----------------- | ------------- | ----------------------------------- | ------------------------------ |
| `id`              | INT           | PK, AUTO_INCREMENT                  | 附件 ID                        |
| `notice_id`       | INT           | FK -> `project_notice.id`           | 所属公告 ID                    |
| `file_name`       | VARCHAR(191)  | NOT NULL                            | 附件文件名                     |
| `file_url`        | VARCHAR(1024) | NULL                                | 附件原始下载链接               |
| `file_type`       | VARCHAR(191)  | NULL                                | 文件类型，例如 PDF、DOCX、XLSX |
| `storage_path`    | VARCHAR(1024) | NULL                                | 本地或对象存储路径             |
| `file_hash`       | VARCHAR(64)   | NULL                                | 文件哈希，用于去重             |
| `file_size_bytes` | BIGINT        | NULL                                | 文件大小，单位字节             |
| `parse_status`    | VARCHAR(32)   | DEFAULT 'PENDING'                   | 附件解析状态                   |
| `created_at`      | DATETIME(3)   | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间                       |

### 主要索引

| 索引  | 字段           | 说明               |
| ----- | -------------- | ------------------ |
| INDEX | `notice_id`    | 查询某公告下的附件 |
| INDEX | `file_hash`    | 附件去重           |
| INDEX | `parse_status` | 查询待解析附件     |

------

## 3.7 `project_company_relation` 项目企业关系表

### 表作用

记录企业在某个项目中的角色，例如投标人、候选人、中标人、合同签约方。该表是企业画像、竞争分析、中标概率分析的核心事实表。

### 字段说明

| 字段                   | 类型          | 约束                                | 说明                 |
| ---------------------- | ------------- | ----------------------------------- | -------------------- |
| `id`                   | INT           | PK, AUTO_INCREMENT                  | 主键 ID              |
| `project_id`           | INT           | FK -> `project.id`                  | 项目 ID              |
| `notice_id`            | INT           | FK -> `project_notice.id`, NULL     | 来源公告 ID          |
| `company_id`           | INT           | FK -> `company.id`, NULL            | 归一后的企业 ID      |
| `company_name`         | VARCHAR(191)  | NOT NULL                            | 公告中的企业名称原文 |
| `relation_type`        | VARCHAR(191)  | NOT NULL                            | 企业与项目关系       |
| `stage_type`           | VARCHAR(191)  | NULL                                | 所属公告阶段         |
| `ranking`              | INT           | NULL                                | 候选排名             |
| `bid_amount`           | DECIMAL(18,2) | NULL                                | 投标报价或中标金额   |
| `is_winner`            | TINYINT(1)    | NOT NULL, DEFAULT 0                 | 是否中标             |
| `is_consortium`        | TINYINT(1)    | NOT NULL, DEFAULT 0                 | 是否联合体           |
| `is_consortium_leader` | TINYINT(1)    | NOT NULL, DEFAULT 0                 | 是否联合体牵头方     |
| `created_at`           | DATETIME(3)   | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间             |

### 关系类型建议

| 值           | 说明       |
| ------------ | ---------- |
| `BIDDER`     | 投标人     |
| `CANDIDATE`  | 候选人     |
| `WINNER`     | 中标人     |
| `CONTRACTOR` | 合同签约方 |

### 主要索引

| 索引   | 字段                                                  | 说明                           |
| ------ | ----------------------------------------------------- | ------------------------------ |
| INDEX  | `project_id`                                          | 查询项目参与企业               |
| INDEX  | `company_id`                                          | 查询企业参与过的项目           |
| INDEX  | `notice_id`                                           | 查询公告中出现的企业关系       |
| INDEX  | `project_id, relation_type`                           | 查询项目下某类企业，如中标人   |
| INDEX  | `company_id, relation_type`                           | 查询企业作为某种角色出现的记录 |
| UNIQUE | `project_id, company_name, relation_type, stage_type` | 建议用于去重                   |

------

## 3.8 `project_consortium_member` 联合体成员表

### 表作用

保存联合体投标中的成员企业明细。`project_company_relation` 记录联合体整体关系，本表记录联合体内部每个成员。

### 字段说明

| 字段                  | 类型         | 约束                                | 说明                           |
| --------------------- | ------------ | ----------------------------------- | ------------------------------ |
| `id`                  | INT          | PK, AUTO_INCREMENT                  | 主键 ID                        |
| `relation_id`         | INT          | FK -> `project_company_relation.id` | 对应的项目企业关系 ID          |
| `member_company_id`   | INT          | NULL                                | 归一后的成员企业 ID            |
| `member_company_name` | VARCHAR(191) | NOT NULL                            | 成员企业名称原文               |
| `member_role`         | VARCHAR(191) | NULL                                | 成员角色，例如牵头人、成员单位 |
| `created_at`          | DATETIME(3)  | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间                       |

### 主要索引

| 索引  | 字段                             | 说明                             |
| ----- | -------------------------------- | -------------------------------- |
| INDEX | `relation_id`                    | 查询某联合体关系下的成员         |
| INDEX | `member_company_id, member_role` | 查询企业作为联合体成员的历史记录 |

------

## 3.9 `project_company_person` 项目人员表

### 表作用

保存某个项目公告中出现的人员信息，例如项目经理、项目负责人、总监理工程师及其证书信息。

### 字段说明

| 字段               | 类型         | 约束                                | 说明                |
| ------------------ | ------------ | ----------------------------------- | ------------------- |
| `id`               | INT          | PK, AUTO_INCREMENT                  | 主键 ID             |
| `relation_id`      | INT          | FK -> `project_company_relation.id` | 所属项目企业关系 ID |
| `person_name`      | VARCHAR(191) | NOT NULL                            | 人员姓名            |
| `person_role`      | VARCHAR(191) | NULL                                | 人员角色            |
| `certificate_name` | VARCHAR(191) | NULL                                | 证书名称            |
| `certificate_no`   | VARCHAR(191) | NULL                                | 证书编号            |
| `created_at`       | DATETIME(3)  | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间            |

### 主要索引

| 索引  | 字段          | 说明                           |
| ----- | ------------- | ------------------------------ |
| INDEX | `relation_id` | 查询某企业参与关系下的项目人员 |

### 与 `company_personnel` 的区别

- `project_company_person`：记录公告里出现的项目级人员。
- `company_personnel`：记录企业长期可用的技术人员池。

------

## 3.10 `company_profile_snapshot` 企业画像快照表

### 表作用

保存企业画像计算结果，例如近三年中标数量、主要行业、主要区域、平均中标金额等。用于推荐、竞争分析和 Dashboard 加速展示。

### 字段说明

| 字段                   | 类型          | 约束                                | 说明               |
| ---------------------- | ------------- | ----------------------------------- | ------------------ |
| `id`                   | INT           | PK, AUTO_INCREMENT                  | 快照 ID            |
| `company_id`           | INT           | FK -> `company.id`                  | 企业 ID            |
| `snapshot_date`        | DATETIME(3)   | NOT NULL                            | 快照日期           |
| `win_project_count_3y` | INT           | NOT NULL, DEFAULT 0                 | 近三年中标项目数量 |
| `main_industries`      | JSON          | NULL                                | 主要行业分布       |
| `main_regions`         | JSON          | NULL                                | 主要区域分布       |
| `avg_win_amount`       | DECIMAL(18,2) | NULL                                | 平均中标金额       |
| `profile_json`         | JSON          | NULL                                | 完整画像扩展数据   |
| `created_at`           | DATETIME(3)   | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间           |

### 主要索引

| 索引   | 字段                        | 说明                                   |
| ------ | --------------------------- | -------------------------------------- |
| INDEX  | `company_id, snapshot_date` | 查询企业历史画像                       |
| UNIQUE | `company_id, snapshot_date` | 建议保证同一企业同一快照日只有一条记录 |

------

## 3.11 `data_source` 数据源配置表

### 表作用

保存爬虫数据源配置，例如山西政府采购网、公共资源交易平台、招投标平台等。每个数据源可以对应多个采集任务。

### 字段说明

| 字段                      | 类型         | 约束               | 说明                                       |
| ------------------------- | ------------ | ------------------ | ------------------------------------------ |
| `id`                      | INT          | PK, AUTO_INCREMENT | 数据源 ID                                  |
| `name`                    | VARCHAR(200) | NOT NULL           | 站点名称                                   |
| `short_code`              | VARCHAR(32)  | UNIQUE             | 站点简码，例如 `ccgp_shanxi`               |
| `base_url`                | VARCHAR(512) | NULL               | 站点主域名                                 |
| `source_level`            | VARCHAR(32)  | NULL               | 数据源层级，例如国家级、省级、市级、平台级 |
| `province`                | VARCHAR(32)  | NULL               | 所属省份                                   |
| `crawl_frequency_minutes` | INT          | DEFAULT 60         | 默认采集频率，单位分钟                     |
| `crawl_config`            | JSON         | NULL               | 爬虫配置，例如接口、参数、分类映射、请求头 |
| `is_enabled`              | TINYINT(1)   | DEFAULT 1          | 是否启用                                   |
| `created_at`              | DATETIME     | NOT NULL           | 创建时间                                   |
| `updated_at`              | DATETIME     | NOT NULL           | 更新时间                                   |

### 主要索引

| 索引   | 字段         | 说明             |
| ------ | ------------ | ---------------- |
| UNIQUE | `short_code` | 数据源简码唯一   |
| INDEX  | `is_enabled` | 查询启用的数据源 |

------

## 3.12 `crawl_task` 采集任务表

### 表作用

记录每一次采集任务的调度、执行状态、执行时间、成功数量、失败数量和错误信息。便于监控爬虫运行情况。

### 字段说明

| 字段             | 类型        | 约束                   | 说明                                       |
| ---------------- | ----------- | ---------------------- | ------------------------------------------ |
| `id`             | BIGINT      | PK, AUTO_INCREMENT     | 任务 ID                                    |
| `data_source_id` | INT         | FK -> `data_source.id` | 数据源 ID                                  |
| `task_type`      | VARCHAR(32) | NOT NULL               | 任务类型，例如列表采集、详情采集、附件下载 |
| `status`         | VARCHAR(32) | DEFAULT 'PENDING'      | 任务状态                                   |
| `scheduled_at`   | DATETIME    | NULL                   | 计划执行时间                               |
| `started_at`     | DATETIME    | NULL                   | 实际开始时间                               |
| `finished_at`    | DATETIME    | NULL                   | 实际结束时间                               |
| `total_count`    | INT         | DEFAULT 0              | 总处理数量                                 |
| `success_count`  | INT         | DEFAULT 0              | 成功数量                                   |
| `fail_count`     | INT         | DEFAULT 0              | 失败数量                                   |
| `error_message`  | TEXT        | NULL                   | 错误信息                                   |
| `created_at`     | DATETIME    | NOT NULL               | 创建时间                                   |

### 状态值建议

| 值          | 说明   |
| ----------- | ------ |
| `PENDING`   | 待执行 |
| `RUNNING`   | 执行中 |
| `SUCCESS`   | 成功   |
| `FAILED`    | 失败   |
| `CANCELLED` | 已取消 |

### 主要索引

| 索引  | 字段                     | 说明                       |
| ----- | ------------------------ | -------------------------- |
| INDEX | `data_source_id, status` | 查询某数据源下不同状态任务 |
| INDEX | `status, scheduled_at`   | 调度器查询待执行任务       |

------

## 3.13 `raw_notice` 原始公告归档表

### 表作用

保存从源站采集到的原始公告，包括原始 HTML、清洗后的纯文本、源站链接、发布时间、采集时间、解析状态等。该表属于采集原始层，便于追溯和重新解析。

### 字段说明

| 字段               | 类型          | 约束                        | 说明            |
| ------------------ | ------------- | --------------------------- | --------------- |
| `id`               | BIGINT        | PK, AUTO_INCREMENT          | 原始公告 ID     |
| `data_source_id`   | INT           | FK -> `data_source.id`      | 数据源 ID       |
| `crawl_task_id`    | BIGINT        | FK -> `crawl_task.id`, NULL | 来源采集任务 ID |
| `source_url`       | VARCHAR(1024) | NOT NULL                    | 原始公告链接    |
| `source_notice_id` | VARCHAR(256)  | NULL                        | 源站公告 ID     |
| `title`            | VARCHAR(512)  | NULL                        | 公告标题        |
| `raw_html`         | LONGTEXT      | NULL                        | 原始 HTML 内容  |
| `raw_text`         | LONGTEXT      | NULL                        | 清洗后的纯文本  |
| `publish_date`     | DATETIME      | NULL                        | 公告发布时间    |
| `crawl_time`       | DATETIME      | NOT NULL                    | 爬取时间        |
| `parse_status`     | VARCHAR(32)   | DEFAULT 'PENDING'           | 解析状态        |
| `fingerprint`      | VARCHAR(64)   | NULL                        | 去重指纹        |
| `created_at`       | DATETIME      | NOT NULL                    | 创建时间        |

### 解析状态建议

| 值        | 说明     |
| --------- | -------- |
| `PENDING` | 待解析   |
| `PARSING` | 解析中   |
| `PARSED`  | 已解析   |
| `FAILED`  | 解析失败 |

### 主要索引

| 索引   | 字段                               | 说明           |
| ------ | ---------------------------------- | -------------- |
| UNIQUE | `data_source_id, source_notice_id` | 源站公告去重   |
| INDEX  | `parse_status`                     | 查询待解析公告 |
| INDEX  | `fingerprint`                      | 内容指纹去重   |
| INDEX  | `publish_date`                     | 按发布时间筛选 |
| INDEX  | `crawl_time`                       | 按采集时间筛选 |

------

## 3.14 `raw_notice_attachment` 原始公告附件归档表

### 表作用

保存原始公告中发现的附件，属于采集原始层。与 `project_notice_attachment` 不同，本表保存的是源站原始附件，后续解析归并后再进入公告附件事实表。

### 字段说明

| 字段              | 类型          | 约束                  | 说明               |
| ----------------- | ------------- | --------------------- | ------------------ |
| `id`              | BIGINT        | PK, AUTO_INCREMENT    | 附件 ID            |
| `raw_notice_id`   | BIGINT        | FK -> `raw_notice.id` | 原始公告 ID        |
| `file_name`       | VARCHAR(512)  | NULL                  | 附件文件名         |
| `file_url`        | VARCHAR(1024) | NULL                  | 原始附件下载链接   |
| `storage_path`    | VARCHAR(1024) | NULL                  | 本地或对象存储路径 |
| `file_hash`       | VARCHAR(64)   | NULL                  | 文件哈希           |
| `file_size_bytes` | BIGINT        | NULL                  | 文件大小，单位字节 |
| `file_type`       | VARCHAR(32)   | NULL                  | 文件类型           |
| `parse_status`    | VARCHAR(32)   | DEFAULT 'PENDING'     | 附件解析状态       |
| `created_at`      | DATETIME      | NOT NULL              | 创建时间           |

### 主要索引

| 索引  | 字段            | 说明               |
| ----- | --------------- | ------------------ |
| INDEX | `raw_notice_id` | 查询某原始公告附件 |
| INDEX | `file_hash`     | 附件去重           |
| INDEX | `parse_status`  | 查询待解析附件     |

------

## 3.15 `notice_extraction` 公告字段抽取结果表

### 表作用

保存解析器、规则模型或 AI 从公告中抽取出的结构化字段，例如项目名称、预算金额、采购人、代理机构、开标时间、资质要求等。

### 字段说明

| 字段                  | 类型         | 约束                            | 说明                  |
| --------------------- | ------------ | ------------------------------- | --------------------- |
| `id`                  | BIGINT       | PK, AUTO_INCREMENT              | 抽取结果 ID           |
| `raw_notice_id`       | BIGINT       | FK -> `raw_notice.id`           | 原始公告 ID           |
| `project_notice_id`   | INT          | FK -> `project_notice.id`, NULL | 归并后的结构化公告 ID |
| `notice_type`         | VARCHAR(64)  | NOT NULL                        | 公告类型              |
| `extracted_fields`    | JSON         | NOT NULL                        | 抽取字段全集          |
| `extraction_model`    | VARCHAR(64)  | NULL                            | 模型名称或规则名称    |
| `extraction_version`  | VARCHAR(32)  | NULL                            | 抽取版本              |
| `confidence_score`    | DECIMAL(5,4) | NULL                            | 整体置信度            |
| `field_confidences`   | JSON         | NULL                            | 字段级置信度          |
| `source_text_snippet` | TEXT         | NULL                            | 字段对应的原文片段    |
| `is_verified`         | TINYINT(1)   | DEFAULT 0                       | 是否人工审核          |
| `verified_by`         | INT          | NULL                            | 审核人 ID             |
| `verified_at`         | DATETIME     | NULL                            | 审核时间              |
| `created_at`          | DATETIME     | NOT NULL                        | 创建时间              |
| `updated_at`          | DATETIME     | NOT NULL                        | 更新时间              |

### 主要索引

| 索引  | 字段                       | 说明                       |
| ----- | -------------------------- | -------------------------- |
| INDEX | `raw_notice_id`            | 查询原文公告的抽取结果     |
| INDEX | `project_notice_id`        | 查询结构化公告对应抽取结果 |
| INDEX | `notice_type, is_verified` | 按公告类型和审核状态筛选   |

------

## 3.16 `project_requirement` 项目要求表

### 表作用

拆分并保存项目中的资格要求、资质要求、技术要求、人员要求、业绩要求等。该表主要服务于推荐匹配和中标概率分析。

### 字段说明

| 字段               | 类型        | 约束                            | 说明         |
| ------------------ | ----------- | ------------------------------- | ------------ |
| `id`               | INT         | PK, AUTO_INCREMENT              | 要求 ID      |
| `project_id`       | INT         | FK -> `project.id`              | 项目 ID      |
| `notice_id`        | INT         | FK -> `project_notice.id`, NULL | 来源公告 ID  |
| `requirement_type` | VARCHAR(64) | NOT NULL                        | 要求类型     |
| `requirement_text` | TEXT        | NOT NULL                        | 要求原文     |
| `keywords`         | JSON        | NULL                            | 关键词列表   |
| `is_mandatory`     | TINYINT(1)  | DEFAULT 1                       | 是否必要条件 |
| `created_at`       | DATETIME    | NOT NULL                        | 创建时间     |

### 要求类型示例

| 值              | 说明     |
| --------------- | -------- |
| `QUALIFICATION` | 资质要求 |
| `PERSONNEL`     | 人员要求 |
| `PERFORMANCE`   | 业绩要求 |
| `TECHNICAL`     | 技术要求 |
| `FINANCIAL`     | 财务要求 |

### 主要索引

| 索引  | 字段                           | 说明                   |
| ----- | ------------------------------ | ---------------------- |
| INDEX | `project_id, requirement_type` | 查询项目下不同类型要求 |
| INDEX | `notice_id`                    | 查询某公告抽取出的要求 |

------

## 3.17 `recommendation_result` 推荐结果表

### 表作用

保存某企业对某项目的推荐匹配结果，是推荐列表、项目详情推荐理由、风险提示、匹配分排序的核心来源。

### 字段说明

| 字段                | 类型         | 约束               | 说明                                       |
| ------------------- | ------------ | ------------------ | ------------------------------------------ |
| `id`                | BIGINT       | PK, AUTO_INCREMENT | 推荐结果 ID                                |
| `company_id`        | INT          | FK -> `company.id` | 被推荐企业 ID                              |
| `project_id`        | INT          | FK -> `project.id` | 推荐项目 ID                                |
| `match_score`       | DECIMAL(5,2) | NOT NULL           | 综合匹配分，0-100                          |
| `win_probability`   | DECIMAL(5,2) | NULL               | 中标概率，0-100                            |
| `recommend_level`   | VARCHAR(32)  | NULL               | 推荐等级                                   |
| `competition_level` | VARCHAR(16)  | NULL               | 竞争强度                                   |
| `score_breakdown`   | JSON         | NULL               | 子分明细，例如资质匹配、区域匹配、业绩匹配 |
| `reason`            | JSON         | NULL               | 推荐理由列表                               |
| `risk`              | JSON         | NULL               | 风险提示列表                               |
| `algorithm_version` | VARCHAR(32)  | NOT NULL           | 算法版本                                   |
| `is_read`           | TINYINT(1)   | DEFAULT 0          | 用户是否已读                               |
| `created_at`        | DATETIME     | NOT NULL           | 生成时间                                   |
| `expired_at`        | DATETIME     | NULL               | 推荐有效期                                 |

### 推荐等级建议

| 值        | 含义     | 建议规则                 |
| --------- | -------- | ------------------------ |
| `STRONG`  | 强烈推荐 | `match_score >= 85`      |
| `NORMAL`  | 推荐关注 | `70 <= match_score < 85` |
| `CAUTION` | 谨慎参与 | `match_score < 70`       |

### 主要索引

| 索引   | 字段                                        | 说明                                       |
| ------ | ------------------------------------------- | ------------------------------------------ |
| UNIQUE | `company_id, project_id, algorithm_version` | 同一算法版本下同企业同项目只有一条推荐结果 |
| INDEX  | `company_id, match_score`                   | 推荐列表按匹配分排序                       |
| INDEX  | `company_id, created_at`                    | 查询企业近期推荐项目                       |
| INDEX  | `project_id`                                | 查询某项目的推荐情况                       |

------

## 3.18 `user_feedback` 用户反馈表

### 表作用

保存用户对推荐项目的反馈，例如喜欢、不喜欢、适合、不适合。后续可以作为推荐模型优化和排序训练的数据来源。

### 字段说明

| 字段            | 类型        | 约束               | 说明            |
| --------------- | ----------- | ------------------ | --------------- |
| `id`            | INT         | PK, AUTO_INCREMENT | 反馈 ID         |
| `user_id`       | INT         | NOT NULL           | 用户 ID         |
| `company_id`    | INT         | FK -> `company.id` | 用户所属企业 ID |
| `project_id`    | INT         | FK -> `project.id` | 反馈项目 ID     |
| `feedback_type` | VARCHAR(32) | NOT NULL           | 反馈类型        |
| `comment`       | TEXT        | NULL               | 反馈备注        |
| `created_at`    | DATETIME    | NOT NULL           | 创建时间        |

### 反馈类型建议

| 值           | 说明   |
| ------------ | ------ |
| `LIKE`       | 喜欢   |
| `DISLIKE`    | 不喜欢 |
| `SUITABLE`   | 适合   |
| `UNSUITABLE` | 不适合 |

### 主要索引

| 索引   | 字段                        | 说明                             |
| ------ | --------------------------- | -------------------------------- |
| UNIQUE | `user_id, project_id`       | 同一用户对同一项目只保留一条反馈 |
| INDEX  | `company_id, feedback_type` | 查询企业下不同类型反馈           |
| INDEX  | `project_id`                | 查询项目反馈情况                 |

------

## 3.19 `recommendation_model_version` 推荐模型版本表

### 表作用

管理推荐算法版本，包括规则模型、机器学习模型、特征配置、权重配置和评估指标。

### 字段说明

| 字段             | 类型        | 约束               | 说明                   |
| ---------------- | ----------- | ------------------ | ---------------------- |
| `id`             | INT         | PK, AUTO_INCREMENT | 版本 ID                |
| `version_code`   | VARCHAR(32) | UNIQUE             | 版本号，例如 `rule-v1` |
| `model_type`     | VARCHAR(32) | NOT NULL           | 模型类型               |
| `feature_config` | JSON        | NULL               | 特征和权重配置         |
| `description`    | TEXT        | NULL               | 版本说明               |
| `is_active`      | TINYINT(1)  | DEFAULT 0          | 是否当前生效           |
| `metrics`        | JSON        | NULL               | 离线评估指标           |
| `created_at`     | DATETIME    | NOT NULL           | 创建时间               |

### 模型类型建议

| 值         | 说明          |
| ---------- | ------------- |
| `RULE`     | 规则模型      |
| `LIGHTGBM` | LightGBM 模型 |
| `XGBOOST`  | XGBoost 模型  |

### 主要索引

| 索引   | 字段           | 说明             |
| ------ | -------------- | ---------------- |
| UNIQUE | `version_code` | 模型版本号唯一   |
| INDEX  | `is_active`    | 查询当前启用版本 |

------

## 3.20 `competition_analysis` 竞争分析结果表

### 表作用

保存某项目下我方企业与潜在竞争对手的对比分析结果，包括威胁等级、竞争力评分、历史交锋、优势短板、雷达图数据等。

### 字段说明

| 字段                      | 类型         | 约束                     | 说明             |
| ------------------------- | ------------ | ------------------------ | ---------------- |
| `id`                      | BIGINT       | PK, AUTO_INCREMENT       | 分析结果 ID      |
| `project_id`              | INT          | FK -> `project.id`       | 项目 ID          |
| `target_company_id`       | INT          | FK -> `company.id`       | 我方企业 ID      |
| `competitor_company_id`   | INT          | FK -> `company.id`, NULL | 竞争企业 ID      |
| `competitor_name`         | VARCHAR(200) | NULL                     | 竞争企业名称原文 |
| `threat_level`            | VARCHAR(32)  | NULL                     | 威胁等级         |
| `competitor_score`        | DECIMAL(5,2) | NULL                     | 竞争力评分       |
| `overall_win_rate`        | DECIMAL(5,2) | NULL                     | 对手历史总中标率 |
| `encounter_count`         | INT          | DEFAULT 0                | 历史交锋次数     |
| `encounter_opponent_wins` | INT          | DEFAULT 0                | 对方胜出次数     |
| `advantages`              | JSON         | NULL                     | 对手优势         |
| `weaknesses`              | JSON         | NULL                     | 对手短板         |
| `our_advantages`          | JSON         | NULL                     | 我方相对优势     |
| `our_weaknesses`          | JSON         | NULL                     | 我方主要短板     |
| `radar_data`              | JSON         | NULL                     | 六维雷达图数据   |
| `algorithm_version`       | VARCHAR(32)  | NULL                     | 算法版本         |
| `created_at`              | DATETIME     | NOT NULL                 | 创建时间         |

### 竞争强度建议

| 值       | 说明 |
| -------- | ---- |
| `LOW`    | 低   |
| `MEDIUM` | 中   |
| `HIGH`   | 高   |

### 主要索引

| 索引  | 字段                            | 说明                           |
| ----- | ------------------------------- | ------------------------------ |
| INDEX | `project_id, target_company_id` | 查询某企业在某项目下的竞争分析 |
| INDEX | `competitor_company_id`         | 查询某竞争企业相关分析         |

------

## 3.21 `win_probability_analysis` 中标概率分析表

### 表作用

保存某企业针对某项目的中标概率分析，包括概率、置信区间、正向因素、负向因素、风险因素和投标策略建议。

### 字段说明

| 字段                      | 类型         | 约束               | 说明            |
| ------------------------- | ------------ | ------------------ | --------------- |
| `id`                      | BIGINT       | PK, AUTO_INCREMENT | 分析 ID         |
| `project_id`              | INT          | FK -> `project.id` | 项目 ID         |
| `company_id`              | INT          | FK -> `company.id` | 企业 ID         |
| `win_probability`         | DECIMAL(5,2) | NULL               | 中标概率，0-100 |
| `probability_lower`       | DECIMAL(5,2) | NULL               | 置信区间下限    |
| `probability_upper`       | DECIMAL(5,2) | NULL               | 置信区间上限    |
| `positive_factors`        | JSON         | NULL               | 正向因素        |
| `negative_factors`        | JSON         | NULL               | 负向因素        |
| `risk_factors`            | JSON         | NULL               | 风险因素        |
| `suggestions`             | JSON         | NULL               | 投标策略建议    |
| `competition_intensity`   | VARCHAR(16)  | NULL               | 竞争强度        |
| `known_competitors_count` | INT          | DEFAULT 0          | 已知竞争者数量  |
| `algorithm_version`       | VARCHAR(32)  | NULL               | 算法版本        |
| `created_at`              | DATETIME     | NOT NULL           | 创建时间        |

### 主要索引

| 索引  | 字段                     | 说明                         |
| ----- | ------------------------ | ---------------------------- |
| INDEX | `project_id, company_id` | 查询某企业对某项目的中标概率 |
| INDEX | `company_id, created_at` | 查询企业近期概率分析         |

------

## 3.22 `company_qualification` 企业资质证书表

### 表作用

保存企业级资质、认证和证书明细，用于项目资格匹配和企业画像。

### 字段说明

| 字段                | 类型         | 约束               | 说明     |
| ------------------- | ------------ | ------------------ | -------- |
| `id`                | INT          | PK, AUTO_INCREMENT | 资质 ID  |
| `company_id`        | INT          | FK -> `company.id` | 企业 ID  |
| `cert_name`         | VARCHAR(200) | NOT NULL           | 证书名称 |
| `cert_level`        | VARCHAR(64)  | NULL               | 证书等级 |
| `cert_no`           | VARCHAR(128) | NULL               | 证书编号 |
| `issue_date`        | DATE         | NULL               | 发证日期 |
| `expiry_date`       | DATE         | NULL               | 到期日期 |
| `issuing_authority` | VARCHAR(200) | NULL               | 发证机构 |
| `status`            | VARCHAR(32)  | DEFAULT 'VALID'    | 证书状态 |
| `created_at`        | DATETIME     | NOT NULL           | 创建时间 |
| `updated_at`        | DATETIME     | NOT NULL           | 更新时间 |

### 证书状态建议

| 值        | 说明   |
| --------- | ------ |
| `VALID`   | 有效   |
| `EXPIRED` | 已过期 |
| `REVOKED` | 已吊销 |
| `UNKNOWN` | 未知   |

### 主要索引

| 索引  | 字段                 | 说明             |
| ----- | -------------------- | ---------------- |
| INDEX | `company_id, status` | 查询企业有效资质 |
| INDEX | `expiry_date`        | 查询即将过期资质 |

------

## 3.23 `company_personnel` 企业技术人员表

### 表作用

保存企业可用于投标和履约的技术人员池，例如项目经理、注册建造师、监理工程师、技术负责人等。

### 字段说明

| 字段           | 类型         | 约束               | 说明       |
| -------------- | ------------ | ------------------ | ---------- |
| `id`           | INT          | PK, AUTO_INCREMENT | 人员 ID    |
| `company_id`   | INT          | FK -> `company.id` | 企业 ID    |
| `person_name`  | VARCHAR(64)  | NULL               | 人员姓名   |
| `person_role`  | VARCHAR(64)  | NULL               | 岗位或职称 |
| `cert_name`    | VARCHAR(200) | NULL               | 持证名称   |
| `cert_no`      | VARCHAR(128) | NULL               | 证书编号   |
| `cert_level`   | VARCHAR(64)  | NULL               | 证书等级   |
| `is_available` | TINYINT(1)   | DEFAULT 1          | 是否可调配 |
| `created_at`   | DATETIME     | NOT NULL           | 创建时间   |
| `updated_at`   | DATETIME     | NOT NULL           | 更新时间   |

### 主要索引

| 索引  | 字段         | 说明               |
| ----- | ------------ | ------------------ |
| INDEX | `company_id` | 查询企业人员池     |
| INDEX | `cert_name`  | 按证书名称查询人员 |

------

## 3.24 `contract` 合同表

### 表作用

保存合同与履约相关公告的结构化结果，例如合同编号、合同名称、采购方、供应商、合同金额、签署日期、履约状态等。

### 字段说明

| 字段                 | 类型          | 约束                            | 说明           |
| -------------------- | ------------- | ------------------------------- | -------------- |
| `id`                 | INT           | PK, AUTO_INCREMENT              | 合同 ID        |
| `project_id`         | INT           | FK -> `project.id`              | 项目 ID        |
| `notice_id`          | INT           | FK -> `project_notice.id`, NULL | 来源公告 ID    |
| `contract_no`        | VARCHAR(128)  | NULL                            | 合同编号       |
| `contract_name`      | VARCHAR(512)  | NULL                            | 合同名称       |
| `buyer_name`         | VARCHAR(200)  | NULL                            | 采购方名称     |
| `seller_company_id`  | INT           | FK -> `company.id`, NULL        | 供应商企业 ID  |
| `seller_name`        | VARCHAR(200)  | NULL                            | 供应商名称原文 |
| `contract_amount`    | DECIMAL(18,2) | NULL                            | 合同金额       |
| `sign_date`          | DATE          | NULL                            | 签署日期       |
| `start_date`         | DATE          | NULL                            | 合同开始日期   |
| `end_date`           | DATE          | NULL                            | 合同结束日期   |
| `contract_content`   | TEXT          | NULL                            | 合同主要内容   |
| `performance_status` | VARCHAR(32)   | DEFAULT 'ONGOING'               | 履约状态       |
| `created_at`         | DATETIME      | NOT NULL                        | 创建时间       |
| `updated_at`         | DATETIME      | NOT NULL                        | 更新时间       |

### 履约状态建议

| 值           | 说明   |
| ------------ | ------ |
| `ONGOING`    | 履约中 |
| `COMPLETED`  | 已完成 |
| `TERMINATED` | 已终止 |
| `UNKNOWN`    | 未知   |

### 主要索引

| 索引  | 字段                | 说明             |
| ----- | ------------------- | ---------------- |
| INDEX | `project_id`        | 查询项目合同     |
| INDEX | `seller_company_id` | 查询企业合同历史 |

------

## 3.25 `report` 报告表

### 表作用

保存系统生成的投标决策报告、竞争分析报告、中标概率报告、周报、月报等结构化报告内容。

### 字段说明

| 字段           | 类型         | 约束                     | 说明            |
| -------------- | ------------ | ------------------------ | --------------- |
| `id`           | INT          | PK, AUTO_INCREMENT       | 报告 ID         |
| `user_id`      | INT          | NOT NULL                 | 用户 ID         |
| `company_id`   | INT          | FK -> `company.id`, NULL | 报告所属企业 ID |
| `project_id`   | INT          | FK -> `project.id`, NULL | 报告关联项目 ID |
| `report_type`  | VARCHAR(64)  | NOT NULL                 | 报告类型        |
| `title`        | VARCHAR(512) | NULL                     | 报告标题        |
| `content_json` | JSON         | NULL                     | 报告结构化内容  |
| `status`       | VARCHAR(32)  | DEFAULT 'GENERATING'     | 报告生成状态    |
| `created_at`   | DATETIME     | NOT NULL                 | 创建时间        |
| `updated_at`   | DATETIME     | NOT NULL                 | 更新时间        |

### 报告类型建议

| 值             | 说明         |
| -------------- | ------------ |
| `BID_DECISION` | 投标决策报告 |
| `COMPETITION`  | 竞争分析报告 |
| `WIN_RATE`     | 中标概率报告 |
| `WEEKLY`       | 周报         |
| `MONTHLY`      | 月报         |

### 报告状态建议

| 值           | 说明     |
| ------------ | -------- |
| `GENERATING` | 生成中   |
| `SUCCESS`    | 生成成功 |
| `FAILED`     | 生成失败 |

### 主要索引

| 索引  | 字段                   | 说明             |
| ----- | ---------------------- | ---------------- |
| INDEX | `user_id, report_type` | 查询用户某类报告 |
| INDEX | `project_id`           | 查询项目相关报告 |
| INDEX | `company_id`           | 查询企业相关报告 |

------

## 3.26 `sys_dict` 系统字典表

### 表作用

集中维护公告类型、项目状态、企业关系类型、推荐等级、反馈类型、报告类型等枚举/字典数据，避免代码中到处写死字符串。

### 字段说明

| 字段         | 类型         | 约束               | 说明     |
| ------------ | ------------ | ------------------ | -------- |
| `id`         | INT          | PK, AUTO_INCREMENT | 字典 ID  |
| `dict_type`  | VARCHAR(64)  | NOT NULL           | 字典类型 |
| `dict_code`  | VARCHAR(64)  | NOT NULL           | 字典编码 |
| `dict_label` | VARCHAR(128) | NOT NULL           | 展示名称 |
| `sort_order` | INT          | DEFAULT 0          | 排序号   |
| `is_enabled` | TINYINT(1)   | DEFAULT 1          | 是否启用 |
| `created_at` | DATETIME     | NOT NULL           | 创建时间 |

### 常见字典类型

| 字典类型            | 说明             |
| ------------------- | ---------------- |
| `NOTICE_TYPE`       | 公告类型         |
| `RELATION_TYPE`     | 项目企业关系类型 |
| `STAGE_TYPE`        | 项目阶段类型     |
| `CRAWL_TASK_STATUS` | 采集任务状态     |
| `PARSE_STATUS`      | 解析状态         |
| `RECOMMEND_LEVEL`   | 推荐等级         |
| `FEEDBACK_TYPE`     | 用户反馈类型     |
| `REPORT_TYPE`       | 报告类型         |

### 主要索引

| 索引   | 字段                   | 说明                   |
| ------ | ---------------------- | ---------------------- |
| UNIQUE | `dict_type, dict_code` | 同一字典类型下编码唯一 |
| INDEX  | `dict_type`            | 查询某类字典           |
| INDEX  | `is_enabled`           | 查询启用字典项         |

------

# 4. 表之间的核心关系

## 4.1 采集到解析链路

```text
数据源 data_source
  -> 采集任务 crawl_task
  -> 原始公告 raw_notice
  -> 原始附件 raw_notice_attachment
  -> 抽取结果 notice_extraction
```

说明：爬虫先从数据源采集公告，保存原文和附件；解析程序再对原文进行字段抽取。

## 4.2 项目事实链路

```text
项目 project
  -> 公告 project_notice
  -> 公告附件 project_notice_attachment
  -> 项目要求 project_requirement
```

说明：一个项目可以有多条公告，每条公告可以有多个附件，也可以抽取出多个项目要求。

## 4.3 企业参与链路

```text
企业 company
  -> 企业别名 company_alias
  -> 项目企业关系 project_company_relation
  -> 联合体成员 project_consortium_member
  -> 项目人员 project_company_person
```

说明：企业通过项目企业关系参与项目，可能作为投标人、候选人、中标人或合同方出现。

## 4.4 企业画像链路

```text
企业 company
  -> 企业资质 company_qualification
  -> 企业人员 company_personnel
  -> 企业画像快照 company_profile_snapshot
```

说明：企业画像由企业基础信息、资质、人员、历史中标项目等综合计算而来。

## 4.5 推荐与分析链路

```text
企业 company + 项目 project
  -> 推荐结果 recommendation_result
  -> 竞争分析 competition_analysis
  -> 中标概率 win_probability_analysis
  -> 用户反馈 user_feedback
  -> 报告 report
```

说明：推荐系统以企业和项目为输入，生成推荐结果、竞争分析和中标概率；用户反馈可以反哺推荐优化。

------

# 5. 常用状态和枚举说明

## 5.1 公告类型 `NOTICE_TYPE`

| code               | label          |
| ------------------ | -------------- |
| `PLAN`             | 招标计划       |
| `PREQUALIFICATION` | 资格预审公告   |
| `TENDER`           | 招标公告       |
| `CANDIDATE`        | 中标候选人公示 |
| `FINAL_CANDIDATE`  | 定标候选人公示 |
| `AWARD`            | 中标结果公告   |
| `CORRECTION`       | 更正公告       |
| `CONTRACT`         | 合同与履约公告 |

## 5.2 项目企业关系 `RELATION_TYPE`

| code         | label      |
| ------------ | ---------- |
| `BIDDER`     | 投标人     |
| `CANDIDATE`  | 候选人     |
| `WINNER`     | 中标人     |
| `CONTRACTOR` | 合同签约方 |

## 5.3 阶段类型 `STAGE_TYPE`

| code               | label          |
| ------------------ | -------------- |
| `PLAN`             | 招标计划阶段   |
| `PREQUALIFICATION` | 资格预审阶段   |
| `TENDER`           | 招标公告阶段   |
| `CANDIDATE`        | 候选人公示阶段 |
| `FINAL_CANDIDATE`  | 定标候选人阶段 |
| `AWARD`            | 中标结果阶段   |
| `CONTRACT`         | 合同履约阶段   |

## 5.4 推荐等级 `RECOMMEND_LEVEL`

| code      | label    |
| --------- | -------- |
| `STRONG`  | 强烈推荐 |
| `NORMAL`  | 推荐关注 |
| `CAUTION` | 谨慎参与 |

## 5.5 竞争强度/威胁等级

| code     | label |
| -------- | ----- |
| `LOW`    | 低    |
| `MEDIUM` | 中    |
| `HIGH`   | 高    |

------

# 6. 使用建议

## 6.1 爬虫入库建议

爬虫采集时建议先写入原始层：

1. `data_source`：记录平台配置。
2. `crawl_task`：记录每次采集任务。
3. `raw_notice`：保存公告原始 HTML 和清洗文本。
4. `raw_notice_attachment`：保存原始附件。

解析完成后再写入事实层：

1. `project`：归并或创建项目。
2. `project_notice`：写入结构化公告。
3. `project_notice_attachment`：写入公告附件。
4. `project_requirement`：写入资格、技术、人员、业绩要求。
5. `project_company_relation`：写入投标人、候选人、中标人等关系。

## 6.2 推荐分析建议

推荐引擎不应直接修改事实表，而应写入结果层：

- 推荐结果写入 `recommendation_result`。
- 竞争分析写入 `competition_analysis`。
- 中标概率写入 `win_probability_analysis`。
- 报告内容写入 `report`。
- 用户反馈写入 `user_feedback`。

## 6.3 字段设计建议

- 高频筛选字段应使用普通列，例如 `province`、`city`、`industry`、`notice_type`。
- 扩展字段可以使用 `JSON`，例如 `structured_data`、`score_breakdown`、`profile_json`。
- 大正文使用 `LONGTEXT`，例如公告正文和原始 HTML。
- 金额统一使用 `DECIMAL(18,2)`。
- 评分和概率统一使用 `DECIMAL(5,2)` 或 `DECIMAL(5,4)`。

------

# 7. 一句话总结

`crawler` 数据库的核心设计是：**先保存原始公告和附件，再抽取结构化项目事实，最后基于企业画像和项目要求生成推荐、竞争分析、中标概率和报告**。这样既能保证爬虫数据可追溯，也能为后续智能推荐和投标决策提供稳定的数据基础。