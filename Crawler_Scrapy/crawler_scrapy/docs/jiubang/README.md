# 玖邦招标采购电子交易平台爬虫

Spider名称为 `jiubang`。详细的公共框架、字段、溯源、断点和防封说明见
[《五站爬虫详细实现说明》](../五站爬虫详细实现说明.md)，站点接口分析见
[analysis.md](analysis.md)。

## 采集内容

当前只采集招投标模块：

- `zbgg_zys`：招标公告和资格预审；
- `hxr`：中标候选人公示；
- `gs`：中标结果、更正或撤销结果；
- `zbjh`：招标计划。

玖邦和华新使用同版本TWS前端，因此复用已由实样修正的请求流程与字段规则，但使用玖邦
独立域名、接口、平台代码、详情链接和输出目录。独立采购、竞价和零散采购模块目前不在
采集范围内。

附件阶段会在每次下载前按 `fileId` 重新查询短期签名 CDN URL，只接受玖邦官网及
`cdn.v3.bjjbkj.cn`、`public.cdn.bjjbkj.cn` 的 HTTPS 地址，避免旧签名失效或跨站
重定向。

## 运行

```bash
cd /home/intsig/Crawler_Scrapy

# 最近180天公告和附件，默认直连
./run_jiubang.sh

# 只抓公告
./run_jiubang.sh --phase notices --days 30

# 四类各最多5条
./run_jiubang.sh --phase notices --max-records 5 --max-pages 2 --page-size 5

# 开启C2混合AI（GLM-5.2）
./run_jiubang.sh --phase notices --ai-extract --ai-provider zhipu --ai-model glm-5.2

# 切换为Qwen3-8B；0表示不限制总调用次数，仍遵守调用间隔和限流退避
./run_jiubang.sh --phase notices --ai-extract --ai-provider siliconflow --ai-model Qwen/Qwen3-8B --ai-max-calls 0

# 只下载附件
./run_jiubang.sh --phase attachments
```

正式入口默认直连、并发2、请求间隔3到5秒、每400个响应冷却180到300秒；第一次
403/429即停止。默认输出位于 `output/jiubang/`。

当前已接入与千极链相同的证据裁决型混合AI，但只在重要字段缺失且有明确标签、
长章节越界、HTML/转义残留或名单报价错位时调用；快照、原始响应和附件字段不交给AI。
