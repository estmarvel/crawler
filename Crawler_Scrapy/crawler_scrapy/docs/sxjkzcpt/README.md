# 山西交控招投标采购服务平台爬取说明

## 1. 接入范围与访问边界

目标站点：`https://www.sxjkzcpt.com.cn/pub/jyxx_pages.html?menuCode=zbcg`

本 Spider 仅采集页面前两栏公开交易信息：

| 频道参数 | 页面栏目 | 源公告类型 |
| --- | --- | --- |
| `zbcg` | 依法必须招标项目 | 采购计划、招标公告、变更公告、中标候选人公示、结果公告、合同订立信息 |
| `qzbcg` | 其他必须招标项目 | 招标公告、变更公告、中标候选人公示、结果公告 |

入口、列表和本次抽查的 44 个详情均不要求登录或 CA。权限不是按整个频道固定，
而是由每个详情页的 `isNbgg` 标志决定：

- `isNbgg=false`：公开详情，可采集正文和公开附件元数据；
- `isNbgg=true`：页面脚本会调用本机 CA 服务并校验机构归属，Spider 记录统计后跳过，
  不尝试绕过登录或 CA；
- 公告正文可能提示投标人登录交易系统、注册并办理 CA 才能获取招标文件或参与投标。
  这属于交易操作权限，不代表当前公告正文不公开。本爬虫不登录交易系统，也不采集
  账户内文件。

`NBCG`、`FZBCG` 等其他频道不在本次范围内。

## 2. 网站接口与会话

该站不是 JSON API，而是带 CSRF 和会话 Cookie 的服务端 HTML 接口：

1. GET `/pub/jyxx_pages.html?menuCode=zbcg` 建立 `JSESSIONID` 并读取隐藏 `_csrf`；
2. POST `/pub/JYXX_pages.htm`，提交 `menuCode`、`typeCode`、`page`、
   `pageSize`、`keyName` 和 `_csrf` 获取列表片段；
3. 从列表 `.erjizt-right-cont-dt` 读取 UUID、标题和发布日期；
4. POST `/pub/detail_pages.htm`，提交 `info=<UUID>` 和 `_csrf` 获取完整详情；
5. 对外保存的详情地址使用可打开的 GET 包装页
   `/pub/detail_pages.html?info=<UUID>`，实际 POST 请求地址保留在 `_trace`。

CSRF 和 Cookie 不写入 JSON、日志或快照元数据。

## 3. 类型纠正

源站存在混栏，不能直接把列表栏目当成最终公告类型。例如本次样本中：

- `qzbcg.award` 的 5 条实际为 2 条结果、1 条候选人、2 条终止/流标；
- `qzbcg.candidate` 中有流标公告；
- `zbcg.award` 中有候选人公示；
- `zbcg.candidate` 中有候选人公示更正。

解析器根据详情标题纠正为 `plan/tender/change/candidate/award/correction/termination/contract`，
同时在 `_trace.fieldMeta.source_feed` 保留原频道和原栏目。终止、流标、废标、撤销公告
使用招标公告字段形状，数据库公告编码为 `TERMINATION`。

## 4. 字段抽取

优先级为“详情顶部结构化表格 > 正文明确标签 > 章节规则”。主要规则如下：

- 项目编号：投资项目统一代码、招标项目编号、采购项目编号、项目代码或明确项目编号；
- 招标编号：优先正文明确的招标编号、采购编号、代理编号；正文未披露时才用顶部
  “交控集团招采认证编号”兜底，原认证编号始终保留在 `_trace`；
- 项目名称、采购方式、招采类型、文件获取时间、开标时间、公示时间：顶部表格；
- 项目规模、范围、资格要求、资金来源、工期、质量、保证金、递交方式：正文标题或标签，
  并兼容“建设资金来自”“建设地址”“设计服务期限”“服务周期”“质量/技术标准”等模板；
