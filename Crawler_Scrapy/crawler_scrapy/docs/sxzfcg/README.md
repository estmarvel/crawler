# 山西省政府采购网爬虫

## 入口

- 站点：`http://www.ccgp-shanxi.gov.cn/`
- 列表接口：`POST /portal/searchHome`
- 详情接口：`GET /portal/detail?articleId=...`
- 用户可打开详情页：`/site/detail?articleId=...`

## 已接入栏目

- `tender`：采购公告，`ZcyAnnouncement1`，映射为招标公告
- `award`：结果公告，`ZcyAnnouncement2`，映射为中标结果公示
- `change`：变更公告，`ZcyAnnouncement3`，映射为更正结果公示
- `contract`：合同公告，`ZcyAnnouncement4`，映射为合同与履约

列表接口存在 `total=0` 但 `children` 有数据的情况，因此翻页只依赖当前页
是否返回记录、日期窗口、`max_pages` 和 `max_records`。

## 运行

```bash
./run_sxzfcg.sh --phase notices --categories tender --max-records 5 --max-pages 1
```
