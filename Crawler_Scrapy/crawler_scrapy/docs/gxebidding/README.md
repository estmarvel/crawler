# 国信 e 采（山西）公开公告爬虫

公开入口：<https://gx.e-bidding.org/>

站点代码为 `gxebidding`。爬虫只读取无需账号、登录或 CA 的公告列表、公告详情
外壳和公告 PDF，不访问招标文件购买、投标文件递交、开标解密等交易接口。

## 采集范围

三个公开频道：

- `lawful`：依法招标，源站参数 `tenderMethod=01`；
- `nonlawful`：非依法招标，源站参数 `tenderMethod=02`；
- `purchase`：非招标采购，源站参数 `tenderMethod=00`。

五个源站栏目：

| 参数 | 源站类别 | categoryId | 统一公告类型 |
|---|---|---:|---|
| `tender` | 招标/采购公告 | 2 | 招标公告 / TENDER |
| `change` | 变更/二次公告 | 3 | 更正结果公示 / CORRECTION |
| `candidate` | 中标/成交候选人公示 | 5 | 中标候选人公示 / CANDIDATE |
| `award` | 中标/成交结果公告 | 4 | 中标结果公示 / AWARD |
| `termination` | 终止/废标公告 | 6 | 更正结果公示 / CORRECTION |

`notice_subtype` 使用 `<频道>.<类别>`，例如 `purchase.candidate`。标准类型统一，
源站的“成交”“终止”等语义仍由 `notice_subtype`、项目性质、公共类型和溯源元数据保留。

## 获取与解析流程

1. 请求 `/sxyczscms/category/iframe.html` 的服务端渲染列表；
2. 提取 CMS 编号、标题、发布日期、报名截止时间和详情地址；
3. 请求 `sdny_bulletin`、`sdny_changeBulletin`、`sdny_winningperson`、
   `sdny_resultBulletin` 或 `sdny_failBulletin` 详情；
4. 从 PDF.js iframe 中解析 `fileType` 和公开文件 UUID；
5. 公告阶段登记 PDF，保存详情 HTML 和 payload 快照；
6. 附件阶段断点下载 PDF，校验 `%PDF-` 文件头；
7. 优先使用 `pdftotext -layout` 提取文字层并回填 JSON/CSV；无有效文字层时保留
   PDF 并标记 `DOWNLOADED_NO_OCR`，不伪造字段。

公开 PDF 的 `fileType` 对应关系为：招标 2、变更 3、结果 4、候选 5、终止 6。

项目编号优先取“招标项目编号/采购项目编号/投资项目统一代码/项目代码”，招标编号
单独取“招标编号/采购编号/代理编号”。候选人名称和报价按 PDF 表格排名对齐，不能
分别抽取后无序组合。

## 运行命令

默认采集最近 180 天，随后下载并解析附件：

```bash
cd /home/intsig/Crawler_Scrapy
./run_gxebidding.sh --phase all
```

首次小规模验证，每个频道、每个类别最多 5 条：

```bash
./run_gxebidding.sh --phase all --days 180 --max-records 5 --max-pages 20
```

全历史：

```bash
./run_gxebidding.sh --phase all --all
```

只采集公告和登记 PDF：

```bash
./run_gxebidding.sh --phase notices --all
```

只下载尚未完成的 PDF 并回填字段：

```bash
./run_gxebidding.sh --phase attachments
```

指定频道和类别：

```bash
./run_gxebidding.sh --phase all --days 365 \
  --channels lawful,purchase \
  --sections tender,candidate,award
```

统一运行器对本站强制单域单并发。默认请求间隔为 3～5 秒、每 400 个响应冷却
180～300 秒、普通请求超时 300 秒、附件读取超时 900 秒。可在命令行调整间隔和
超时，但不建议提高并发。

## 输出与恢复

输出目录为 `new_output/gxebidding/`：

```text
json/                  按统一公告类型输出的 JSON
csv/                   与 JSON 对应的 CSV
snapshots/             PDF 容器详情 HTML
payloads/              列表记录、频道、类别和 PDF 定位元数据
attachments/           原始公告 PDF
state/dedup/            公告身份和内容指纹去重索引
state/jobs/             Scrapy JOBDIR 请求断点
state/attachments.json  附件下载进度
logs/                   分批运行日志
```

公告身份使用 `<详情路径族>:<CMS编号>`，例如 `bulletin:11253`；附件身份使用
公开文件 UUID。中断后再次运行相同范围会复用 JOBDIR、公告去重索引和附件状态，
不会重新保存已完成记录。

查看状态：

```bash
./crawler_status.sh gxebidding
./crawler_status.sh gxebidding --watch 10
```

完整性审计（校验 JSON/CSV 数量、类型映射、编号证据、候选人/报价对应、快照、
PDF 文件头/大小/哈希和附件解析状态）：

```bash
/home/vipuser/miniconda3/envs/myenv/bin/python \
  -m crawler_scrapy.sites.gxebidding.audit \
  --output-root new_output
```

建议在 tmux 中运行：

```bash
tmux new -s gxebidding
./run_gxebidding.sh --phase all --all
# Ctrl-b d 离开；随后使用 tmux attach -t gxebidding 返回
```

当前规则版本：`gxebidding-v2-validated-html-pdf`。

## 10 条实站验证结论（2026-08-12）

验证目录为 `new_output/gxebidding_validation_10/gxebidding/`。本轮按三个频道和
五个源站类别分别取 10 条，共 150 条，而不是只按合并后的四种标准类型抽样：

- 招标公告 30 条；
- 中标候选人公示 30 条；
- 中标结果公示 30 条；
- 更正结果公示 60 条（变更 30 条、终止 30 条）。

150 份 PDF 均下载成功，HTML/payload/PDF 路径、大小和哈希校验通过；148 份
PDF 有可用文字层并完成结构化，2 份图片型 PDF 标记为 `PARTIAL` 和
`DOWNLOADED_NO_OCR`。审计核对了 194 个项目/招标编号以及 94 个候选/中标报价，
均可回到 PDF 或源站快照证据。图片型 PDF 不会凭标题伪造正文，也不会进入项目
业务表；原始公告、PDF 和抽取记录仍可进入原始存储，待 OCR 后重新解析。

根据实站样本补充的规则包括：跨行候选人和联合体表格、第一/第二成交候选人、
百分比和元/吨报价、含税/不含税中标价、中标单位/中标人/成交人写法、联合体
牵头人与成员拆分、标题方括号/标段清理，以及明确项目编号优先于普通代理编号。

数据库输入使用 `crawler_prisma/new_scripts`：原始层可接收 150 条公告和 150 个
附件；业务层只接收 148 条 `PARSED` 公告，共归并为 105 个项目（项目编号 69、
招标编号兜底 24、项目名称兜底 12）。数据库数据源对应 `data_source.id=26`、
`short_code=guoxin_shanxi`。所有非项目列业务字段写入
`project_notice.structured_data` 和抽取 Mongo 文档，HTML、payload、正文和附件
分别按溯源链保存，不要求每个 JSON 字段在 `project` 表重复建立独立列。
