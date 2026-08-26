# 云买卖电子综合交易平台

采集官网“招标信息”的三个前端栏目：招标公告、候选人公示、中标公示。

接口来自官网前端脚本：列表 `POST /web-back/nx/n/list/notice`，详情
`POST /web-back/nx/n/w/{kid}`。详情正文、列表业务字段以及 `note` 中的招标
文件获取、递交、开标等结构化字段会合并解析，原始 HTML 进入统一快照管线。

少量验证每类两条：

```bash
START_DATE=2020-01-01 END_DATE=$(date +%F) MAX_RECORDS=2 bash run_eqbidding.sh
```

只跑某一类可追加：`-a feeds=tender`、`-a feeds=candidate` 或 `-a feeds=award`。
