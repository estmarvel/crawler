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
5. JSON/CSV 按 13 个数据源分别导出；HTML 快照、原始列表/详情 payload、响应元数据和
   SHA256 一并保存，便于按采集时状态溯源。
6. 公告阶段只登记附件清单；附件阶段独立下载并回写同一公告，附件慢不会阻塞公告翻页。

编号不互相复制：正文中明确的项目编号优先，其次使用详情接口 `projectCode`；招标编号只
读取“招标编号、采购编号、代理编号”等明确标签。后续关联可按“项目编号、招标编号、
项目名称”依次回退。

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

结果位于 `new_output/qianji/`：

- `json/`、`csv/`：13 个数据源的统一 Schema 结果；
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
