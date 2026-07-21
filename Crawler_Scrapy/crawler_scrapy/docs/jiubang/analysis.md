# 玖邦招投标模块前端分析

分析范围为本目录现有生产前端 JS。当前只接入招投标模块，不接入采购、竞价和
零散采购模块。

## 平台与接口

- 网页域名：`https://www.bjjbkj.cn`
- API 根地址：`https://www.bjjbkj.cn:9998`
- 招投标列表：`POST /bidding/bidAnnouncement/getWebAnnPage`
- 公告详情：`GET /bidding/bidAnnouncement/getAnnWebByAnnId?annId=<ID>`
- 备用详情：`GET /web/inputAnnouncement/getInputAnn?annId=<ID>`
- 招标计划列表：`POST /bidding/web/biddingPlan/biddingPlanList`
- 附件元数据：`GET /bidding/file/query/<fileId>`
- 普通详情页：`/#/biddingdetails?annId=<ID>`
- 招标计划详情页：`/#/biddingplan?planid=<ID>`

公开页面的列表、详情和附件请求没有依赖登录 Token。主文件会把可选的
`jbcookie` 写入 `authentication` 请求头，但无 Cookie 时公开接口仍按匿名请求。

## 与华新的复用关系

玖邦与华新使用同一套 TWS 招投标前端，以下语义一致：

- `annClassification=1/2/3` 对应招标公告、候选公示、结果公示；
- `annNature` 的正常、再次、重新、变更、终止、延期、暂停、更正和撤销映射；
- 普通公告详情对象中的正文、联系人、中标候选人、中标结果字段；
- 页面只在顶层 `fileId` 存在时展示附件，并通过 bidding 文件服务解析地址；
- 主详情无数据时回退到 inputAnnouncement 接口。

因此代码复用华新经过真实数据修正的解析规则和请求流程，仅替换平台身份、域名、
API 地址及列表请求体。JSON/CSV、附件、去重索引全部使用独立的 `jiubang` 输出空间，
不会与华新数据混合。

## 招标计划说明

首页 JS 明确给出了招标计划列表接口、请求体 `{current,size,status:6}` 和详情路由。
当前目录没有路由清单指向的懒加载文件 `2413.32016dd6.js`，因此无法从现有文件再次
核验计划详情请求行。代码暂按同版本 TWS 系统已验证的公开接口
`/bidding/web/biddingPlan/getBiddingPlan/<ID>` 接入，并集中放在站点配置中，后续若
补充该 JS，只需核对或调整一个常量。

