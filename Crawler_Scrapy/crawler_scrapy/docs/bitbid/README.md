# 比比网爬虫

Spider 名称：`bitbid`，只采集“招标信息”下的招标计划、招标公告、中标候选人公示和中标结果公示。

## 安装

```bash
pip install -r requirements_bitbid.txt
```

## 小规模验证

```bash
scrapy crawl bitbid -a max_records=1 -a max_pages=1
```

## 历史全量

```bash
bash run_bitbid.sh --all
```

## 指定日期

```bash
./run_bitbid.sh --start-date 2026-07-29 --end-date 2026-07-29
```

栏目参数为 `plan,tender,candidate,award`，可以通过 `-a categories=tender,candidate` 只运行部分栏目。

默认输出到 `new_output/bitbid/`，其中 `json/`、`csv/` 保存四个独立结果文件，`snapshots/` 保存详情正文，`attachments/` 保存原始PDF和招标计划附件，`state/` 保存跨运行去重状态。

详情解析优先使用接口结构化字段及接口返回的HTML正文；三类公示同时下载签章PDF并通过文字层补充字段。没有文字层时保留HTML解析结果，并记录 `bitbid/pdf_without_text_layer` 统计项。
