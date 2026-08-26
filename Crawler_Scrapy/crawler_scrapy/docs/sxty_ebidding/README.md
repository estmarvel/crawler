# 招采进宝电子招标投标交易平台（山西）爬虫

## 数据源

- 入口：`https://sxty.ebidding.net.cn/cms/sx/webfile/zdsx=jyxx/index.html`
- 列表：`POST /cms/api/dynamicData/queryContentPage`
- 详情：`POST /cms/api/dynamicData/queryContent`
- 站点代码：`sxty_ebidding`

浏览器详情路由有时展示点选验证码，但页面最终正文仍由公开详情 API 加载。
Spider 不访问验证码页面、不提交或伪造 `captchaVerification`，只调用官网前端
自身使用的公开 JSON API。

## 采集范围

建设工程包括招标计划、招标公告、变更公告、中标候选人公示、中标公告、
其他公告、暂停/终止公告；企业采购包括采购计划、采购公告、变更公告、
成交候选人公示、成交结果公告和其他公告。

源站栏目名称与代码会保留在 `field_meta`，输出则按框架统一公告类型合并：

- 采购计划归入招标计划；
- 采购公告归入招标公告；
- 成交候选人公示归入中标候选人公示；
- 中标/成交结果归入中标结果公示；
- 变更、暂停、终止归入更正结果公示；
- “其他公告”按标题和正文重新判型，不能直接假定为变更公告。

## 完整性和溯源

详情 API 的 `res` 按官网 `detail.js` 逻辑执行 base64 与 URL 解码。解码结果包含
项目、标段、同项目各阶段公告、正文 HTML 和 `resourceList`。Spider 使用列表
`contentId` 在所有标段和阶段中精确定位当前公告，并保存：

- 当前公告正文 HTML 快照；
- 解码后的完整 API payload 快照；
- 列表和详情安全响应元数据；
- HTML/payload SHA256；
- 附件元数据和独立下载状态。

平台内部的 `project.id`、`outProjectId`、`package.id` 只用于溯源，不能当业务
项目编号。官网 `detail.js` 明确把 `project.code` 显示为“项目编号”，正文若有
“招标项目编号/采购项目编号”则优先使用正文精确标签；招标编号只从正文明确的
“招标编号/采购编号/代理编号”提取，不能根据代码形态猜测。

公开公告正文和公开附件可以采集；登录、投标、CA 或权限控制的招标文件不属于
公开详情 API 数据，不能声称已经采集。

## 规则解析与选择性 AI

本站沿用框架的“规则/API 主提取，异常字段局部 AI 复核，证据校验后裁决”流程，
默认模型为硅基流动 `Qwen/Qwen3-8B`。模型不会接收全部预设字段，也不会读取
整篇 HTML：程序先根据字段标签和章节生成带行号的小窗口，模型返回字段值及
原文行范围，只有通过字段类型、标签、原文回指和列表一致性校验的候选才可能
补全或替换规则值。API 明确返回的发布日期、项目类型、招标方式和组织形式会被
标记为可信字段，AI 不得覆盖。

当前只把实采审计中已证明不稳定的字段列入候选：

- 招标计划：表格相邻单元格可能串列的招标内容、建设内容、金额、招标方式、
  项目类型、招标人及监督部门；
- 招标/资格预审：缺值但正文有明确标签的编号、金额、时间、地点、范围、资格、
  工期、质量、文件获取/递交、保证金及联系方式；规则范围误吞“质量要求”时
  强制升级该范围字段；
- 中标候选人：只复核缺值或异常的编号、公示时间和联系方式，API/表格中已经
  对齐的候选人名称与报价不重复调用模型；
- 中标结果：只复核缺值或签章占位异常的工期、项目经理、证书及联系方式；
- 更正/终止：复核实际更正内容的语义边界，以及缺值或异常的编号、时间、依据、
  监督部门和联系方式。

AI 调用失败、限流或证据校验不通过时保留规则结果，并把调用、候选窗口、冲突
与裁决信息写入 `field_meta.sxtyEbiddingHybridAi`，不影响 HTML、payload 和
附件溯源。

## 反爬保护

- 单域并发固定为 1；
- 推荐请求间隔 3～5 秒并启用 AutoThrottle；
- 403、429 第一次即退避并停爬；
- API 返回验证码 HTML、非 JSON 或异常业务码时立即关闭 Spider；
- JOBDIR、去重索引和已保存快照不会删除，下次可恢复；
- 不自动识别验证码，不轮换 IP 绕过访问控制。

## 运行

近 180 天公告和附件：

```bash
cd /home/intsig/Crawler_Scrapy
./run_sxty_ebidding.sh --phase all --days 180 \
  --output-root /home/intsig/Crawler_Scrapy/output \
  --delay-min 3 --delay-max 5
```

只采公告：

```bash
./run_sxty_ebidding.sh --phase notices --days 30 --max-records 5
```

启用 Qwen3-8B 选择性 AI（密钥只从 `.env` 的 `SILICONFLOW_API_KEY` 读取）：

```bash
./run_sxty_ebidding.sh --phase notices --days 30 --max-records 5 \
  --ai-extract --ai-provider siliconflow --ai-model Qwen/Qwen3-8B
```

隔离的 100 条/源栏目 AI 验证任务：

```bash
./run_sxty_ebidding_ai_validation_100.sh
```

该任务遍历建设工程和企业采购共 13 个公开源栏目，每个栏目最多调度 100 条，
不足 100 条时采完栏目现有公告；结果只保存为 JSON，并写入独立目录
`validation_output/sxty_ebidding_ai_validation_100/sxty_ebidding/`，不会与正式
`output/sxty_ebidding/` 的去重状态或结果混用。重复运行会沿用 JOBDIR 和验证目录
中的去重状态继续执行。

默认使用 `.env` 中的 `SILICONFLOW_API_KEY`；若需要选择另一个已配置账号：

```bash
SXTY_VALIDATION_API_KEY_ENV=SILICONFLOW_API_KEY2 \
  ./run_sxty_ebidding_ai_validation_100.sh
```

全部历史：

```bash
./run_sxty_ebidding.sh --phase all --all
```

输出位于 `output/sxty_ebidding/`，JSON 按框架标准公告类型保存，不生成 CSV。
