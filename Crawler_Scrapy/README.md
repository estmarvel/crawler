# Crawler_Scrapy

公共 Scrapy 框架当前包含统一公告 Schema、HTML 快照和 CSV/JSON 输出，已适配华新、
玖邦，以及山西省公共资源交易平台工程建设公告和政府采购更正/结果公告。

## 运行环境

```bash
conda activate myenv
```

如需重建环境，可使用 `requirements.txt` 安装框架的最小依赖。

## 华新离线测试

```bash
python3 -m unittest tests.test_huaxin_parser -v
```

## 山西省公共资源交易平台

该站使用服务端 HTML 列表和详情页，按前端真实 `channelId`、精确日期窗口动态翻页；
不使用浏览器渲染或不存在的 JSON 详情 API。PDF 转换型 HTML 会按视觉行重建正文，
并支持 CMS 附件元数据解析。实现、栏目映射、字段策略和运行场景见
[`crawler_scrapy/docs/sxzwfw/README.md`](crawler_scrapy/docs/sxzwfw/README.md)。

```bash
cd /home/intsig/Crawler_Scrapy
./run_sxzwfw.sh --days 1 --max-records 5
./run_sxzwfw.sh --days 180
```

工程建设六种信息类型每种 5 条的隔离测试：

```bash
./run_sxzwfw.sh --phase notices --max-records 5
```

政府采购更正、结果公告每种 5 条的隔离测试：

```bash
./run_sxzwfw.sh --phase notices --sections zc_gz,zc_jg --max-records 5
```

## 五站统一运行入口

每个网站只保留一个入口；同一命令默认先采公告/快照，再独立下载附件：

完整参数、运行场景、续跑和问题排查见
[`crawler_scrapy/docs/五站统一运行说明.md`](crawler_scrapy/docs/五站统一运行说明.md)。

```bash
cd /home/intsig/Crawler_Scrapy
./run_sxjm.sh --days 180
./run_sxzwfw.sh --days 180
./run_bitbid.sh --days 180
./run_huaxin.sh --days 180
./run_jiubang.sh --days 180
```

补采源站现存的全部历史公告时使用 `--all`，例如：

```bash
./run_huaxin.sh --all
```

也可以给出精确日期、栏目和阶段：

```bash
./run_huaxin.sh --start-date 2026-01-16 --end-date 2026-07-16
./run_huaxin.sh --phase notices --days 30 --sections zbgg_zys,hxr
./run_huaxin.sh --phase attachments
```

可采集栏目为 `zbgg_zys`（招标/资审）、`hxr`（候选公示）、`gs`（结果公示）和
`zbjh`（招标计划），可通过 `--sections` 传入其逗号分隔子集。每个栏目从最新页开始，
按发布日期倒序翻页；到达开始日期后立即停止。如果网站该栏目全部公告仍不足所需时间，
则在最后一页自然停止，不会空翻后续页面。列表日期缺失时仍请求详情，并在详情合并后再
校验日期，避免因源站偶发缺字段而漏采。

每次入口只运行一个网站，避免日志、锁和出口策略互相干扰。默认总并发/单域名并发为 2，
每批从 3~5 秒中选择请求间隔，并启用 AutoThrottle；每 400 个响应冷却 180~300 秒。
收到首次连续 403/429 即主动终止该站任务，不会继续冲击服务器出口 IP。

脚本启用跨运行去重和追加输出，同一公告相同内容不会重复保存；同一公告正文发生变化时
会追加新版本，保留旧版本和各自的“爬虫时间”。同一时间窗口中断后再次执行相同命令，
会从 `new_output/<网站代码>/state/jobs/` 中恢复；不同时间窗口使用相互隔离的恢复目录。

输出位置：

- 华新 JSON/CSV/附件：`new_output/huaxin/`
- 玖邦 JSON/CSV/附件：`new_output/jiubang/`
- 单站实时日志：`new_output/<网站代码>/logs/`

