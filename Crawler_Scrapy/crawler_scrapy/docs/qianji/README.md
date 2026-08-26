# 千极数采（Qianji）爬取说明

## 采集范围

`qianji` 使用千极数采公开列表和详情接口，覆盖 5 个一级公告类别、13 个实际数据源：

| 一级类别 | 二级项目类型 | 框架公告结构 | 数据库公告编码 |
| --- | --- | --- | --- |
| 招标计划 | 全部 | 招标计划 | `PLAN` |
| 招标公告 | 工程、货物、服务 | 招标公告 | `TENDER` |
| 变更公告 | 工程、货物、服务 | 招标公告 | 一般变更为 `TENDER`；暂停、终止、撤销、流标、废标为 `TERMINATION` |
| 中标候选人公示 | 工程、货物、服务 | 中标候选人公示 | `CANDIDATE` |
| 结果公告 | 工程、货物、服务 | 中标结果公示 | `AWARD` |

栏目 ID 固定维护在 `crawler_scrapy/sites/qianji/config.py`。程序只请求公开接口，不依赖
浏览器渲染或登录状态。

## 爬取流程

1. 列表接口按栏目 ID、页码和每页数量返回公告摘要。
2. Spider 先按日期范围、公告 ID 和历史指纹去重，再请求详情接口。
3. 详情的 `content` 是 Base64 编码 HTML；解析器解码并清洗为正文，同时原样保留 HTML。
4. API 明确字段优先提供项目名称、项目编号、招标人、代理机构、发布日期等值；正文规则
   负责招标编号、金额、时间、范围、资格条件、中标人和联系方式等字段。
5. 13 个数据源按 5 个一级公告类别合并导出 JSON；工程、货物、服务仍保存在
   `公告子类型` 和 `项目类型/行业分类` 中，不丢失分类信息。HTML 快照、原始列表/详情
   payload、响应元数据和 SHA256 一并保存，便于按采集时状态溯源。
6. 公告阶段只登记附件清单；附件阶段独立下载并回写同一公告，附件慢不会阻塞公告翻页。

编号不互相复制：详情接口 `projectCode` 已由多份正文中的“招标项目编号”样本完成语义
验证，因此存在时直接映射为项目编号；即使某一篇正文未重复展示，也保留 API 值并在
`_trace.fieldMeta.qianjiIdentifierExtraction` 中标记为 `detail_api.projectCode` 及
`visibleInBody=false`。招标编号只读取 HTML 正文中“招标编号、采购编号、代理编号”等
明确标签，支持标签和值分处相邻段落。后续关联按“项目编号、招标编号、项目名称”回退。

## GLM-5.2 HTML 辅助提取

千极链采用“结构化 API 优先、HTML 再解析”的分层策略：

1. `projectCode`、招标人、代理机构、发布日期等普通 API 字段直接使用，不让模型重猜；
2. API 的 Base64 `content` 解码成 HTML/纯文本，先执行网站规则；
3. 显式加 `--ai-extract` 后，先按标签、章节和规则值为每个目标字段
   定位 1~2 个带原文偏移的候选窗口；`glm-5.2` 不接收整篇 HTML/正文；
4. 资金来源、建设/项目规模、招标范围、资格要求、质量要求、文件获取/递交方法
   和保证金方式优先使用候选窗口 AI；编号、时间、金额和结果表格优先使用 API/DOM/规则，
   只在缺失、HTML 残留、章节污染或列表错位时升级；
5. 规则与 AI 冲突或证据失败时，只对相关字段扩大到完整章节再调用一次；
   没有冲突时每篇一次，有冲突时最多增加一次批量章节核验；
6. C2 使用 JSON mode 和非思考模式，候选窗口逐行加入 `L001` 等稳定行号；模型只返回
   `window_id/line_start/line_end`，普通短字段再返回逐字原文值，长章节由程序按行切片；
7. 只有首轮与扩展章节 AI 结果一致、证据和本地类型校验全部通过，才允许覆盖非空规则值；
8. 证据按候选窗口实际偏移保存 SHA256 和短预览；名称/价格列表必须数量一致，并来自
   同一窗口和同一原文行范围；
9. 首轮或扩展章节调用超时都不阻塞规则结果落盘；记录标记为
   `FAILED` 或 `PARTIAL` 后可从 payload 快照离线重试，不重新
   请求源站，也不连接数据库。

当前使用智谱开放平台的 OpenAI 兼容接口：模型代码为 `glm-5.2`，Base URL 为
`https://open.bigmodel.cn/api/paas/v4`。结构化字段提取默认关闭深度思考和随机采样，
输出上限为 1200 tokens；长字段按行切片，不会因该上限丢失正文。响应同时记录上下文
缓存命中的 token 数，以便继续优化成本和延迟。

