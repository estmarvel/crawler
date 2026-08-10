# 山西省公共资源交易平台工程建设公告适配说明

五站统一框架、公共字段和运行机制见
[《五站爬虫详细实现说明》](../五站爬虫详细实现说明.md)。本文重点记录SXZWFW特有的
栏目、HTML还原和附件逻辑。

## 实现范围

Spider 名称为 `sxzwfw`。默认仍只采集工程建设六个栏目；另外已经按独立字段解析器接入
政府采购更正公告和结果公告。土地矿权、产权交易等其他业务模块尚未接入。

| 参数 | channelId | 源站栏目 |
| --- | ---: | --- |
| `zbjh` | 198 | 招标计划 |
| `zbgg_zys` | 12 | 招标/资审公告 |
| `bg` | 13 | 更正公告 |
| `hxr` | 14 | 中标候选人公示 |
| `gs` | 15 | 中标结果公示 |
| `qt` | 16 | 其他公告 |

政府采购可选栏目：

| 参数 | channelId | 源站栏目 | 保存类型 |
| --- | ---: | --- | --- |
| `zc_gz` | 19 | 更正公告 | 更正结果公示 |
| `zc_jg` | 20 | 中标结果公告 | 中标结果公示 |

采购公告 `channelId=18` 暂不采集，预留请求和字段方案见
[`政府采购/README.md`](政府采购/README.md)。

列表页是服务端渲染 HTML。Spider 按前端真实表单向
`/queryContent-jyxx.jspx` 发起 POST；第二页起使用
`/queryContent_2-jyxx.jspx`、`/queryContent_3-jyxx.jspx`。请求参数为
`title/channelId/inDates/beginTime/endTime/origin/ext`。历史采集优先使用精确起止日期，
并按自然月拆成较小查询窗口，避免单次查询页数过多。

详情页直接请求列表中的 `.jhtml` 地址，不依赖登录 Token，不使用浏览器渲染，也没有
臆造 JSON API。标题、发布时间、信息来源和 `.cs_xq_content` 均从服务端 HTML 获取。

## 正文与字段提取

部分详情由 PDF 转换器生成，同一视觉行会拆成多个 `span`。解析器先按 `.stl_01`
重新拼接同一视觉行，再删除 `display:none`、`visibility:hidden`、签名和盖章占位，
从而避免“获取方式”被拆字以及隐藏签章污染联系方式。普通富文本详情按块级标签和
`br` 保留换行。

字段解析采用规则优先：

- 标题关键词优先确定八类 Schema，标题不规范时用 `channelId` 栏目兜底；
- 项目名称删除“招标公告、中标候选人公示、中标结果公示”等公告后缀；
- 项目性质和组织形式只有正文明确给出才保存，不固定推断；
- 项目地点保存为“正文地点|列表交易场所”，相同值不重复；
- 招标人和代理机构都从正文最后一个真正的“联系方式”章节提取，分别限定角色边界；
- PDF 物理换行只在字段解析时按下一标签/下一编号合并，原始正文不被改写；
- 项目编号兼容“项目编号、招标编号、招标项目编号、标段编号”；控制价和财政审定金额
  优先取标签后的总额，括号内分标段金额不会覆盖总额；
- 兼容“（1）获取时间、电子招标文件获取方式、建设资金为/项目资金来源由”等源站
  写法，中文“年月日时分”会规范化后再写入时间字段；
- 候选人—报价、中标人—中标价先构造成逐条明细，缺少某条报价时保留 `null`，不会
  让后续价格向前错位；兼容“第1名：公司，投标报价：金额”、中标人和中标价同一行，
  以及中标价格标签和值分行的写法；
- 更正、终止、延期、答疑、控制价等细分类别写入“源站公告性质”。中标结果更正映射
  到“更正结果公示”；终止、废标、流标、招标失败和撤销使用 `zzgg` 子类型，业务字段
  复用招标公告 Schema，但导出编码为数据库统一使用的 `TERMINATION`。其他公告标题
  未写“废标”时，正文明确出现“有效投标人不足三家”也按终止类识别；

工程建设公告子类型使用 `engineering.<源栏目>.<Schema子类型>`，例如：