固定代理地址允许由环境变量覆盖；认证信息只从 `HUAXIN_PROXY_USERNAME`、
`HUAXIN_PROXY_PASSWORD` 读取，不写入源码或命令行。代理更换后应更新部署环境。

当前公开列表、详情和文件元数据 API 不发送登录 Token。详情页 JS 只根据顶层
`fileId` 展示附件，`bidAnnouncementSectionDOS` 不是页面附件来源。`pdfFile` 是
后台 PDF 版本：HTML 正文存在时不重复下载；没有 `annContent/annContent2` 的
PDF-only 详情则作为详情原件下载归档。
即使 `fileName` 为空也会保留 `fileId`，再通过前端实际使用的
`/bidding/file/query/{fileId}` 补充名称和预览 URL。华新 Spider 会下载二进制
原文件，但不做 OCR/AI 文档解析；文件保存在
`new_output/huaxin/attachments/<公告类型>/<公告ID>/`。导出结果的附件字段保留
`source_file_id`、`file_name`、`file_url`、`storage_path`、`file_hash`、
`file_size_bytes`、`file_type`、`parse_status`，其中 `storage_path` 相对于
`FILES_STORE`。同一源站文件 ID 使用稳定路径，已下载文件直接复用，不覆盖历史
CSV/JSON。华新已关闭HTML快照保存，但附件下载保持开启。

天启配置仍完整保留为手动备用模式。启用时需注入 `TIANQI_SECRET`、`TIANQI_SIGN`，
并增加 `-s CRAWLER_OUTBOUND_MODE=tianqi`。代理默认每次提取 10 个、有效期 3 分钟、
HTTPS 类型；生产最多调用代理 API 5 次，测试可限制为 1 次。天启模式无可用代理时
立即终止，绝不回退服务器直连。

## 玖邦招投标适配器

玖邦与华新使用同一套 TWS 招投标前端。框架中的 `jiubang` Spider 复用华新已验证
的公告分类、字段提取、`annNature`、去重追加和附件处理规则，仅使用玖邦自己的
API、详情页域名和 `new_output/jiubang/` 输出空间。当前只采集招投标模块，不会混入
独立采购、竞价或零散采购数据。前端分析记录见
`crawler_scrapy/docs/jiubang/analysis.md`。

离线校验命令：

```bash
python -m unittest tests.test_jiubang_spider -v
```

玖邦使用 `./run_jiubang.sh` 运行。结果追加到 `new_output/jiubang/json/` 和
`new_output/jiubang/csv/`，附件保存在 `new_output/jiubang/attachments/`，不会覆盖历史版本。

## AI 补充 HTML 缺失字段

框架已接入可选的公共 AI Pipeline，默认关闭，因此普通爬取和离线测试不会调用
AI API。处理顺序为：网站规则解析、八类 Schema 规范化、AI 仅补空字段、再次按
Schema 转换字段类型、最后导出。AI 不覆盖规则已经提取到的值，也不会提取爬虫时间、
快照路径、详情链接、发布日期、发布网站等框架/请求元数据。

启用前只在当前终端或部署系统中注入密钥，不要写入源码：

```bash
export DMX_API_KEY='...'
scrapy crawl huaxin -a max_records=5 \
  -s NOTICE_AI_ENABLED=True \
  -s NOTICE_AI_MAX_CALLS=5
```

默认使用 OpenAI 兼容地址 `https://vip.dmxapi.com/v1` 和模型
`glm-4.6-thinking`，可分别通过 `NOTICE_AI_BASE_URL`、`NOTICE_AI_MODEL`
覆盖。超长正文会同时保留开头和结尾；同步 SDK 调用、限速等待和失败重试均在线程池
执行，不会阻塞 Scrapy 下载器。调用次数、Token 和填充字段数写入 Scrapy Stats，
每条公告的模型、目标字段和实际补充字段记录在 `field_meta.ai_extraction`。

AI 服务本身不固定字段，调用方可以为任意网站、任意公告类型传入目标字段：

