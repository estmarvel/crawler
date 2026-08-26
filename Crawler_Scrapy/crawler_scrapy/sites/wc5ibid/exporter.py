"""旺采网按源栏目和实际Schema分别输出。"""

from crawler_scrapy.sites.sxxindian.exporter import SxxindianMultiFormatPipeline


class Wc5ibidMultiFormatPipeline(SxxindianMultiFormatPipeline):
    PREFIX = "__wc5ibid__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        payload = route.removeprefix(cls.PREFIX)
        category, schema_type = payload.split("|", 1)
        safe_category = category.replace("/", "及").replace("\\", "及")
        return f"旺采网_{safe_category}_{schema_type}", schema_type

    def process_item(self, item):
        from itemadapter import ItemAdapter
        from crawler_scrapy.schemas.notice_fields import normalize_notice_type

        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if not subtype.startswith("wc5ibid|"):
            return item
        _, category = subtype.split("|", 1)
        schema_type = normalize_notice_type(adapter.get("notice_type"))
        route = self._route(f"{category}|{schema_type}")
        record = self._build_record(adapter, schema_type)
        writer = self._get_csv_writer(route)
        self._get_json_path(route)
        self._append_json_record(route, record)
        writer.writerow({key: self._serialize_csv(value) for key, value in record.items()})
        self._csv_files[route].flush()
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
        self.crawler.stats.inc_value(f"export/wc5ibid/{category}/{schema_type}")
        return item
