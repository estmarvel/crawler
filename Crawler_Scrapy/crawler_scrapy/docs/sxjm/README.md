# 山西焦煤电子招采平台爬虫

完整的接口、类型映射、字段解析、快照、附件、限速、续跑和故障处理说明见
[《SXJM 网站爬取实现与运行说明》](./SXJM网站爬取实现与运行说明.md)。

`sxjm` 复用现有 Scrapy 公告框架，默认采集首页全部四个频道，并按频道、栏目分别生成 JSON/CSV：

- `yfxm` 依法项目：招标计划、招标（预审）公告、中标候选人公示、结果公告、终止公告。
- `zbxm` 招标项目：招标（预审）公告、中标候选人公示、中标公告、终止公告。
- `fzxm` 非招项目：采购（预审）公告、成交候选人公示、成交公告、终止公告。
- `jycg` 简易采购限额以下：采购公告、终止公告、成交公告。

接口返回值采用网站前端相同的 AES-128-CBC 方式解密。四个频道分别使用网站接口的 `category=1/3/2/4`，公告类型也按各频道真实编号请求。

公告类型采用双层模型：

- `notice_type` 使用公共框架生命周期 Schema（如采购公告复用 `TENDER` 字段、成交候选人复用 `CANDIDATE` 字段）；
- `notice_subtype` 始终保留真实栏目（如 `fzxm.cjhxr`、`fzxm.cjgg`），数据库导入时分别保存为“成交候选人公示”“成交公告”，不会混成中标候选人或中标结果。

2026-08-04 实际接口验证，依法项目招标栏目使用 `announcement_type=8`，其中同时包含普通招标、二次、延期和变更公告；`category=1&announcement_type=1` 当前 `total=0`，框架不再发送该空请求。

每条 JSON 还包含统一 `_trace` 溯源包：解密后的原始列表记录、原始详情对象、
列表请求参数、分页信息、列表/详情 HTTP 元数据、接口业务状态、完整 HTML、按 DOM
顺序清洗的正文及 SHA-256。CSV 表头不变，也不包含 `_trace`。

该实现不要求修改数据库结构。导入时复用现有 MongoDB
`raw_notices.payload/rawHtml/rawText/responseMetadata` 和
`notice_extractions.evidence`，MySQL 只保存现有索引字段。

SXJM 详情接口 `content` 中的原始公告 HTML 会同时保存为两种可追溯形式：

- 独立文件：`new_output/sxjm/snapshots/<公共Schema类型>/<公告ID>_<SHA256前缀>.html`；
- JSON：`_trace.rawHtml` 保存同一份 HTML，`_trace.integrity.rawHtmlSha256`
  保存完整哈希。

顶层 `HTML快照路径`、`HTML快照SHA256` 与
`_trace.exportMetadata.snapshotPath/snapshotSha256` 保持一致。导入后原始 HTML
对应 MongoDB `raw_notices.rawHtml`，快照定位信息保存在现有
`responseMetadata.trace.exportMetadata`，无需修改数据库结构。若源站个别记录确实
没有 HTML，仍保留 `_trace.payload` 中的解密详情 JSON 并记录告警，不会因为无法
生成 HTML 文件而丢弃整条公告。

## 安装与运行

```bash
pip install -r requirements.txt
scrapy crawl sxjm -a days=30 -a max_records=200
```

正式手工采集建议使用可恢复入口。默认结果写入项目根目录的 `new_output`，
公告与附件完全分为两个阶段，且不连接数据库：

```bash
# 默认：最近 180 天，先公告、后附件
bash run_sxjm.sh

# 两个阶段也可以在不同时间分别运行
bash run_sxjm.sh --phase notices --all
bash run_sxjm.sh --phase attachments
```

公告阶段会先保存 HTML 快照，再导出 JSON/CSV；默认使用并发 2、每批 3~5 秒请求间隔、
自动限速、分批请求预算和批间冷却；一旦
收到 403/429 会立即停爬，不会用高频重试继续冲击源站。它通过
`new_output/sxjm/state/` 下的 JOBDIR、公告版本索引和阶段完成标记恢复：第一次
按 Ctrl-C 后再次执行相同命令，已经正确导出的公告 ID 不会再次请求详情；如果
公告阶段已经正常结束，则直接进入未完成的附件阶段。以后确实需要扫描新公告时
显式加 `--refresh-notices`，需要检查旧公告内容更新时加 `--check-updates`。

附件阶段从 JSON 的 `附件` 清单读取任务，下载到稳定路径
`new_output/sxjm/attachments/<公告类型>/<公告ID>/`。未完成文件使用 `.part`
后缀，下次运行通过 HTTP Range 续传；已完整落盘的附件按确定性路径跳过，不会
重复下载。默认连接超时 30 秒、单次流读取超时 900 秒，并进行指数退避重试。
完成后会同时回写 JSON 主记录、`_trace.exportMetadata.attachments` 和同名 CSV
附件列，确保附件与公告仍按公告 ID 一一对应。

两个阶段都会在终端打印实时进度，并将完整日志写入
`new_output/sxjm/logs/`。这些措施用于降低固定出口 IP 被限流的风险，不能保证
源站永远不会封禁；脚本不使用身份伪造，也不会在明确拒绝后绕过限制。

仅抓其他三个频道：

```bash
scrapy crawl sxjm -a channels=yfxm,fzxm,jycg -a days=30 -a max_records=200
```

仅抓非招项目：

```bash
scrapy crawl sxjm -a channels=fzxm
```

完整历史采集：

```bash
bash run_sxjm.sh --all
```

可通过 `channels`、`sections`、`start_date`、`end_date`、`days`、`page_size`、`max_pages` 和 `max_records` 控制范围。`max_records` 按每个实际接口分别计算。

建议新规则上线前先隔离实抓并执行导入 dry-run：

```bash
scrapy crawl sxjm \
  -a channels=yfxm,zbxm,fzxm,jycg \
  -a max_records=2 -a page_size=5 -a max_pages=2 \
  -s NOTICE_OUTPUT_ROOT=/tmp/sxjm-audit \
  -s FILES_STORE=/tmp/sxjm-audit \
  -s NOTICE_DEDUP_ENABLED=False

cd /home/intsig/crawler_prisma/new_scripts
npm run import:all -- --site=sxjm --output-root=/tmp/sxjm-audit
```

不带 `--commit` 时只验证 JSON、附件和字段映射，不连接或写入数据库。

按每个有效接口类型抓取 5 条后，可运行正文与字段审计：

```bash
python -m crawler_scrapy.sites.sxjm.audit /tmp/sxjm-audit \
  --expected-per-feed 5 \
  --report /tmp/sxjm-audit/audit_report.json
```

审计会验证 16 个有效 feed 的数量、源站类型与公共 Schema 的对应关系、正文与 `_trace.rawText` 一致性、候选人/成交人是否确实出现在正文，以及附件元数据完整性。源站正文为空模板但附件中可能含结果时只给出警告，不会猜测填值。
