"""不访问网络的字段、快照和多格式输出自检 Spider。

运行：
    scrapy crawl schema_demo

结果：
    output/schema_demo/
    ├── csv/
    ├── json/
    └── snapshots/
"""

from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_TYPES, get_notice_fields
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SchemaDemoSpider(BaseNoticeSpider):
    name = "schema_demo"
    platform_name = "字段框架自检"
    platform_code = "schema_demo"

    custom_settings = {
        # 自检时为八种类型都生成一条数据。
        "NOTICE_EXPORT_EMPTY_FILES": False,
        "NOTICE_SNAPSHOT_REQUIRED": True,
    }

    async def start(self):
        for index, notice_type in enumerate(ANNOUNCEMENT_TYPES, start=1):
            fields = get_notice_fields(notice_type)
            example_data = {}

            if fields:
                example_data[fields[0]] = "示例值"
            if "项目名称" in fields:
                example_data["项目名称"] = f"{notice_type}示例项目"
            if "发布网站" in fields:
                example_data["发布网站"] = "字段框架自检"

            html = f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{notice_type}字段自检</title></head>
<body>
<h1>{notice_type}字段自检</h1>
<p>这是第 {index} 条HTML快照示例。</p>
</body>
</html>
"""

            yield self.build_notice_item(
                notice_type=notice_type,
                notice_id=f"demo-{index:02d}",
                title=f"{notice_type}字段自检",
                publish_time="2026-07-10 00:00:00",
                detail_url=f"https://example.invalid/detail/{index}",
                data=example_data,
                raw_data={"demo": True, "index": index},
                raw_html=html,
                attachments=[
                    {
                        "name": f"{notice_type}示例附件.pdf",
                        "url": f"https://example.invalid/files/{index}.pdf",
                        "file_id": f"file-{index:02d}",
                    }
                ],
            )