"""千极链按公告类型和工程/货物/服务独立导出。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.bitbid.exporter import BitbidMultiFormatPipeline


class QianjiMultiFormatPipeline(BitbidMultiFormatPipeline):
    ROUTES = {
        "plan.all": ("千极链_招标计划_全部", "招标计划"),
        "tender.engineering": ("千极链_招标公告_工程", "招标公告"),
        "tender.goods": ("千极链_招标公告_货物", "招标公告"),
        "tender.service": ("千极链_招标公告_服务", "招标公告"),
        "change.engineering": ("千极链_变更公告_工程", "招标公告"),
        "change.goods": ("千极链_变更公告_货物", "招标公告"),
        "change.service": ("千极链_变更公告_服务", "招标公告"),
        "candidate.engineering": ("千极链_中标候选人公示_工程", "中标候选人公示"),
        "candidate.goods": ("千极链_中标候选人公示_货物", "中标候选人公示"),
        "candidate.service": ("千极链_中标候选人公示_服务", "中标候选人公示"),
        "award.engineering": ("千极链_结果公示_工程", "中标结果公示"),
        "award.goods": ("千极链_结果公示_货物", "中标结果公示"),
        "award.service": ("千极链_结果公示_服务", "中标结果公示"),
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__qianji_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        subtype = route.removeprefix("__qianji_").removesuffix("__")
        try:
            return cls.ROUTES[subtype]
        except KeyError as exc:
            raise ValueError(f"未知千极链导出路由：{route}") from exc

    @staticmethod
    def _is_termination(subtype: str, title: str) -> bool:
        return subtype.startswith("change.") and any(
            word in title for word in ("废标", "流标", "终止", "撤销", "暂停")
        )

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
                f"千极链栏目与Schema不一致：subtype={subtype} type={actual_type}"
            )
        record = self._build_record(adapter, schema_type)
        if self._is_termination(subtype, str(adapter.get("title") or "")):
            # 变更栏目混有流标、撤销、暂停和终止公告；字段形状继续使用
            # 招标公告 Schema，数据库生命周期编码保存为 TERMINATION。
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
        self.crawler.stats.inc_value(f"export/qianji/{subtype}")
        return item
