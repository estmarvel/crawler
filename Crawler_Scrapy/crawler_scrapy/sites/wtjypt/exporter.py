"""伟拓按项目模块、公告类别和工程/货物/服务分别导出。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline


class WtjyptMultiFormatPipeline(SxjmMultiFormatPipeline):
    PREFIX = "__wtjypt__"

    @classmethod
    def _route(cls, value: str) -> str:
        return f"{cls.PREFIX}{value}"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        module, category, project_type, schema_type = route.removeprefix(cls.PREFIX).split("|", 3)
        pieces = ["伟拓", module, category]
        if project_type != "全部":
            pieces.append(project_type)
        if category in {"招标公告", "采购公告"} and schema_type not in {"招标公告", "资格预审公告"}:
            pieces.append(schema_type)
        return "_".join(pieces), schema_type

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if not subtype.startswith("wtjypt|"):
            return item
        _, module, category, project_type = subtype.split("|", 3)
        schema_type = normalize_notice_type(adapter.get("notice_type"))
        route = self._route("|".join((module, category, project_type, schema_type)))
        record = self._build_record(adapter, schema_type)
        writer = self._get_csv_writer(route)
        self._get_json_path(route)
        self._append_json_record(route, record)
        writer.writerow({k: self._serialize_csv(v) for k, v in record.items()})
        self._csv_files[route].flush()
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
        self.crawler.stats.inc_value(f"export/wtjypt/{module}/{category}/{project_type}")
        return item