密钥只放项目根目录的本机 `.env`，不写源码、命令行或日志；`.env` 已被
`.gitignore` 排除。可以复制模板后在编辑器中填写：

```bash
cd /home/intsig/Crawler_Scrapy
cp -n .env.example .env
chmod 600 .env
```

`.env` 内容：

```dotenv
ZHIPUAI_API_KEY=在这里填写智谱开放平台密钥
```

首次只测试“工程/招标公告”3 条，最多 6 次调用（每条首轮一次，冲突时最多
再复核一次），不下载附件、不写数据库：

```bash
./run_qianji.sh --phase notices --days 30 --refresh-notices \
  --output-root new_output/qianji_glm52_smoke \
  --sections tender --project-types engineering \
  --max-records 3 --page-size 3 --max-pages 1 \
  --ai-extract --ai-model glm-5.2 --ai-max-calls 6
```

历史 JSON 的编号只读复核属于已完成的验收流程；正式采集统一通过
`./run_ai_full_crawl.sh` 调度，不再保留独立的离线审核入口，避免误把测试任务
当成生产采集任务。

只重试 JSON 中 `FAILED/PARTIAL` 的 AI 记录（不访问源站）：

```bash
python -m crawler_scrapy.sites.qianji.retry_failed_ai \
  --output-root new_output/qianji_ai_validation_30_v4 \
  --workers 1 --timeout 300 --min-interval 3 --model glm-5.2
```

仅用最新规则修复、规范化已有 JSON，不调用 AI：

```bash
python -m crawler_scrapy.sites.qianji.retry_failed_ai \
  --output-root new_output/qianji_ai_validation_30_v4 --normalize-only
```

## 运行命令

在项目目录运行：

```bash
cd /home/intsig/Crawler_Scrapy

# 默认最近 180 天：公告、快照完成后再下载附件
./run_qianji.sh

# 全部历史
./run_qianji.sh --all

# 只采公告和快照，不下载附件
./run_qianji.sh --phase notices --days 30

# 只下载已登记但尚未完成的附件
./run_qianji.sh --phase attachments
```

选择公告和项目类型：

```bash
# 招标、候选、结果；仅工程项目
./run_qianji.sh --phase notices \
  --sections tender,candidate,award \
  --project-types engineering

# 每个实际数据源最多 5 条，用于字段验证
./run_qianji.sh --phase notices --all \
  --sections plan,tender,change,candidate,award \
  --project-types engineering,goods,service \
  --max-records 5 --page-size 5 --max-pages 2
```

`--max-records` 是每个实际数据源的上限。因此上面的验证命令会得到：招标计划 5 条，
其余 4 个一级类别分别为工程、货物、服务各 5 条，共 65 条。

统一入口默认直连、并发 2、请求间隔 3～5 秒、每 400 个响应冷却 180～300 秒，并启用
AutoThrottle 和 403/429 主动停止保护。所有选项可通过 `./run_qianji.sh --help` 查看。

## 输出和续跑

正式任务结果默认位于 `output/qianji/`：

- `json/`：13 个数据源合并为 5 个一级公告类别文件；正式运行不创建 CSV：
  `千极链_招标计划`、`千极链_招标公告`、`千极链_变更公告`、
  `千极链_中标候选人公示`、`千极链_结果公示`；
- `snapshots/`：按公告类型保存的详情 HTML 快照；
- `attachments/`：附件原文件；
- `state/notice_versions.json`：跨运行公告版本去重；
- `state/jobs/`、`state/runner/`：Scrapy 调度和分批续跑状态；
- `logs/`：实时运行日志。

手动停止后，用完全相同的范围参数再次执行即可续跑。已成功导出的相同内容不会重复保存；
源公告正文改变时会追加新版本，不覆盖旧版本。附件使用 `.part` 临时文件和 HTTP Range
续传，默认连接超时 30 秒、读取超时 900 秒。

## 已验证的模板差异

- 支持 `2026年08 月28 日10时00分`、`上午9时00分` 等日期排版；
- 支持“中 标 人”以及中标人、价格分处不同表格行的结果模板；
- 支持“1、招标人信息 / 名 称”分节式联系方式；
- 支持“招标控制价总价”，并保留控制价、延期、变更、终止等源站语义；
- 对正文没有出现的业务字段保持空值，不根据标题或相邻公告猜造。

离线测试：

```bash
python -m pytest -q tests/test_qianji_spider.py tests/test_site_runner.py
```