- `engineering.zbgg_zys.zbgg`：源站“招标/资审公告”，Schema 为招标公告；
- `engineering.bg.zbgg`：源站“更正公告”，内容仍是招标公告的变更；
- `engineering.qt.zzgg`：源站“其他公告”，实际为终止/废标类；
- `engineering.gs.zbjg`：源站“中标结果公示”。

这样既能按最后一段直接映射现有数据库公告类型，又不会丢失源站六种信息类型。

默认保存详情 HTML 快照用于核验。正文中的直接文件链接会进入附件清单；如果页面调用
`Cms.attachment(...)`，Spider 会先请求 `/attachment_url.jspx` 解析真实后缀，再按
前端规则生成 `/attachment.jspx?cid=...&i=...` 下载地址。正式入口把公告和文件下载
分成两个阶段，避免大附件阻塞公告采集；附件只下载归档，不做 OCR。

## 运行方式

先进入项目目录：

```bash
cd /home/intsig/Crawler_Scrapy
```

新框架正式入口默认采集最近 180 天，并在公告完成后独立下载附件：

```bash
./run_sxzwfw.sh
```

六种工程建设信息类型各取最多 5 条，只采公告、正文、快照和附件清单：

```bash
./run_sxzwfw.sh --phase notices --days 365 --max-records 5
```

指定精确区间：

```bash
./run_sxzwfw.sh \
  --start-date 2026-01-01 --end-date 2026-08-04
```

只运行附件阶段：

```bash
./run_sxzwfw.sh --phase attachments
```

只抓公告、候选人和结果：

```bash
./run_sxzwfw.sh --phase notices --days 30 \
  --sections zbgg_zys,hxr,gs
```

审计分类、正文、字段值、快照 SHA256 和附件清单：

```bash
python -m crawler_scrapy.sites.sxzwfw.audit new_output \
  --report new_output/sxzwfw/audit_report.json
```

入口默认使用已经实抓验证的服务器直连，并默认总并发/单域名并发为 2、每批 3~5 秒间隔、
AutoThrottle、403/429 首次即停及不自动重试。它不会读取环境代理，也不会使用浏览器。
如需指定 Python 环境：

```bash
export CRAWLER_PYTHON_COMMAND=/home/vipuser/miniconda3/envs/myenv/bin/python
```

输出采用框架现有的追加和版本去重逻辑：

- JSON：`new_output/sxzwfw/json/`
- CSV：`new_output/sxzwfw/csv/`
- HTML 快照：`new_output/sxzwfw/snapshots/`
- 附件：`new_output/sxzwfw/attachments/`
- 去重、JOBDIR 和续跑状态：`new_output/sxzwfw/state/`
- 日志：`new_output/sxzwfw/logs/`

默认严格跳过已经成功导出的公告 ID；正文发生变化需要主动检查时使用
`--check-updates`。相同日期窗口中断后重跑同一命令，会复用对应 `JOBDIR` 和持久化
去重索引；已经导出的记录不重复，尚未完成的详情可以重新获取。附件每完成一个就同步
回 JSON/CSV，已有完整文件直接跳过，`.part` 文件继续使用 HTTP Range 下载。

每条 JSON 的 `_trace` 还会保存当前列表记录、列表 POST 表单、日期窗口、页码、总数、
列表 HTML 的字节数与 SHA256、详情响应元数据，以及 CMS 附件元数据响应。完整详情 HTML
仍由快照和 `_trace.rawHtml` 保存，正文由 `_trace.rawText` 保存；这些诊断内容不增加或修改
数据库字段，导入时写入现有 MongoDB 溯源字段。

## 离线验证

不请求网站、不消耗代理的完整站点测试：

```bash
/home/vipuser/miniconda3/bin/conda run -n myenv \
  python -m pytest tests/test_sxzwfw_parser.py tests/test_sxzwfw_spider.py \
    tests/test_sxzwfw_exporter.py tests/test_sxzwfw_attachment_downloader.py -q
```

规则提取无法确定的字段保持空值；可在人工抽样确认后选择性启用框架 AI 补空接口，AI
不会覆盖已有规则值。批量历史采集脚本默认关闭 AI，避免不受控的模型调用和费用。
