# 山西省招标投标公共服务平台爬取说明

## 采集范围

入口：<https://www.sxbid.com.cn/f/new/notice/list/16>

站点代码为 `sxbid`，采集“依法招标”区域的八类公开公告：

| 参数 | 源站栏目 | 统一公告类型 |
|---|---|---|
| `plan` | 招标计划 | `PLAN` |
| `prequalification` | 资格预审公告 | `PREQUALIFICATION` |
| `tender` | 招标公告 | `TENDER` |
| `candidate` | 中标候选人公示 | `CANDIDATE` |
| `final_candidate` | 定标候选人公示 | `FINAL_CANDIDATE` |
| `award` | 中标结果公示 | `AWARD` |
| `correction` | 更正公告公示 | `CORRECTION` |
| `contract` | 合同和履约 | `CONTRACT` |

这些公开列表和详情不要求登录或 CA。导航中的“项目核查”等业务功能不在采集范围。

## 实现方式

列表使用 `/f/new/notice/list/<栏目编号>`。第一页为 GET，后续分页为 POST，提交
`pageNo/pageSize/title/recentType`；每页最多 100 条。列表保存公告 UUID、标题、发布日期、
地区、工程/货物/服务类型和详情地址。

详情存在三种正文形态：

1. 招标计划：HTML 结构化表格；
2. 合同和履约：HTML 结构化表格；
3. 其余公告：HTML 保存项目元数据，公告正文由同源 PDF iframe 提供。

对于第三种页面，公告阶段会读取正文 PDF 的文字层用于字段提取，同时仍把该 PDF 作为附件
登记。HTML 原响应独立保存为快照，PDF 在附件阶段可恢复下载，公告、正文 PDF 与附件通过公告
UUID 保持对应，并记录 PDF 内容 SHA256 与字节数。若 PDF 没有文字层或下载失败，记录保存为 `PARTIAL`，不会伪造缺失字段。
普通公告使用 `pdftotext -layout` 保留段落结构；定标候选人历史 PDF 存在超宽、跨页表格，
单独使用 `pdftotext -raw` 恢复排名顺序，再重组被换行拆开的联合体名称和对应报价。

字段抽取按统一八类 Schema 输出，并同时保留：

- `项目编号`：优先投资项目统一代码、项目编号、招标项目编号；
- `招标编号`：只取明确的招标编号、采购编号或代理编号；
- `_trace.responseMetadata/fieldMeta/payloadSnapshot`；
- HTML 快照路径和 SHA256；
- 源站名称、栏目、地区、项目类型、项目链编号及 PDF 地址。

当前规则版本为 `sxbid-v3-hybrid-semantic-provenance`。在既有真实样本规则上补充了以下处理：

- “招标项目编号”优先作为项目关联编号；同一正文另有不同的普通“项目编号”时，后者保存为招标编号；
- 识别“重新/二次”、招标控制价等公告后缀，避免混入项目名称；
- 兼容“项目概况和招标范围”“获取方法”“投标文件递交截止时间”等同义标签；
- 恢复被 PDF 表格拆到排名行上下两侧的候选人名称，并按排名绑定报价和单位；
- 支持正文句式描述的联合体中标人、元/吨单价和特许经营期；
- 合并 PDF 换行拆开的资金来源、地址、联系人和代理机构字段。
- 不再把内部项目链 UUID 当作项目编号，不再把列表地区冒充正文项目地点；
- 不再把递交地址冒充开启地点，“招标计划”也不再作为项目性质保存；
- 对正文重要且规则容易误切的字段使用 C 方案局部证据窗口，由 Qwen3-8B 复核后再经过字段契约校验；API/HTML 表格中可信的直接字段不调用模型。

验证集共 39 条真实公告：除源站只有 4 条的定标候选人外，其余七类各 5 条。关键字段、
候选人/报价数量、PDF 正文和 HTML 快照哈希均通过离线复核。

## 访问保护

该站连续并行请求时可能返回 nginx 错误，因此统一运行器对 `sxbid` 强制单域单并发。默认请求
间隔 3～5 秒、每 400 个响应冷却 180～300 秒，使用直连和 AutoThrottle。不要为了追求速度提高
并发；可以通过日期范围减少请求量。

## 运行命令

运行环境需要 `pdftotext`（Ubuntu/Debian 对应 `poppler-utils`）：

```bash
command -v pdftotext
```

先测试每类最新 5 条公告，不下载附件（定标候选人源站目前总共只有 4 条）：

```bash
cd /home/intsig/Crawler_Scrapy
./run_sxbid.sh --phase notices --all --max-records 5 --max-pages 1
```

公告和附件连续运行：

```bash
./run_sxbid.sh --phase all --days 180 --ai-extract
```

全历史采集：

```bash
./run_sxbid.sh --phase all --all
```

只采指定类型：

```bash
./run_sxbid.sh --phase notices \
  --sections plan,tender,candidate,award \
  --days 30
```

只补下载附件：

```bash
./run_sxbid.sh --phase attachments
```

默认输出目录为 `output/sxbid/`：

- `json/`：八类公告；正式运行不创建 CSV；
- `snapshots/`：详情 HTML 快照；
- `attachments/`：公告正文 PDF 和公开附件；
- `state/`：公告去重、JOBDIR、运行锁和断点；
- `logs/`：每批公告及附件实时日志。

手动停止后用相同参数重启，会从 JOBDIR 和公告去重索引继续，不会重新保存已完成公告。

传入 `--ai-extract` 后，默认 Qwen 模型没有固定总调用次数上限，但最小调用间隔为 6 秒，
以适配免费接口的分钟限流；不传该参数即只用规则，也可使用
`--ai-provider zhipu --ai-model glm-5.2` 切换回原 GLM 配置。
