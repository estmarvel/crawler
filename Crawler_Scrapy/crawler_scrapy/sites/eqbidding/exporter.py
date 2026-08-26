"""云买卖三类公告分目录导出。"""

from itemadapter import ItemAdapter
from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline


class EqbiddingMultiFormatPipeline(SxjmMultiFormatPipeline):
    PREFIX = "__eqbidding__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        category, schema = route.removeprefix(cls.PREFIX).split("|", 1)
        return f"云买卖_{category}", schema

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if not subtype.startswith("eqbidding|"):
            return item
        category = subtype.split("|", 1)[1]
        schema = normalize_notice_type(adapter.get("notice_type"))
        route = f"{self.PREFIX}{category}|{schema}"
        record = self._build_record(adapter, schema)
        writer = self._get_csv_writer(route); self._get_json_path(route)
        self._append_json_record(route, record)
        writer.writerow({k: self._serialize_csv(v) for k, v in record.items()}); self._csv_files[route].flush()
        self.crawler.stats.inc_value("export/appended_versions")
        self.crawler.stats.inc_value(f"export/eqbidding/{category}")
        return item