- 招标人和代理机构：正文联系方式分块提取；
- 候选人与报价：优先解析同一张候选人报价表；若表格只有名称而正文明确披露
  “中标候选人1 + 投标报价”或下一行“中标价/中标金额”，以正文补齐一一对应的报价，
  同时停止跨表拼接，避免把项目负责人、工期、响应情况误当报价；
- 中标人与金额：优先顶部成交/中标结构化字段，再使用正文结果段落；
- 采购计划：读取正文计划表中的投资统一代码、投资额、建设地点、建设规模和预计发布时间。

项目编号和招标编号始终分别保存；组合字段按“项目编号；招标编号”生成。没有明确来源
时保留空值，不根据标题臆造编号。

## 5. 快照、溯源与附件

每条公告保存：

- 统一 JSON 和 CSV；
- POST 详情响应的原始 HTML 快照及 SHA256；
- 清洗后的公告正文；
- 原始列表记录、详情结构化表格、访问判定、原始频道；
- 请求/响应状态、重试次数和下载延迟；
- 内容指纹、解析器版本和字段缺失列表。

公开详情中 `downloadFile(fileId)` 只在公告阶段保存附件元数据。附件阶段重新建立站点会话，
先 POST `/pub/checkFile/<fileId>`，再 POST `/fileInfo/downloadFile/<fileId>`。下载使用独立的
连接/读取超时、重试和原子 `.part` 文件，不阻塞公告采集。源站下载接口未声明支持 Range，
因此失败后从头覆盖 `.part`，不会错误追加完整响应。

## 6. 运行命令

在项目根目录执行：

```bash
# 最近 180 天：先公告、后附件
./run_sxjkzcpt.sh --phase all

# 全历史，两栏全部源类型
./run_sxjkzcpt.sh --phase all --all

# 只采集第二栏公开公告
./run_sxjkzcpt.sh --phase all --channels qzbcg

# 只采公告或只下载已有 JSON 中的附件
./run_sxjkzcpt.sh --phase notices --channels zbcg,qzbcg
./run_sxjkzcpt.sh --phase attachments

# 每个源栏目最近 5 条的安全抽样
./run_sxjkzcpt.sh --phase notices --all --max-records 5 --page-size 5

# 每个源栏目跨历史页随机 5 条；相同 seed 可复现同一批样本
./run_sxjkzcpt.sh --phase notices --all --max-records 5 --page-size 100 \
  --sample-mode random --sample-seed 20260954
```

默认直连、并发 2、每批随机请求间隔 3～5 秒、每 400 个响应冷却 180～300 秒。
`--phase all` 表示先完成公告和快照，再执行附件下载；不是无限循环。建议在 tmux 中运行。

若解析规则升级，可完全离线地用已保存 HTML 重算测试结果：

```bash
python -m crawler_scrapy.sites.sxjkzcpt.reparse_output --output-root new_output
```

重算命令会获得站点锁、原子更新 JSON/CSV，不请求网站，也不修改数据库。

## 7. 2026-08-06 抽样结果

- 使用 `--sample-mode random --sample-seed 20260954` 跨历史页抽样；
- 源请求共 74 个，全部 HTTP 200；导出 44 条；没有 403、429、重试或 CA 拦截；
- 依法栏采购计划全站仅 4 条，已全部测试；合同订立信息当前 0 条；
- 其余 8 个非空源栏目均请求 5 条；
- 44 条详情均为 `isNbgg=false`；样本详情没有 `downloadFile(fileId)` 公开附件；
- HTML 快照 44 份，均可按 JSON 路径定位并通过 SHA256 校验；
- 根据正文逐条对比修复了候选人跨表重复、同行/次行报价遗漏、联合体牵头人与成员、
  资金来源括号误提取、建设地址、服务期限、质量标准、评标办法和两类编号混用问题，
  解析版本为 `sxjkzcpt-v5-random-field-audit`。

正式测试数据位于 `new_output/sxjkzcpt`；本轮可复现随机样本位于
`new_output_random_20260954_fixed/sxjkzcpt`。两者均不写数据库。
