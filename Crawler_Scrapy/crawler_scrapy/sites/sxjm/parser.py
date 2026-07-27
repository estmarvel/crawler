"""山西焦煤接口解密及公告字段适配。"""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping
from urllib.parse import urljoin

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from crawler_scrapy.sites.huaxin.parser import HuaxinParser, clean_html_keep_lines
from crawler_scrapy.sites.sxjm import config


class SxjmResponseError(ValueError):
    """接口返回失败或加密载荷不合法。"""


def decrypt_envelope(payload: Mapping[str, Any]) -> Any:
    """解开前端所用 AES-128-CBC 响应载荷并返回 JSON 数据。"""

    if payload.get("errcode") not in (0, "0", None):
        raise SxjmResponseError(str(payload.get("errmsg") or "接口返回失败"))
    encrypted = payload.get("result")
    if not isinstance(encrypted, str) or not encrypted.strip():
        raise SxjmResponseError("接口响应缺少加密 result")
    try:
        ciphertext = base64.b64decode(encrypted)
        plaintext = unpad(
            AES.new(config.AES_KEY, AES.MODE_CBC, config.AES_IV).decrypt(ciphertext),
            AES.block_size,
        )
        return json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SxjmResponseError("山西焦煤接口响应解密失败") from exc


class _FrameworkParser(HuaxinParser):
    """复用 sjq 框架现有的八类公告规则解析能力。"""

    platform_name = config.PLATFORM_NAME
    web_base_url = config.WEB_BASE_URL


class SxjmParser:
    """把本站字段转换为 sjq 框架统一公告结构。"""

    SECTION_TO_FRAMEWORK = {
        "zbjh": "zbjh",
        "zbgg": "zbgg_zys",
        "cggg": "zbgg_zys",
        "hxr": "hxr",
        "cjhxr": "hxr",
        "zbjg": "gs",
        "cjgg": "gs",
        # 框架没有单独的终止类型，按招标公告保存，并保留源站公告性质。
        "zzgg": "zbgg_zys",
    }
    PROJECT_TYPES = {"10": "货物", "20": "工程", "30": "服务"}

    @classmethod
    def parse(
        cls, channel: str, section: str, detail: Mapping[str, Any]
    ) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
        adapted = cls._adapt_detail(channel, section, detail)
        # 首页“结果公告/成交公告”按接口栏目归档。少量历史记录标题带“更正”，
        # 仍应留在该栏目文件中，并使用结果公告的统一 Schema。
        original_title = str(adapted.get("annTitle") or "")
        if section in {"zbjg", "cjgg"}:
            adapted["annTitle"] = original_title.replace("更正", "变更")
        subtype, notice_type, data, _ = _FrameworkParser.parse(
            cls.SECTION_TO_FRAMEWORK[section], adapted
        )
        attachments = cls.attachments(detail)
        if original_title and "公告标题" in data:
            data["公告标题"] = original_title
        if "附件" in data:
            data["附件"] = attachments
        if "源站公告性质" in data:
            data["源站公告性质"] = config.section_label(channel, section)
        if "项目性质" in data:
            data["项目性质"] = config.channel_label(channel)
        if "发布网站" in data:
            data["发布网站"] = config.PLATFORM_NAME
        return subtype, notice_type, data, attachments

    @classmethod
    def _adapt_detail(
        cls, channel: str, section: str, detail: Mapping[str, Any]
    ) -> dict[str, Any]:
        project_type = str(detail.get("project_type") or "")
        classification = {
            "zbjh": 1, "zbgg": 1, "cggg": 1, "zzgg": 1,
            "hxr": 2, "cjhxr": 2, "zbjg": 3, "cjgg": 3,
        }[section]
        return {
            **dict(detail),
            "annId": detail.get("id"),
            "annTitle": detail.get("title") or detail.get("project_name"),
            "annClassification": classification,
            "annNature": 5 if section == "zzgg" else 1,
            "announcementType": 1,
            "annContent": detail.get("content") or "",
            "releaseTime": detail.get("publish_time_format") or detail.get("publish_time"),
            "createTime": detail.get("created_at_format") or detail.get("created_at"),
            "projectNatureName": config.channel_label(channel),
            "projectName": detail.get("project_name") or detail.get("title"),
            "industryName": detail.get("industry_category"),
            "submitDeadline": detail.get("bid_opening_date_format"),
            "tenderProjectCode": detail.get("tender_number") or detail.get("code"),
            "projectCode": detail.get("code") or detail.get("tender_number"),
            "classificationName": cls.PROJECT_TYPES.get(project_type, project_type),
            "administrativeName": detail.get("region") or detail.get("project_address"),
            "companyName": detail.get("tendering_agency"),
            "acquisitionStart": detail.get("sale_begin_time_format"),
            "acquisitionEnd": detail.get("sale_end_time_format"),
            "publicityStart": detail.get("sale_begin_time_format"),
            "publicityEnd": detail.get("sale_end_time_format"),
        }

    @staticmethod
    def attachments(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for document in detail.get("document") or []:
            if not isinstance(document, Mapping):
                continue
            path = str(document.get("path") or "").strip()
            result.append(
                {
                    "source_file_id": str(document.get("id") or ""),
                    "file_name": str(document.get("original_name") or ""),
                    "file_url": urljoin(f"{config.WEB_BASE_URL}/", path) if path else "",
                    "file_type": str(document.get("mime_type") or ""),
                    "parse_status": "PENDING",
                }
            )
        return result

    @staticmethod
    def raw_html(detail: Mapping[str, Any]) -> str:
        return str(detail.get("content") or "")

    @classmethod
    def raw_text(cls, detail: Mapping[str, Any]) -> str:
        return clean_html_keep_lines(cls.raw_html(detail))
