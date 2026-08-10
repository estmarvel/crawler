"""山西交控按来源栏目和纠正后的实际公告类型独立导出。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline


class SxjkzcptMultiFormatPipeline(SxjmMultiFormatPipeline):
    ROUTES = {
        "zbcg.plan": ("依法必须招标_采购计划", "招标计划"),
        "zbcg.tender": ("依法必须招标_招标公告", "招标公告"),
        "zbcg.change": ("依法必须招标_变更公告", "招标公告"),
        "zbcg.termination": ("依法必须招标_终止公告", "招标公告"),
        "zbcg.correction": ("依法必须招标_更正结果公示", "更正结果公示"),
        "zbcg.candidate": ("依法必须招标_中标候选人公示", "中标候选人公示"),
        "zbcg.award": ("依法必须招标_结果公告", "中标结果公示"),
        "zbcg.contract": ("依法必须招标_合同订立信息", "合同与履约"),
        "qzbcg.tender": ("其他必须招标_招标公告", "招标公告"),
        "qzbcg.change": ("其他必须招标_变更公告", "招标公告"),
        "qzbcg.termination": ("其他必须招标_终止公告", "招标公告"),
        "qzbcg.correction": ("其他必须招标_更正结果公示", "更正结果公示"),
        "qzbcg.candidate": ("其他必须招标_中标候选人公示", "中标候选人公示"),
        "qzbcg.award": ("其他必须招标_结果公告", "中标结果公示"),
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__sxjkzcpt_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        subtype = route.removeprefix("__sxjkzcpt_").removesuffix("__")
        try:
            return cls.ROUTES[subtype]
        except KeyError as exc:
            raise ValueError(f"未知山西交控导出路由：{route}") from exc

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
                f"山西交控栏目与Schema不一致：subtype={subtype} type={actual_type}"
            )
        record = self._build_record(adapter, schema_type)
        if subtype.endswith(".termination"):
            # 数据库生命周期编码使用 TERMINATION，字段形状仍复用招标公告。
            record["公告类型"] = "TERMINATION"
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
        self.crawler.stats.inc_value(f"export/sxjkzcpt/{subtype}")
        return item
