# 山西省公共资源交易平台工程建设公告适配说明

## 实现范围

Spider 名称为 `sxzwfw`。默认仍只采集工程建设六个栏目；另外已经按独立字段解析器接入
政府采购更正公告和结果公告。土地矿权、产权交易等其他业务模块尚未接入。

| 参数 | channelId | 源站栏目 |
| --- | ---: | --- |
| `zbjh` | 198 | 招标计划 |
| `zbgg_zys` | 12 | 招标/资审公告 |
| `bg` | 13 | 更正公告 |
| `hxr` | 14 | 中标候选人公示 |
| `gs` | 15 | 中标结果公示 |
| `qt` | 16 | 其他公告 |

政府采购可选栏目：

| 参数 | channelId | 源站栏目 | 保存类型 |
| --- | ---: | --- | --- |
| `zc_gz` | 19 | 更正公告 | 更正结果公示 |
| `zc_jg` | 20 | 中标结果公告 | 中标结果公示 |

采购公告 `channelId=18` 暂不采集，预留请求和字段方案见
[`政府采购/README.md`](政府采购/README.md)。

列表页是服务端渲染 HTML。Spider 按前端真实表单向
`/queryContent-jyxx.jspx` 发起 POST；第二页起使用
`/queryContent_2-jyxx.jspx`、`/queryContent_3-jyxx.jspx`。请求参数为
`title/channelId/inDates/beginTime/endTime/origin/ext`。历史采集优先使用精确起止日期，
并按自然月拆成较小查询窗口，避免单次查询页数过多。

详情页直接请求列表中的 `.jhtml` 地址，不依赖登录 Token，不使用浏览器渲染，也没有
臆造 JSON API。标题、发布时间、信息来源和 `.cs_xq_content` 均从服务端 HTML 获取。

## 正文与字段提取

部分详情由 PDF 转换器生成，同一视觉行会拆成多个 `span`。解析器先按 `.stl_01`
重新拼接同一视觉行，再删除 `display:none`、`visibility:hidden`、签名和盖章占位，
从而避免“获取方式”被拆字以及隐藏签章污染联系方式。普通富文本详情按块级标签和
`br` 保留换行。

字段解析采用规则优先：

- 标题关键词优先确定八类 Schema，标题不规范时用 `channelId` 栏目兜底；
- 项目名称删除“招标公告、中标候选人公示、中标结果公示”等公告后缀；
- 项目性质和组织形式只有正文明确给出才保存，不固定推断；
- 项目地点保存为“正文地点|列表交易场所”，相同值不重复；
- 招标人和代理机构都从正文最后一个真正的“联系方式”章节提取，分别限定角色边界；
- PDF 物理换行只在字段解析时按下一标签/下一编号合并，原始正文不被改写；
- 候选人—报价、中标人—中标价先构造成逐条明细，缺少某条报价时保留 `null`，不会
  让后续价格向前错位；兼容“第1名：公司，投标报价：金额”的同行写法；
- 更正、终止、延期、答疑、控制价等细分类别写入“源站公告性质”。中标结果更正映射
  到“更正结果公示”，其余没有独立 Schema 的公告保留大类和源站细分类别。

默认保存详情 HTML 快照用于核验。正文中的直接文件链接会进入附件队列；如果页面调用
`Cms.attachment(...)`，Spider 会先请求 `/attachment_url.jspx` 解析真实后缀，再按
前端规则生成 `/attachment.jspx?cid=...&i=...` 下载地址。附件只下载归档，不做 OCR。

## 运行方式

先进入项目目录：

```bash
cd /home/intsig/Crawler_Scrapy
```

最近一天、小批量检查（每个栏目最多 5 条）：

```bash
./run_sxzwfw_history.sh --days 1 --max-records 5
```

六种工程建设信息类型的独立验收测试（每种 5 条，独立保存，不混入正式输出）：

```bash
./run_sxzwfw_test.sh
```

政府采购更正、结果公告各 5 条的独立验收测试：

```bash
./run_sxzwfw_test.sh --module government
```

测试默认在最近 365 天内，用一个日期查询窗口分别获取六种类型的最新 5 条，避免按月
拆分产生无用列表请求。结果保存在
`test_output/sxzwfw_5_each_<运行时间>/sxzwfw/`，日志会同时输出每种类型是否达到 5 条
以及最终是否成功导出 30 条。需要扩大搜索范围时可使用
`./run_sxzwfw_test.sh --days 730`。

最近 6 个月（默认 180 天）：

```bash
./run_sxzwfw_history.sh --days 180
```

指定精确区间：

```bash
./run_sxzwfw_history.sh --start-date 2026-01-16 --end-date 2026-07-16
```

只抓公告、候选人和结果：

```bash
./run_sxzwfw_history.sh --days 30 --sections zbgg_zys,hxr,gs
```

脚本使用 `myenv`，固定代理默认为 `210.51.27.8:10000`。代理地址、账号和密码可以用
`HUAXIN_PROXY_ENDPOINT/HUAXIN_PROXY_USERNAME/HUAXIN_PROXY_PASSWORD` 覆盖。
固定代理不可用、认证失败或触发访问限制保护阈值时任务立即停止，不回退服务器公网 IP。

输出采用框架现有的追加和版本去重逻辑：

- JSON：`output/sxzwfw/json/`
- CSV：`output/sxzwfw/csv/`
- HTML 快照：`output/sxzwfw/snapshots/`
- 附件：`output/sxzwfw/attachments/`
- 去重和续跑状态：`output/sxzwfw/state/`
- 日志：`output/logs/<运行时间>/sxzwfw.log`

同一公告内容指纹不变时不会重复写入；正文发生变化时追加新版本并保留各自爬取时间，
不会覆盖历史结果。相同时间窗口中断后重跑同一命令，会复用对应 `JOBDIR` 继续调度。

## 离线验证

不请求网站、不消耗代理的完整站点测试：

```bash
/home/vipuser/miniconda3/bin/conda run -n myenv \
  python -m unittest tests.test_sxzwfw_parser tests.test_sxzwfw_spider -v
```

规则提取无法确定的字段保持空值；可在人工抽样确认后选择性启用框架 AI 补空接口，AI
不会覆盖已有规则值。批量历史采集脚本默认关闭 AI，避免不受控的模型调用和费用。
