# 山西新点（sxxindian）爬虫

站点地址：<http://www.sxxindian.com/>。

本爬虫已经接入项目现有的 Scrapy 统一框架，覆盖“招标信息”和“企业采购”两个业务模块。它直接调用网站列表接口，再请求服务端渲染的公告详情 HTML，不依赖 Selenium 或浏览器自动化。

## 采集范围

- 招标信息：招标计划，以及招标公告、其他公告、资格预审公告、变更公告、中标候选人公示、结果公告的工程、货物、服务类型。
- 企业采购：采购公告、变更公告、结果公告、合同公示、征求意见、招标公告；详情解析时识别公开招标、询比采购、竞争性谈判、竞争性磋商、询价采购、单一来源等采购方式并分别输出。

共配置 25 条列表采集分支。栏目在指定日期范围内没有数据时不会生成空结果文件。

## 统一运行与断点续爬

```bash
cd /home/intsig/Crawler_Scrapy
chmod +x run_sxxindian.sh
./run_sxxindian.sh --phase all --days 30 --ai-extract
```

全历史公告和附件：

```bash
./run_sxxindian.sh --phase all --all --ai-extract
```

只采公告或只补附件：

```bash
./run_sxxindian.sh --phase notices --days 30
./run_sxxindian.sh --phase attachments
```

小样本测试：

```bash
./run_sxxindian.sh --phase notices --days 30 --max-records 5 --max-pages 1
```

结果默认统一写入 `output/sxxindian/`：`json/` 保存结构化结果，
`snapshots/` 保存 HTML，`payloads/` 保存列表请求证据，`attachments/` 保存附件，
`state/` 保存去重索引、JOBDIR 和运行锁，`logs/` 保存实时日志。相同参数停止后重启会沿用
断点，不会重复保存已完成公告。

## 字段提取与 AI 校验

传入 `--ai-extract` 时采用“规则候选值 + 局部证据窗口 + Qwen3-8B 复核 + 字段契约校验”的 C 方案。
列表/API 直接给出的标题、日期和源站元数据不调用模型；仅对正文中重要且规则容易误切的字段
触发模型。模型没有固定总调用次数上限，但按最小 6 秒间隔控制免费接口的每分钟频率。
不传 `--ai-extract` 即只用规则，也可用 `--ai-provider zhipu --ai-model glm-5.2` 保留原 GLM 路径。

已修正规则包括：项目性质不再使用导航栏目冒充；工程/货物/服务只保存为项目类型，不冒充
所属行业；项目编号与招标编号按明确标签分离；候选人/中标人不再接受章节标题；资格要求、
递交方法和联系人字段在下一章节或提示语前截止。

`run_sxxindian.sh` 只在人工执行时运行，不会创建定时任务。
