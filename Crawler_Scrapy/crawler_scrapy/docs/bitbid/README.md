# 比比网爬虫

Spider 名称为 `bitbid`，采集比比网“招标信息”下四个源栏目，并按公告正文
重新归一为当前公共 Schema。五站的公共字段、
限速、快照、附件和断点机制见
[《五站爬虫详细实现说明》](../五站爬虫详细实现说明.md)。字段来源逐项说明见
[field_source_mapping.md](field_source_mapping.md)。

## 采集范围

| 参数 | ggType | 详情对象 | 保存类型 |
| --- | ---: | --- | --- |
| `plan` | 4 | `zbjhInfo` | 招标计划 |
| `tender` | 1 | `ggInfo` | 招标公告、资格预审、更正、中标候选人、定标候选人；旧聚合数据中的中标/成交结果归入中标结果公示 |
| `candidate` | 2 | `hxrInfo` | 中标候选人公示 |
| `award` | 3 | `zbjgInfo` | 中标结果公示；废标、流标、终止、撤销归入更正结果公示 |

列表使用公开接口 `/api/home/bbzbMoreList`，详情分别请求四个公开详情接口。解析器合并
接口结构化字段和 HTML/PDF 正文，区分项目编号与招标编号，提取中文时间、采购人、代理
机构、项目联系人、候选人/报价和中标人/中标价。多标段候选人按“标段+候选人”保留，
缺失报价用同位置空值保持列表对齐。废标、流标、终止和撤销按当前数据库枚举统一保存为
`CORRECTION`，具体语义保存在 `公共类型`，不再使用旧的 `TERMINATION` 特例。

旧版和新版计划接口复用了 `nameApproval`、`capital`、`planProjectOverview` 等键。
解析器不会只按键名直接入库，而会校验项目性质、招标方式、项目类型、行政监督部门和
预计发布时间的值域；正文项目名称和公告标题也优先于可能为“开标测试校验值”的历史
`xmInfo.faBaoMingCheng`。

## C 方案 AI 复核

比比网已经接入与千极链相同的候选窗口、字段契约、证据回指和冲突裁决链路，默认关闭。
开启后仅在规则值为空但正文存在明确标签、字段残留 HTML、长章节越界或名称/报价列表
错位时调用模型；API 发布日期等可信结构化字段不会交给模型猜测。

```bash
./run_bitbid.sh --phase notices --max-records 5 \
  --ai-extract --ai-model glm-5.2 --ai-max-calls 10
```

不加 `--ai-extract` 时只运行确定性规则，不产生模型 token 消耗。

## 推荐运行方式

```bash
cd /home/intsig/Crawler_Scrapy

# 最近180天，只抓公告、正文、快照和附件清单
./run_bitbid.sh --phase notices

# 四类各最多5条
./run_bitbid.sh --phase notices \
  --sections plan,tender,candidate,award \
  --max-records 5 --max-pages 3 --page-size 20

# 指定日期
./run_bitbid.sh --phase notices \
  --start-date 2026-07-29 --end-date 2026-07-29
```

统一入口默认直连、并发2、请求间隔3到5秒、每400个响应冷却180到300秒，并在第一次
403/429时停止。相同范围中断后重跑会复用JOBDIR和去重索引。

## PDF附件说明

公告阶段固定使用 `parse_pdf=false`，避免签章PDF阻塞正文采集；PDF地址作为附件清单
保存，随后由独立附件阶段下载。

2026-08-06 已按官网当前前端逻辑修正附件端点：招标公告、中标候选人公示和中标
结果公示的签章 PDF 使用 `www.bitbid.cn/auth/...`，招标计划附件使用
`xzb.bitbid.cn`。下载器会先取得官网下发的 `verify` Cookie，并自动把既有 JSON 中
`zb.bitbid.cn` 的旧地址迁移成当前地址；下载完成后还会校验 PDF 文件头，避免把登录页
或错误页误存成附件。遇到 403/429 仍会立即停止，不会高频重试或绕过限制。

## 输出

结果默认写入 `output/bitbid/`：

- `json/`：按标准公告类型拆分 JSON，包含紧凑 `_trace`；
- 正式运行不创建 CSV；
- `snapshots/`：接口返回HTML快照；
- `attachments/`：成功下载的附件；
- `state/`：断点和版本去重；
- `logs/`：分批运行日志。

历史 JSON 不会因解析器升级而自动覆盖；后续重新采集或执行单独、可审计的离线迁移时，
才会使用新分类和新字段值。
