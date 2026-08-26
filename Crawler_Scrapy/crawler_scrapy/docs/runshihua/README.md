# 润世和电子招投标交易平台爬取说明

## 采集范围

公开入口：<https://ec.runshihua.com/web/home>

站点代码为 `runshihua`。爬虫只访问无需账号、登录或 CA 的公开公告接口和公开 PDF，
不访问投标报名、招标文件购买、投标文件递交等交易功能。

| 参数 | 源站公告类型 | 统一公告类型 |
|---|---|---|
| `prequalification` | 资格预审公告 | `PREQUALIFICATION` |
| `tender` | 招标公告 | `TENDER` |
| `purchase` | 采购公告 | `TENDER` |
| `prequalification_change` | 资格预审变更公告 | `CORRECTION` |
| `tender_change` | 招标变更公告 | `CORRECTION` |
| `purchase_change` | 采购变更公告 | `CORRECTION` |
| `candidate` | 中标候选人公示 | `CANDIDATE` |
| `award` | 中标结果公示 | `AWARD` |
| `candidate_correction` | 中标候选人公示更正 | `CORRECTION` |
| `award_correction` | 中标结果公示更正 | `CORRECTION` |
| `control_price` | 控制价公告 | `CORRECTION` |
| `control_price_change` | 控制价变更公告 | `CORRECTION` |
| `cancellation` | 撤销公告 | `CORRECTION` |
| `supplement` | 补充公告 | `CORRECTION` |
| `delay` | 延期公告 | `CORRECTION` |

源站三组列表的公告编号可能重复，所以去重键使用 `接口族:源站ID`，例如
`notice:123`、`candidate:123`，不会错误地把不同公告合并。

## 实现方式

页面是单页应用，真实数据来自三个公开 JSON 接口族：

- 普通公告：`/spi/cms/cmsNotice/getNoticeList` 和 `getNotice`；
- 候选人/结果公示：`/spi/cms/candidate/cmsList` 和 `queryCandidate`；
- 控制价、撤销、补充、延期：`/spi/cms/cancellation/getNoticeList` 和 `getNotice`。

请求携带公开平台代码 `100001`。列表按源站时间倒序分页，先按日期范围、公告类别和唯一
编号过滤，再请求详情。列表接口单页最多支持 400 条；统一运行器默认使用 100 条，避免一次
重试重复传输过多数据。运行器默认每批最多处理 400 个 HTTP 响应，然后冷却 180～300 秒。

字段提取按“接口结构字段优先、HTML/PDF 正文补充”的顺序执行：

1. 项目名称、项目编号、招标编号、时间、地点、资金来源、招标人和代理机构优先读取详情
   接口的独立字段；
2. 招标范围、资格要求读取标段映射，并保留标段与内容的对应关系；
3. 候选人、中标人及报价从正文表格或正文句式提取，解析阶段保持企业与报价成对；
4. 接口没有 HTML 正文时，根据真实结构字段生成可检索正文，不填造源站没有的字段；
5. 项目编号与招标编号分别提取。前者优先 `tenderingCode/招标项目编号/项目编号`，后者
   优先 `noticeNumber/candidateNumber/招标编号`，不将两个编号混为同一个值。

公告 PDF 作为附件登记并在附件阶段独立下载，来源通常为
`https://file.runshihua.com/files/c/...`。公告阶段不会被大 PDF 阻塞；附件通过
`sourceNoticeId + source_file_id` 与公告关联，可断点续传和跳过已完成文件。附件下载后校验
HTTP 状态、PDF 文件头和文件哈希。下载阶段结束后，会使用 `pdftotext -layout` 离线读取
PDF 文字层，并用列表、详情 payload 和 HTML 快照重新计算字段，再原子更新 JSON/CSV。没有
有效文字层时保留 `DOWNLOADED_NO_OCR` 状态且不伪造内容，后续可对该文件单独安排 OCR。

## 快照与溯源

每条公告保存以下溯源信息：

- 列表记录和详情结构字段的 payload 快照及 SHA256；
- 原始 HTML 正文快照及 SHA256；
- 详情接口与可选 PDF 响应元数据；
- 源站接口族、源类型、工程/货物/服务分类；
- `crawlerVersion`、`extractionVersion`、解析警告；
- PDF 地址、本地保存路径、下载状态、文件大小和 SHA256。

payload 快照不重复保存大段 HTML，只保存 HTML 字段哈希；正文原文在独立 HTML 快照中，
既能溯源又避免 JSON 与快照重复占用空间。

## 运行命令

先确认虚拟环境中存在 Scrapy，并建议安装 `pdftotext` 供 PDF 调试解析：

```bash
cd /home/intsig/Crawler_Scrapy
command -v pdftotext
```

每类测试 5 条公告，只采公告和快照，不下载附件：

```bash
./run_runshihua.sh --phase notices --all --max-records 5
```

公告采集完成后继续下载附件：

```bash
./run_runshihua.sh --phase all --days 180
```

全历史公告及附件：

```bash
./run_runshihua.sh --phase all --all
```

只采部分类型：

```bash
./run_runshihua.sh --phase notices \
  --sections tender,purchase,candidate,award \
  --days 30 --max-records 1000
```

只补下载已登记但尚未完成的附件：

```bash
./run_runshihua.sh --phase attachments
```

校验 JSON/CSV 数量、payload/HTML 快照哈希、编号溯源、候选人/报价对应关系及
本地 PDF 文件头、大小和哈希：

```bash
python -m crawler_scrapy.sites.runshihua.audit --output-root new_output
```

如需调试 PDF 文字层参与抽取，可直接运行蜘蛛；此命令适合少量验证，不用于全量附件下载：

```bash
/home/vipuser/miniconda3/envs/myenv/bin/python -m scrapy crawl runshihua \
  -a categories=tender,candidate,award -a max_records=5 -a parse_pdf=true
```

输出目录为 `new_output/runshihua/`：

- `json/`、`csv/`：统一字段公告；
- `snapshots/`：原始正文与 payload 快照；
- `attachments/`：公告 PDF；
- `state/`：去重索引、JOBDIR、运行锁与断点；
- `logs/`：实时公告和附件日志。

相同参数手动停止后再次运行，会使用相同任务作用域和 JOBDIR 继续；已经完成的公告和附件
会由去重索引跳过。

当前抽取规则版本为 `runshihua-v2-verified-api-html-pdf`。

## 2026-08-12 真实样本复核

独立验证目录 `new_output/runshihua_validation_10/runshihua/` 共保存 110 条公告和
110 个 PDF。招标、采购、招标变更、采购变更、候选人、结果、控制价、撤销、补充、延期
等有足够历史数据的类型各验证 10 条；源站公开接口实际只有候选人更正 4 条、结果更正
4 条、控制价变更 2 条，资格预审及其变更为 0 条，未用重复数据补足。

复核后修正了控制价项目名占位词、当前公告发布日期、候选人名称跨行和“拟中标价格”四类
问题。最终 110 条均为 `PARSED`；149 个非空项目/招标编号均可回溯至正文或 payload，
候选人/报价和中标人/中标价数量全部对应，110 个附件的路径、大小、PDF 文件头和哈希通过
校验。108 个 PDF 成功提取文字层；`other:1063` 和 `candidate:960` 为无有效文字层文件，
保留 `DOWNLOADED_NO_OCR` 并使用公开接口/HTML 字段，若需要补齐图片中的文字须另行 OCR。
