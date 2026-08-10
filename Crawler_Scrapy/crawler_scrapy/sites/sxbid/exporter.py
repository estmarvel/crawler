"""山西招投标网八类公告独立导出。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline


class SxbidMultiFormatPipeline(SxjmMultiFormatPipeline):
    ROUTES = {
        "plan": ("山西招投标网_招标计划", "招标计划"),
        "prequalification": ("山西招投标网_资格预审公告", "资格预审公告"),
        "tender": ("山西招投标网_招标公告", "招标公告"),
        "candidate": ("山西招投标网_中标候选人公示", "中标候选人公示"),
        "final_candidate": ("山西招投标网_定标候选人公示", "定标候选人公示"),
        "award": ("山西招投标网_中标结果公示", "中标结果公示"),
        "correction": ("山西招投标网_更正公告公示", "更正结果公示"),
        "contract": ("山西招投标网_合同和履约", "合同与履约"),
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__sxbid_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        subtype = route.removeprefix("__sxbid_").removesuffix("__")
        try:
            return cls.ROUTES[subtype]
        except KeyError as exc:
            raise ValueError(f"未知山西招投标网导出路由：{route}") from exc

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if subtype not in self.ROUTES:
            return item
        route = self._route(subtype)
        _, schema_type = self.ROUTES[subtype]
        actual_type = normalize_notice_type(adapter.get("notice_type"))
        if actual_type != schema_type:
            raise ValueError(
                f"山西招投标网栏目与Schema不一致：subtype={subtype} type={actual_type}"
            )
        record = self._build_record(adapter, schema_type)
        json_record = self._build_json_record(adapter, schema_type, record)
        csv_writer = self._get_csv_writer(route)
        self._get_json_path(route)
        self._append_json_record(route, json_record)
        csv_writer.writerow(
            {key: self._serialize_csv(value) for key, value in record.items()}
        )
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
        self.crawler.stats.inc_value(f"export/sxbid/{subtype}")
        return item
