# Crawler_Scrapy

公共 Scrapy 框架当前包含统一公告 Schema、HTML 快照和 CSV/JSON 输出，
并以华新阳光采购平台作为第一个真实网站适配器。

## 运行环境

```bash
conda activate myenv
```

如需重建环境，可使用 `requirements.txt` 安装框架的最小依赖。

## 华新离线测试

```bash
python3 -m unittest tests.test_huaxin_parser -v
```

## 华新小批量测试

华新 Spider 默认使用固定认证代理。代理地址可放在配置中，账号和密码只允许从
环境变量读取：

```bash
export HUAXIN_PROXY_USERNAME='...'
export HUAXIN_PROXY_PASSWORD='...'
```

建议先用每栏目 1 条记录验证：

```bash
scrapy crawl huaxin -a max_records=1 -a page_size=1 \
  -s DIRECT_MAX_RESPONSES_PER_RUN=20
```

只测试指定栏目：

```bash
scrapy crawl huaxin -a sections=zbgg_zys,hxr -a max_records=5
```

栏目值为 `zbgg_zys`（招标/资审）、`hxr`（候选公示）、`gs`（结果公示）
和 `zbjh`（招标计划）。输出位于 `output/huaxin/`。

当前公开列表、详情和文件元数据 API 不发送登录 Token。详情页 JS 只根据顶层
`fileId` 展示附件，`bidAnnouncementSectionDOS` 不是页面附件来源。`pdfFile` 是
后台 PDF 版本：HTML 正文存在时不重复下载；没有 `annContent/annContent2` 的
PDF-only 详情则作为详情原件下载归档。
即使 `fileName` 为空也会保留 `fileId`，再通过前端实际使用的
`/bidding/file/query/{fileId}` 补充名称和预览 URL。华新 Spider 会下载二进制
原文件，但不做 OCR/AI 文档解析；文件保存在
`output/huaxin/attachments/<公告类型>/<公告ID>/`。导出结果的附件字段保留
`source_file_id`、`file_name`、`file_url`、`storage_path`、`file_hash`、
`file_size_bytes`、`file_type`、`parse_status`，其中 `storage_path` 相对于
`FILES_STORE`。同一源站文件 ID 使用稳定路径，已下载文件直接复用，不覆盖历史
CSV/JSON。

天启配置仍完整保留为手动备用模式。启用时需注入 `TIANQI_SECRET`、`TIANQI_SIGN`，
并增加 `-s CRAWLER_OUTBOUND_MODE=tianqi`。代理默认每次提取 10 个、有效期 3 分钟、
HTTPS 类型；生产最多调用代理 API 5 次，测试可限制为 1 次。天启模式无可用代理时
立即终止，绝不回退服务器直连。

## AI 补充 HTML 缺失字段

框架已接入可选的公共 AI Pipeline，默认关闭，因此普通爬取和离线测试不会调用
AI API。处理顺序为：网站规则解析、八类 Schema 规范化、AI 仅补空字段、再次按
Schema 转换字段类型、最后导出。AI 不覆盖规则已经提取到的值，也不会提取爬虫时间、
快照路径、详情链接、发布日期、发布网站等框架/请求元数据。

启用前只在当前终端或部署系统中注入密钥，不要写入源码：

```bash
export DMX_API_KEY='...'
scrapy crawl huaxin -a max_records=5 \
  -s NOTICE_AI_ENABLED=True \
  -s NOTICE_AI_MAX_CALLS=5
```

默认使用 OpenAI 兼容地址 `https://vip.dmxapi.com/v1` 和模型
`glm-4.6-thinking`，可分别通过 `NOTICE_AI_BASE_URL`、`NOTICE_AI_MODEL`
覆盖。超长正文会同时保留开头和结尾；同步 SDK 调用、限速等待和失败重试均在线程池
执行，不会阻塞 Scrapy 下载器。调用次数、Token 和填充字段数写入 Scrapy Stats，
每条公告的模型、目标字段和实际补充字段记录在 `field_meta.ai_extraction`。

AI 服务本身不固定字段，调用方可以为任意网站、任意公告类型传入目标字段：

```python
result = service.extract(
    notice_type="招标公告",
    title="某项目招标公告",
    fields=["项目编号/招标编号", "招标金额", "开标时间"],
    text=html_or_text,
)
```

具体 Spider 可以通过 `ai_extract_fields` 映射为不同公告类型选择不同字段；不配置
时，框架默认选择该类型仍缺失的业务字段。也可以重写
`select_ai_extract_fields()`，根据网站、公告类型和页面内容动态选择。

华新已经配置网站级字段映射：招标计划不调用 AI；结构化 API 已返回的项目名称、
编号、日期、行业、中标人和中标价等字段也不调用 AI；只有规则解析后仍为空、且可能
位于 `annContent/annContent2` 的金额、范围、资格要求、候选人、工期、项目经理和
联系方式等字段才会提交。`NOTICE_AI_ENABLED` 仍默认关闭，避免批量历史采集意外产生
模型费用。

## 跨运行去重与历史版本

默认启用 `NOTICE_DEDUP_ENABLED=True`。框架使用以下两层判断：

1. `平台代码 + 源站公告ID` 作为公告身份；没有ID时依次回退详情URL、类型/标题/时间。
2. 正文或HTML的 SHA256 作为内容版本。

索引保存在 `output/<网站代码>/state/notice_versions.json`，不是数据库。列表记录与
上次一致时，支持在发出详情请求前跳过；同一内容版本会在快照和 AI 之前丢弃；同一
公告内容变化时，会在原 CSV 和 JSON 中追加一条新版本，旧版本不会清空或覆盖。

CSV 以追加模式写入；JSON 保持标准数组格式，每次只在数组结束符前追加新对象。
第一次启用去重时会从已有 JSON 结果建立索引。为避免跨进程同时追加造成文件冲突，
同一网站同一时间只运行一个 Scrapy 进程。

## 固定代理与天启备用模式

默认模式为 `static`，会强制使用 `STATIC_PROXY_ENDPOINT` 和上述环境变量中的凭据。
凭据缺失、认证失败或代理网络重试耗尽时会停止 Spider，不会回退服务器真实 IP。

华新明确禁止 `CRAWLER_OUTBOUND_MODE=direct`；即使手动传入也会在初始化阶段报错，
不会发送任何目标请求。固定代理设置为单域名并发 1、基础延迟 3 秒、随机延迟、
AutoThrottle 目标并发 0.5、最多重试 1 次。收到 403/429 时按 30～300 秒提高下载槽
延迟；同一域名连续 2 次或累计 4 次限制响应时主动关闭 Spider。默认单次最多接收
300 个响应。

需要临时恢复天启代理时：

```bash
export TIANQI_SECRET='...'
export TIANQI_SIGN='...'
scrapy crawl huaxin -a max_records=5 -a page_size=5 \
  -s CRAWLER_OUTBOUND_MODE=tianqi \
  -s TIANQI_PROXY_API_CALL_LIMIT=1
```
