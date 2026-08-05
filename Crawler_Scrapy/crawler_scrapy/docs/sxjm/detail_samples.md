# 山西焦煤各频道公告详情样本

以下链接用于核验山西焦煤专用解析规则，ID来自网站公开列表接口（2026-07-27）。

```text
依法项目/
├── 招标计划：https://www.sxccdzzcpt.cn/home/detail?id=44798
├── 招标（预审）公告：https://www.sxccdzzcpt.cn/home/detail?id=44945
├── 中标候选人公示：https://www.sxccdzzcpt.cn/home/detail?id=44182
├── 结果公告：https://www.sxccdzzcpt.cn/home/detail?id=44426
└── 终止公告：https://www.sxccdzzcpt.cn/home/detail?id=44105

招标项目/
├── 招标（预审）公告：https://www.sxccdzzcpt.cn/home/detail?id=45117
├── 中标候选人公示：https://www.sxccdzzcpt.cn/home/detail?id=45104
├── 中标公告：https://www.sxccdzzcpt.cn/home/detail?id=45056
└── 终止公告：https://www.sxccdzzcpt.cn/home/detail?id=44994

非招项目/
├── 采购（预审）公告：https://www.sxccdzzcpt.cn/home/detail?id=45123
├── 成交候选人公示：https://www.sxccdzzcpt.cn/home/detail?id=45115
├── 成交公告：https://www.sxccdzzcpt.cn/home/detail?id=45121
└── 终止公告：https://www.sxccdzzcpt.cn/home/detail?id=44933

简易采购限额以下/
├── 采购公告：https://www.sxccdzzcpt.cn/home/detail?id=44521
├── 成交公告：https://www.sxccdzzcpt.cn/home/detail?id=43107
└── 终止公告：https://www.sxccdzzcpt.cn/home/detail?id=44485
```

详情页由同一个前端组件渲染，结构化数据来自
`/api/portal/v1/announcement/details/{id}`；正文的段落和表格模板按招标计划、招标/采购公告、候选人公示、结果公告、终止公告五个家族分别解析。
