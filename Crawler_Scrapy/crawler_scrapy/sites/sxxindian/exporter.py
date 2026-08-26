"""山西新点专用导出器：按业务模块、公告栏目和细分类型分别保存。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline


class SxxindianMultiFormatPipeline(SxjmMultiFormatPipeline):
    PREFIX = "__sxxindian__"

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"{cls.PREFIX}{subtype}"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        payload = route.removeprefix(cls.PREFIX)
        parts = payload.split("|")
        if len(parts) != 4:
            raise ValueError(f"未知山西新点导出路由：{route}")
        module, category, detail_type, schema_type = parts
        pieces = ["山西新点", module, category, detail_type]
        # “其他公告”栏目实际混放多种业务公告，加入Schema类型避免不同列结构写入同一文件。
        if category == "其他公告":
            pieces.append(schema_type)
        filename = "_".join(x for x in pieces if x and x != "全部")
        return filename, schema_type

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if not subtype.startswith("sxxindian|"):
            return item
        _, module, category, detail_type = subtype.split("|", 3)
        schema_type = normalize_notice_type(adapter.get("notice_type"))
        route = self._route("|".join((module, category, detail_type, schema_type)))
        record = self._build_record(adapter, schema_type)
        # JSON 使用统一框架的紧凑溯源结构；正式 AI 任务关闭 CSV 输出。
        json_record = self._build_json_record(adapter, schema_type, record)
        self._write_formats(route, record, json_record)

        field_meta = dict(adapter.get("field_meta") or {})
        dedup = field_meta.get("_dedup") or {}
        store = getattr(self.crawler, "_notice_dedup_stores", {}).get(
            str(adapter.get("platform_code") or self.crawler.spider.platform_code)
        )
        if store is not None and dedup:
            store.commit(
                identity=dedup["identity"],
                content_fingerprint=dedup["content_fingerprint"],
                platform_code=str(adapter.get("platform_code") or ""),
                notice_id=str(adapter.get("notice_id") or ""),
                detail_url=str(adapter.get("detail_url") or ""),
                list_fingerprint=str(dedup.get("list_fingerprint") or ""),
            )
        self.crawler.stats.inc_value("export/appended_versions")
        self.crawler.stats.inc_value(f"export/sxxindian/{module}/{category}/{detail_type}")
        return item
