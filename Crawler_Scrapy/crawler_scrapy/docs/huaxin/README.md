# 华新阳光采购平台爬虫

Spider名称为 `huaxin`。详细的公共框架、字段、溯源、断点和防封说明见
[《五站爬虫详细实现说明》](../五站爬虫详细实现说明.md)，采购模块边界见
[purchase_module_analysis.md](purchase_module_analysis.md)。

## 采集内容

| 参数 | 分类代码 | 内容 |
| --- | ---: | --- |
| `zbgg_zys` | 1 | 招标公告、资格预审及其变更性质 |
| `hxr` | 2 | 中标候选人公示 |
| `gs` | 3 | 中标结果、更正或撤销结果 |
| `zbjh` | 4 | 招标计划 |

列表、详情和附件元数据均使用公开JSON API，不需要登录Token。普通公告主详情没有数据时
会自动使用备用公开详情接口；招标计划使用独立计划列表和详情路由。

解析器优先读取结构化字段，再从公告HTML、项目概况、资格要求、递交方式、评审情况和
联系方式补充。附件阶段会在每次下载前按 `fileId` 重新查询实际 CDN URL，避免公告阶段
保存的短期签名过期；只允许 `www.ygcgpt.com` 和 `v3.cdn.ygcgpt.com` 的 HTTPS
地址。文件本身在独立附件阶段下载。

## 运行

```bash
cd /home/intsig/Crawler_Scrapy

# 最近180天公告和附件，默认直连
./run_huaxin.sh

# 只抓公告
./run_huaxin.sh --phase notices --days 30

# 四类各最多5条
./run_huaxin.sh --phase notices --max-records 5 --max-pages 2 --page-size 5

# 开启C2混合AI（GLM-5.2）
./run_huaxin.sh --phase notices --ai-extract --ai-provider zhipu --ai-model glm-5.2

# 切换为Qwen3-8B；0表示不限制总调用次数，仍遵守调用间隔和限流退避
./run_huaxin.sh --phase notices --ai-extract --ai-provider siliconflow --ai-model Qwen/Qwen3-8B --ai-max-calls 0

# 只继续下载已有JSON中的附件
./run_huaxin.sh --phase attachments
```

正式入口默认直连、并发2、请求间隔3到5秒、每400个响应冷却180到300秒；第一次
403/429即停止。默认输出位于 `output/huaxin/`。

当前已接入与千极链相同的证据裁决型混合AI，但只在重要字段缺失且有明确标签、
长章节越界、HTML/转义残留或名单报价错位时调用；快照、原始响应和附件字段不交给AI。
