"""太原交易中心按大类和源栏目分别导出。"""

from itemadapter import ItemAdapter
from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.wtjypt.exporter import WtjyptMultiFormatPipeline


class TyggzyMultiFormatPipeline(WtjyptMultiFormatPipeline):
    PREFIX = "__tyggzy__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        module, category, _, schema = route.removeprefix(cls.PREFIX).split("|", 3)
        return f"太原公共资源_{module}_{category}", schema

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if not subtype.startswith("tyggzy|"):
            return item
        _, module, category = subtype.split("|", 2)
        schema = normalize_notice_type(adapter.get("notice_type"))
        route = self._route("|".join((module, category, "全部", schema)))
        record = self._build_record(adapter, schema)
        writer = self._get_csv_writer(route); self._get_json_path(route)
        self._append_json_record(route, record)
        writer.writerow({k: self._serialize_csv(v) for k, v in record.items()}); self._csv_files[route].flush()
        field_meta = dict(adapter.get("field_meta") or {})
        dedup = field_meta.get("_dedup") or {}
        store = getattr(self.crawler, "_notice_dedup_stores", {}).get(str(adapter.get("platform_code") or ""))
        if store is not None and dedup:
            store.commit(
                identity=dedup["identity"], content_fingerprint=dedup["content_fingerprint"],
                platform_code=str(adapter.get("platform_code") or ""), notice_id=str(adapter.get("notice_id") or ""),
                detail_url=str(adapter.get("detail_url") or ""), list_fingerprint=str(dedup.get("list_fingerprint") or ""),
            )
        self.crawler.stats.inc_value("export/appended_versions")
        self.crawler.stats.inc_value(f"export/tyggzy/{module}/{category}")
        return item
