# 山西公共资源交易平台 Scrapy 爬虫框架 v3

本版本在 v2 基础上补充：

```text
AI 兜底字段补全 + PDF 文字提取/OCR + 高并发代理池利用
```

不包含“全量历史搜索回填”。当前策略是：抓当天/指定时间范围内当前公告，规则解析后对缺失字段调用 AI 兜底；如果详情页是 PDF，则先直接提取 PDF 文字，文字过少时再调用 qwen-vl-ocr。

## 输出字段

继续保持旧版 v4.3 的 24 个字段：

```text
公告类型, 项目名称, 所属行业, 组织形式, 开标时间, 标书发售时间, 公告内容,
招标人, 招标人地址, 招标人联系人, 招标人联系方式,
招标代理机构, 招标代理机构地址, 招标代理机构联系人, 招标代理机构联系方式,
监督部门, 监督部门地址, 监督部门联系人, 监督部门联系方式,
依据文件, 依据文号, 发布日期, 发布网站, 公告历史
```

## 安装

```bash
cd /home/intsig/zwx/sxbid_scrapy_framework
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 配置

```bash
cp .env.example .env
vim .env
```

至少配置：

```env
ENABLE_AI=true
ENABLE_PDF_OCR=true
DMX_API_KEY=你的DMX密钥
```

使用旧项目天启代理池：

```env
USE_PROXY=true
PROXY_PROVIDER=legacy
LEGACY_PROXY_POOL_PATH=/home/intsig/zwx/sxbid/proxy_pool.py
PROXY_DIRECT_FALLBACK=false
PROXY_NUM=30
PROXY_TIME=5
PROXY_PORT=2
PROXY_MIN_SIZE=8
PROXY_MAX_FAIL_COUNT=1
CRAWLER_CONCURRENT_REQUESTS=32
CRAWLER_CONCURRENT_REQUESTS_PER_DOMAIN=16
```

## 运行

```bash
scrapy crawl sxbid_notice -a crawl_days=1 -a max_pages=3
```

输出：

```text
output/山西.csv
output/山西.jsonl
logs/YYYYMMDD_ai_report.log
```
