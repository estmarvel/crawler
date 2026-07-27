"""山西焦煤专用导出器：按网站四个栏目分别命名输出。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from itemadapter import ItemAdapter

from crawler_scrapy.pipelines import NoticeMultiFormatPipeline
from crawler_scrapy.schemas.notice_fields import normalize_notice_type


class SxjmMultiFormatPipeline(NoticeMultiFormatPipeline):
    """复用公共导出格式，文件名使用“招标项目_栏目名称”。"""

    ROUTES = {
        "yfxm.zbjh": ("依法项目_招标计划", "招标计划"),
        "yfxm.zbgg": ("依法项目_招标（预审）公告", "招标公告"),
        "yfxm.hxr": ("依法项目_中标候选人公示", "中标候选人公示"),
        "yfxm.zbjg": ("依法项目_结果公告", "中标结果公示"),
        "yfxm.zzgg": ("依法项目_终止公告", "招标公告"),
        "zbxm.zbgg": ("招标项目_招标（预审）公告", "招标公告"),
        "zbxm.hxr": ("招标项目_中标候选人公示", "中标候选人公示"),
        "zbxm.zbjg": ("招标项目_中标公告", "中标结果公示"),
        # 公共框架没有终止公告 Schema，因此复用招标公告字段结构。
        "zbxm.zzgg": ("招标项目_终止公告", "招标公告"),
        "fzxm.cggg": ("非招项目_采购（预审）公告", "招标公告"),
        "fzxm.cjhxr": ("非招项目_成交候选人公示", "中标候选人公示"),
        "fzxm.cjgg": ("非招项目_成交公告", "中标结果公示"),
        "fzxm.zzgg": ("非招项目_终止公告", "招标公告"),
        "jycg.cggg": ("简易采购限额以下_采购公告", "招标公告"),
        "jycg.zzgg": ("简易采购限额以下_终止公告", "招标公告"),
        "jycg.cjgg": ("简易采购限额以下_成交公告", "中标结果公示"),
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__sxjm_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        subtype = route.removeprefix("__sxjm_").removesuffix("__")
        try:
            return cls.ROUTES[subtype]
        except KeyError as exc:
            raise ValueError(f"未知山西焦煤导出路由：{route}") from exc

    def _fieldnames(self, notice_type: str) -> list[str]:
        _, schema_type = self._route_config(notice_type)
        return super()._fieldnames(schema_type)

    def _get_csv_writer(self, notice_type: str) -> csv.DictWriter:
        if notice_type in self._csv_writers:
            return self._csv_writers[notice_type]
        if self.csv_dir is None:
            raise RuntimeError("CSV目录尚未初始化")

        basename, _ = self._route_config(notice_type)
        path = self.csv_dir / f"{basename}.csv"
        fieldnames = self._fieldnames(notice_type)
        has_content = path.exists() and path.stat().st_size > 0
        if has_content:
            with path.open("r", encoding="utf-8-sig", newline="") as existing:
                if next(csv.reader(existing), []) != fieldnames:
                    raise RuntimeError(f"现有CSV表头与当前Schema不一致，拒绝追加：{path}")
        file_object = path.open(
            "a", encoding="utf-8" if has_content else "utf-8-sig", newline=""
        )
        writer = csv.DictWriter(file_object, fieldnames=fieldnames, extrasaction="ignore")
        if not has_content:
            writer.writeheader()
            file_object.flush()
        self._csv_files[notice_type] = file_object
        self._csv_writers[notice_type] = writer
        return writer

    def _get_json_path(self, notice_type: str) -> Path:
        if notice_type in self._json_paths:
            return self._json_paths[notice_type]
        if self.json_dir is None:
            raise RuntimeError("JSON目录尚未初始化")

        basename, _ = self._route_config(notice_type)
        path = self.json_dir / f"{basename}.json"
        if path.exists() and path.stat().st_size > 0:
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"现有JSON无法读取，拒绝覆盖：{path}: {exc}") from exc
            if not isinstance(rows, list):
                raise RuntimeError(f"现有JSON不是数组，拒绝覆盖：{path}")
            has_records = bool(rows)
        else:
            path.write_text("[\n]\n", encoding="utf-8")
            has_records = False
        self._json_paths[notice_type] = path
        self._json_has_records[notice_type] = has_records
        return path

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if subtype not in self.ROUTES:
            return super().process_item(item)

        route = self._route(subtype)
        _, schema_type = self.ROUTES[subtype]
        normalized_type = normalize_notice_type(adapter.get("notice_type"))
        is_termination = subtype.endswith(".zzgg")
        if not is_termination and normalized_type != schema_type:
            raise ValueError(
                f"公告子类型与Schema不一致：subtype={subtype} type={normalized_type}"
            )
        record = self._build_record(adapter, schema_type)
        if self.include_meta and is_termination:
            record["公告类型"] = "TERMINATION"

        csv_writer = self._get_csv_writer(route)
        self._get_json_path(route)
        csv_row = {key: self._serialize_csv(value) for key, value in record.items()}
        self._append_json_record(route, record)
        csv_writer.writerow(csv_row)
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
        self.crawler.stats.inc_value(f"export/sxjm/{subtype}")
        return item
