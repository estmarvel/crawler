# 比比网字段来源

- 招标计划：`/api/home/zbjhInfo/{id}` 的结构化字段，附件使用计划项目下载接口。
- 招标公告：`/api/home/ggInfo/{id}` 的 `ggInfo`、`xmInfo` 与 `gongGaoNeiRong`，签章PDF用于补充正文。
- 中标候选人公示：`/api/home/hxrInfo/{id}` 的 `hxrInfo.neiRong` 与候选人公示签章PDF。
- 中标结果公示：`/api/home/zbjgInfo/{id}` 的 `zbjgInfo.neiRong` 与中标结果签章PDF。
- 标题、公告ID、发布日期和PDF文件名优先取详情接口独立字段。
- 项目范围、资格要求、时间、联系方式、候选人、中标人和报价按比比网正文模板分节解析。
- PDF是补充来源，不解析PDF.js查看器页面；爬虫直接请求原始PDF流。
