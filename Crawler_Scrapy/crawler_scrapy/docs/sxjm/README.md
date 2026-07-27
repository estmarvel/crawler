# 山西焦煤电子招采平台爬虫

`sxjm` 复用现有 Scrapy 公告框架，默认采集首页全部四个频道，并按频道、栏目分别生成 JSON/CSV：

- `yfxm` 依法项目：招标计划、招标（预审）公告、中标候选人公示、结果公告、终止公告。
- `zbxm` 招标项目：招标（预审）公告、中标候选人公示、中标公告、终止公告。
- `fzxm` 非招项目：采购（预审）公告、成交候选人公示、成交公告、终止公告。
- `jycg` 简易采购限额以下：采购公告、终止公告、成交公告。

接口返回值采用网站前端相同的 AES-128-CBC 方式解密。四个频道分别使用网站接口的 `category=1/3/2/4`，公告类型也按各频道真实编号请求。

## 安装与运行

```bash
pip install -r requirements_sxjm.txt
scrapy crawl sxjm -a days=30 -a max_records=200
```

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
START_DATE=2020-01-01 bash run_sxjm_history.sh
```

可通过 `channels`、`sections`、`start_date`、`end_date`、`days`、`page_size`、`max_pages` 和 `max_records` 控制范围。`max_records` 按每个实际接口分别计算。