```python
result = service.extract(
    notice_type="招标公告",
    title="某项目招标公告",
    fields=["项目编号/招标编号", "招标金额", "开标时间"],
    text=html_or_text,
)
```

具体 Spider 可以通过 `ai_extract_fields` 映射为不同公告类型选择不同字段；不配置
时，框架默认选择该类型仍缺失的业务字段。也可以重写
`select_ai_extract_fields()`，根据网站、公告类型和页面内容动态选择。

华新已经配置网站级字段映射：招标计划不调用 AI；结构化 API 已返回的项目名称、
编号、日期、行业、中标人和中标价等字段也不调用 AI；只有规则解析后仍为空、且可能
位于 `annContent/annContent2` 的金额、范围、资格要求、候选人、工期、项目经理和
联系方式等字段才会提交。`NOTICE_AI_ENABLED` 仍默认关闭，避免批量历史采集意外产生
模型费用。

## 跨运行去重与历史版本

默认启用 `NOTICE_DEDUP_ENABLED=True`。框架使用以下两层判断：

1. `平台代码 + 源站公告ID` 作为公告身份；没有ID时依次回退详情URL、类型/标题/时间。
2. 正文或HTML的 SHA256 作为内容版本。

索引保存在 `output/<网站代码>/state/notice_versions.json`，不是数据库。列表记录与
上次一致时，支持在发出详情请求前跳过；浏览次数等非业务变化不会触发重新请求详情；
同一内容版本会在附件和 AI 处理前丢弃；同一公告内容变化时，会在原 CSV 和 JSON 中
追加一条新版本，旧版本不会清空或覆盖。每条导出记录都保留独立的“爬虫时间”。
需要完全禁止已知公告ID再次请求时，可启用
`NOTICE_DEDUP_SKIP_KNOWN_IDENTITIES=True`；此模式不会检查同一ID后续是否修改。

CSV 以追加模式写入；JSON 保持标准数组格式，每次只在数组结束符前追加新对象。
第一次启用去重时会从已有 JSON 结果建立索引。为避免跨进程同时追加造成文件冲突，
同一网站同一时间只运行一个 Scrapy 进程。

### JSON 溯源包

默认启用 `NOTICE_EXPORT_TRACE=True`。公告原有公共字段和各公告类型业务字段保持不变，
CSV 表头也不变；每条 JSON 记录额外增加 `_trace`，用于按采集时状态复盘：

- `payload`：列表接口和详情接口返回的原始业务数据；
- `rawHtml`、`rawText`：解析规则实际面对的原始页面/正文；
- `responseMetadata`：请求 URL、方法、响应 URL、状态码、编码、公开响应头、延迟、重试和重定向；
- `crawlerVersion`、`extractionModel`、`extractionVersion`、`fieldMeta`：当时使用的规则/模型和字段级处理记录；
- `capturedAt`、`integrity`：采集时刻以及 payload、HTML、正文的 SHA256，便于判断数据是否被改动。

溯源元数据明确不保存 Cookie、Authorization、代理认证和 API 密钥。`_trace` 会由
`crawler_prisma/new_scripts` 映射到 MongoDB 的 `raw_notices`、`notice_extractions`；
MySQL 只保留查询索引和文档 ID，MinIO 继续只保存附件二进制。

## 服务器直连保护与代理备用模式

SXJM、SXZWFW、Bitbid 默认使用 `direct`；Huaxin、Jiubang 默认使用 `static` 固定代理。
统一入口启用并发 2、3~5 秒批次间隔、AutoThrottle 和
`DirectAccessGuardMiddleware`。第一次连续出现 403/429 就主动关闭 Spider。

固定代理凭据只从环境读取；交互终端缺少凭据时会安全提示输入：

```bash
export HUAXIN_PROXY_USERNAME='...'
export HUAXIN_PROXY_PASSWORD='...'
./run_huaxin.sh --outbound-mode static --days 1
```
