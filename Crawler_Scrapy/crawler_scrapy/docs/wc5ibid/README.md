# 旺采网（wc5ibid）爬虫

站点：<https://www.5ibid.net/Liems/index.html>。

爬虫接入现有 Scrapy 统一框架，直接解析网站GBK编码的服务端HTML，不使用 Selenium。列表按 `/Liems/{栏目}List/{页码}.html` 翻页，详情解析页面结构化参数、正文HTML表格和附件链接。

## 覆盖栏目

- 招标/预审公告：根据详情识别招标公告或资格预审公告。
- 控制价公告：映射为统一“更正结果公示”，控制价正文完整保存在公告内容中。
- 中标候选公示：映射为统一“中标候选人公示”。
- 中标结果：映射为统一“中标结果公示”。
- 变更公告：映射为统一“更正结果公示”。
- 废标公告：映射为统一“更正结果公示”。

## 手动运行

```bash
cd /home/intsig/Crawler_Scrapy
chmod +x run_wc5ibid.sh
START_DATE=2020-01-01 END_DATE=$(date +%F) MAX_RECORDS=100000 bash run_wc5ibid.sh
```

结果位于 `output/wc5ibid/json`、`output/wc5ibid/csv`、`output/wc5ibid/snapshots` 和附件目录。

本实现没有 daily、cron 或凌晨定时任务，只提供手动历史采集入口。

