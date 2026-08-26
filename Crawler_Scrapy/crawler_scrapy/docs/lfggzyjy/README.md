# 临汾公共资源交易平台爬虫说明

站点：`http://lfggzyjy.linfen.gov.cn`

本爬虫只采集公开公告页，不访问 CA 登录后的业务办理后台。

公开列表接口：

```text
http://lfggzyjy.linfen.gov.cn/moreInfoController.do?getMoreNoticeInfo&page=1&rows=20
```

默认只处理工程建设公开表：

- `gcjs_tender_plan`：招标计划
- `gcjs_notice`：招标公告/变更/控制价等
- `gcjs_zbhxrgs`：中标候选人公示
- `gcjs_result_notice`：中标结果公示

CA/登录相关入口如 `loginController.do?...`、投标人/招标人业务办理、电子交易系统不纳入采集。

