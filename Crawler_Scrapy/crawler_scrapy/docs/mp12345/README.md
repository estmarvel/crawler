# mp12345 煤婆网公开招采公告

公开入口：

- 首页：`https://www.mp12345.com/`
- 公告列表页：`https://www.mp12345.com/infomercial/newInviteTendersNotice.jsp`
- 列表 API：`https://www.mp12345.com/get/local/bidding/info.json`
- 详情前端路由：`https://bidtest.mp12345.com/bidweb/#/notice/view/{notice_id}/{notice_type}`
- 详情 API：`https://bidtest.mp12345.com/bids/exchange/noticedetail/getBidNotice.htm`
- PDF 正文：`https://bidtest.mp12345.com/bids/exchange/noticedetail/viewBidNoticePdf.htm?fileId={noticeFileId}`

边界：

- 公开列表、公开详情元数据、公开 PDF 正文不需要登录。
- 登录、报名、投标、CA、签章、保证金等业务流程不纳入公开采集。

运行示例：

```bash
cd /home/intsig/Crawler_Scrapy
./run_mp12345_json.sh --output-root new_output_mp12345_sample --max-records 20 --include-details --include-pdf-text --refresh
./run_mp12345_json.sh --output-root new_output_mp12345_full_list --page-size 500 --refresh
```

输出：

- `json/*.json`：按公告类型拆分后的数据库兼容 JSON 数组，可被 `crawler_prisma/new_scripts` 导入器扫描。
- `json/mp12345_records.jsonl`：断点续跑友好的逐行中间文件，导入器不会读取。
- `state/summary.json`：采集参数、源站总数和结果统计，导入器不会读取。
- `payloads/list_page_*.json`：列表接口原始响应快照。
- `texts/*.txt`：PDF 解析出的公告正文文本。

JSON 兼容入库字段：

- 使用统一字段名：`平台名称`、`平台代码`、`公告ID`、`公告类型`、`公告子类型`、`公告标题`、`发布时间`、`公告页面URL`、`解析状态`、`内容指纹`、`抽取方式`、`抽取版本`、`爬取时间`、`公告内容`、`_trace`。
- 项目/业务字段平铺在记录顶层，不放在嵌套对象中。
- JSON 不保存 PDF 二进制或 base64；只保存 PDF URL、文件 ID、字节数和 PDF 抽出的文本。
