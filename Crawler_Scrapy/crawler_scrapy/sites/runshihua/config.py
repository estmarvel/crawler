"""润世和电子招投标交易平台公开公告接口配置。"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode


PLATFORM_NAME = "润世和电子招投标交易平台"
PLATFORM_CODE = "runshihua"
SOURCE_PLATFORM_CODE = "100001"
WEB_BASE_URL = "https://ec.runshihua.com"
WEB_HOME_URL = f"{WEB_BASE_URL}/web/home"
API_BASE_URL = f"{WEB_BASE_URL}/spi/cms"
FILE_BASE_URL = "https://file.runshihua.com/files/c"


FAMILIES = {
    "notice": {
        "list_endpoint": "/cmsNotice/getNoticeList",
        "detail_endpoint": "/cmsNotice/getNotice",
    },
    "candidate": {
        "list_endpoint": "/candidate/cmsList",
        "detail_endpoint": "/candidate/queryCandidate",
    },
    "other": {
        "list_endpoint": "/cancellation/getNoticeList",
        "detail_endpoint": "/cancellation/getNotice",
    },
}


CATEGORIES = {
    "prequalification": {"family": "notice", "schema": "资格预审公告", "label": "资格预审公告"},
    "tender": {"family": "notice", "schema": "招标公告", "label": "招标公告"},
    "purchase": {"family": "notice", "schema": "招标公告", "label": "采购公告"},
    "prequalification_change": {"family": "notice", "schema": "更正结果公示", "label": "资格预审变更公告"},
    "tender_change": {"family": "notice", "schema": "更正结果公示", "label": "招标变更公告"},
    "purchase_change": {"family": "notice", "schema": "更正结果公示", "label": "采购变更公告"},
    "candidate": {"family": "candidate", "schema": "中标候选人公示", "label": "中标候选人公示"},
    "award": {"family": "candidate", "schema": "中标结果公示", "label": "中标结果公示"},
    "candidate_correction": {"family": "candidate", "schema": "更正结果公示", "label": "中标候选人公示更正"},
    "award_correction": {"family": "candidate", "schema": "更正结果公示", "label": "中标结果公示更正"},
    "control_price": {"family": "other", "schema": "更正结果公示", "label": "控制价公告"},
    "control_price_change": {"family": "other", "schema": "更正结果公示", "label": "控制价变更公告"},
    "cancellation": {"family": "other", "schema": "更正结果公示", "label": "撤销公告"},
    "supplement": {"family": "other", "schema": "更正结果公示", "label": "补充公告"},
    "delay": {"family": "other", "schema": "更正结果公示", "label": "延期公告"},
}
DEFAULT_CATEGORIES = tuple(CATEGORIES)


NOTICE_TYPE_CATEGORIES = {
    "prequalification": "prequalification",
    "bidding": "tender",
    "purchase": "purchase",
    "alteration": "prequalification_change",
    "biddingAlteration": "tender_change",
    "alterationPurchase": "purchase_change",
}
CANDIDATE_TYPE_CATEGORIES = {
    "0": "candidate",
    "1": "award",
    "2": "candidate_correction",
    "3": "award_correction",
}
OTHER_TYPE_CATEGORIES = {
    "controlPrice": "control_price",
    "alterationControlPrice": "control_price_change",
    "cancellation": "cancellation",
    "supplement": "supplement",
    "delay": "delay",
}


def endpoint(family: str, kind: str) -> str:
    try:
        return f"{API_BASE_URL}{FAMILIES[family][f'{kind}_endpoint']}"
    except KeyError as exc:
        raise ValueError(f"未知润世和接口：family={family!r}, kind={kind!r}") from exc


def category_for_record(family: str, record: Mapping[str, object]) -> str:
    if family == "notice":
        return NOTICE_TYPE_CATEGORIES.get(str(record.get("noticeType") or ""), "")
    if family == "candidate":
        return CANDIDATE_TYPE_CATEGORIES.get(str(record.get("candidateType") or ""), "")
    if family == "other":
        return OTHER_TYPE_CATEGORIES.get(str(record.get("noticeType") or ""), "")
    return ""


def source_notice_id(family: str, notice_id: str | int) -> str:
    value = str(notice_id).strip()
    if family not in FAMILIES or not value:
        raise ValueError(f"无效的润世和公告身份：family={family!r}, id={value!r}")
    return f"{family}:{value}"


def list_payload(family: str, page: int, page_size: int, keyword: str = "") -> dict[str, object]:
    filters: dict[str, object] = {}
    if family == "notice":
        filters = {"noticeName": keyword, "remark": ""}
    elif family == "candidate":
        # 不限定 candidateType，避免漏掉 2/3 两种更正公示。
        filters = {"candidateType": "", "sectionName": keyword, "remark": ""}
    elif family == "other":
        filters = {"noticeName": keyword, "gcjsPublicityContent": ""}
    else:
        raise ValueError(f"未知润世和列表族：{family}")
    return {
        "data": filters,
        "size": min(max(int(page_size), 1), 400),
        "pages": max(int(page), 1),
        "platformCode": SOURCE_PLATFORM_CODE,
    }


def detail_payload(family: str, record: Mapping[str, object]) -> dict[str, object]:
    notice_id = str(record.get("id") or "").strip()
    if not notice_id:
        raise ValueError("润世和列表记录缺少 id")
    if family == "notice":
        values = {"id": notice_id, "sectionId": record.get("sectionId")}
    elif family == "candidate":
        values = {
            "id": notice_id,
            "sectionId": record.get("sectionId"),
            "candidateType": str(record.get("candidateType") or ""),
        }
    elif family == "other":
        values = {"id": notice_id}
    else:
        raise ValueError(f"未知润世和详情族：{family}")
    return {"data": values, "platformCode": SOURCE_PLATFORM_CODE}


def detail_page_url(family: str, record: Mapping[str, object]) -> str:
    values: dict[str, object] = {"id": record.get("id")}
    if family == "notice":
        values.update({"t": 1, "stId": record.get("sectionId")})
    elif family == "candidate":
        values.update({
            "t": 2,
            "stId": record.get("sectionId"),
            "type": record.get("candidateType"),
        })
    elif family == "other":
        values.update({"t": 3})
    else:
        raise ValueError(f"未知润世和详情页族：{family}")
    return f"{WEB_BASE_URL}/web/pdfDetail?{urlencode(values)}"


def absolute_pdf_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    return f"{FILE_BASE_URL}/{text.lstrip('/')}"
