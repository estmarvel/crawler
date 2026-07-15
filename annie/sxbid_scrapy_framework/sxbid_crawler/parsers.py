import re
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from sxbid_crawler.columns import DEFAULT_VALUE

try:
    from sxbid_crawler.columns import SITE_NAME
except Exception:
    SITE_NAME = "山西省招标投标公共服务平台"


SKIP_KEYWORDS = [
    "招标控制价变更", "招标控制价", "控制价变更", "控制价",
    "最高投标限价", "最高限价", "投标限价",
]
TERMINATE_KEYWORDS = ["终止公告", "废标公告", "终止招标", "撤销公告", "流标公告"]
AMENDMENT_KEYWORDS = ["变更公告", "更正公告", "变更公示", "更正公示", "澄清公告"]
NOTICE_KEYWORDS = ["资格预审公告", "招标公告", "二次招标公告", "重新招标公告"]
CANDIDATE_KEYWORDS = ["中标候选人公示", "定标候选人公示"]
RESULT_KEYWORDS = ["中标结果公示", "中标结果公告"]


SECTION_TITLES = {
    "项目", "招标项目", "标段（包）", "标段(包)",
    "招标文件/招标文件澄清与修改", "资格预审", "开标", "评标", "中标",
    "合同履约", "异常信息", "折叠/展开",
}


KNOWN_LABELS = {
    "项目编号",
    "项目名称",
    "项目所在行政区域",
    "项目行业分类",
    "所属行业",
    "项目审批文件名称",
    "项目审批文号",
    "项目审批单位",
    "项目建立时间",
    "资金来源",
    "出资比例",
    "项目规模",
    "招标项目编号",
    "招标项目名称",
    "招标人名称",
    "招标代理机构名称",
    "招标方式",
    "招标项目类型",
    "招标组织形式",
    "监督部门名称",
    "招标内容与范围及招标方案说明",
    "标段(包)编号",
    "标段（包）编号",
    "标段(包)名称",
    "标段（包）名称",
    "标段(包)分类代码",
    "标段（包）分类代码",
    "标段合同估算价",
    "标段(包)内容",
    "标段（包）内容",
    "实施地",
    "依据文件",
    "依据文号",
    "组织形式",
    "开标时间",
    "标书发售时间",
    "发布日期",
    "来源",
    "浏览次数",
}


FIELD_ALIASES = {
    "项目名称": "项目名称",
    "招标项目名称": "项目名称",

    "项目行业分类": "所属行业",
    "所属行业": "所属行业",

    "招标组织形式": "组织形式",
    "组织形式": "组织形式",

    "开标时间": "开标时间",
    "标书发售时间": "标书发售时间",

    "招标内容与范围及招标方案说明": "公告内容",
    "标段(包)内容": "公告内容",
    "标段（包）内容": "公告内容",
    "项目规模": "公告内容",

    "招标人名称": "招标人",
    "招标代理机构名称": "招标代理机构",
    "监督部门名称": "监督部门",

    "项目审批文件名称": "依据文件",
    "依据文件": "依据文件",

    "项目审批文号": "依据文号",
    "依据文号": "依据文号",

    "发布日期": "发布日期",
    "来源": "发布网站",
}


def clean_text(value: str) -> str:
    if value is None:
        return ""
    value = unquote(str(value))
    value = value.replace("\xa0", " ")
    value = value.replace("\u3000", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip(" \n\t:：")


def normalize_label(label: str) -> str:
    label = clean_text(label)
    label = label.replace("（", "(").replace("）", ")")
    label = label.rstrip(":：")
    return label.strip()


def classify_title(raw_title: str) -> str:
    raw_title = raw_title or ""
    for kw in SKIP_KEYWORDS:
        if kw in raw_title:
            return "skip"
    for kw in TERMINATE_KEYWORDS:
        if kw in raw_title:
            return "terminate"
    for kw in AMENDMENT_KEYWORDS:
        if kw in raw_title:
            return "amendment"
    for kw in RESULT_KEYWORDS:
        if kw in raw_title:
            return "result"
    for kw in CANDIDATE_KEYWORDS:
        if kw in raw_title:
            return "candidate"
    for kw in NOTICE_KEYWORDS:
        if kw in raw_title:
            return "notice"
    return "unknown"


def type_label(raw_title: str, forced_type: str = "") -> str:
    notice_type = forced_type or classify_title(raw_title)
    labels = {
        "notice": "招标公告",
        "candidate": "中标候选人公示",
        "result": "中标结果公示",
        "amendment": "更正公告",
        "terminate": "废标公示",
        "skip": "其他公告",
        "unknown": "其他公告",
    }
    return labels.get(notice_type, "其他公告")


def is_empty(value: str) -> bool:
    return clean_text(value) in {"", DEFAULT_VALUE, "无", "暂无", "数据加载中..."}


def fill_if_empty(row: dict, field: str, value: str) -> None:
    value = clean_text(value)
    if not value:
        return
    if field not in row:
        return
    if is_empty(row.get(field, "")):
        row[field] = value


def html_to_lines(html_or_text: str) -> list[str]:
    soup = BeautifulSoup(html_or_text or "", "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = []
    for line in text.splitlines():
        line = clean_text(line)
        if not line:
            continue
        if line in SECTION_TITLES:
            continue
        if line == "数据加载中...":
            continue
        lines.append(line)
    return lines


def parse_key_values(html_or_text: str) -> dict:
    lines = html_to_lines(html_or_text)
    kv = {}
    i = 0

    while i < len(lines):
        label = normalize_label(lines[i])
        if label not in KNOWN_LABELS:
            i += 1
            continue

        values = []
        i += 1
        while i < len(lines):
            next_label = normalize_label(lines[i])
            if next_label in KNOWN_LABELS:
                break
            if lines[i] not in SECTION_TITLES:
                values.append(lines[i])
            i += 1

        value = clean_text("\n".join(values))
        if value:
            kv[label] = value

    return kv


def apply_key_values(row: dict, kv: dict) -> dict:
    for source_label, value in kv.items():
        label = normalize_label(source_label)
        target_field = FIELD_ALIASES.get(label)
        if not target_field:
            continue

        value = clean_text(value)
        if not value:
            continue

        if target_field == "项目名称" and label == "招标项目名称":
            row[target_field] = value
            continue

        if target_field == "公告内容" and label in {
            "招标内容与范围及招标方案说明",
            "标段(包)内容",
            "标段（包）内容",
        }:
            if len(value) >= len(clean_text(row.get(target_field, ""))):
                row[target_field] = value
            continue

        fill_if_empty(row, target_field, value)

    fill_if_empty(row, "发布网站", SITE_NAME)
    return row



def parse_table_key_values(html_or_text: str) -> dict:
    soup = BeautifulSoup(html_or_text or "", "html.parser")
    kv = {}

    for table in soup.find_all("table"):
        lines = []
        for line in table.get_text("\n", strip=True).splitlines():
            line = clean_text(line)
            if line:
                lines.append(line)

        i = 0
        while i < len(lines) - 1:
            label = normalize_label(lines[i])
            if label in KNOWN_LABELS:
                value = clean_text(lines[i + 1])
                if value:
                    kv[label] = value
                i += 2
            else:
                i += 1

    return kv

def parse_related_content(html_or_text: str, row: dict | None = None) -> dict:
    row = row or {}
    kv = parse_key_values(html_or_text)
    return apply_key_values(row, kv)


def parse_detail_summary(html_or_text: str, row: dict | None = None) -> dict:
    row = row or {}

    # Detail pages contain long notice/PDF text too, so only parse real HTML tables here.
    kv = parse_table_key_values(html_or_text)
    apply_key_values(row, kv)

    text = BeautifulSoup(html_or_text or "", "html.parser").get_text("\n", strip=True)
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]

    for i, line in enumerate(lines):
        if line.startswith("发布日期"):
            value = clean_text(line.replace("发布日期", "", 1))
            if not value and i + 1 < len(lines):
                value = lines[i + 1]
            fill_if_empty(row, "发布日期", value)

        if line.startswith("来源"):
            value = clean_text(line.replace("来源", "", 1))
            if not value and i + 1 < len(lines):
                value = lines[i + 1]
            fill_if_empty(row, "发布网站", value)

    fill_if_empty(row, "发布网站", SITE_NAME)
    return row

def extract_project_code(html_or_text: str) -> str:
    text = BeautifulSoup(html_or_text or "", "html.parser").get_text("\n", strip=True)
    patterns = [
        r"/f/new/notice/getRelatedContent/\d+/([A-Za-z0-9]+)",
        r"招标项目编号[：:\s]+([A-Za-z0-9]+)",
        r"\b(E\d+[A-Za-z0-9]+)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, html_or_text or text)
        if match:
            return clean_text(match.group(1))
    return ""


def extract_pdf_urls(html_or_text: str) -> list[str]:
    html = html_or_text or ""
    urls = []

    for match in re.findall(r"file=([^\"'&]+(?:%26[^\"']+)*)", html):
        url = unquote(match)
        if url.startswith("/f/downloadByFileName"):
            urls.append(url)

    for match in re.findall(r"(/f/downloadByFileName\?[^\"'\s<>]+)", html):
        urls.append(unquote(match))

    seen = set()
    result = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result

def parse_notice_text(text: str, row: dict | None = None) -> dict:
    row = row or {}
    raw_text = text or ""

    lines = [
        clean_text(line)
        for line in raw_text.splitlines()
        if clean_text(line)
    ]

    def compact(value: str) -> str:
        return re.sub(r"[\s:：]", "", value or "")

    def find_line(start: int, aliases: set[str]) -> int:
        for index in range(start, len(lines)):
            key = compact(lines[index])
            if any(key == alias or key.startswith(alias) for alias in aliases):
                return index
        return -1

    def value_after(block: list[str], aliases: set[str]) -> str:
        for index, line in enumerate(block):
            key = compact(line)

            for alias in aliases:
                if key == alias:
                    if index + 1 < len(block):
                        return clean_text(block[index + 1])

                if key.startswith(alias) and len(key) > len(alias):
                    value = key[len(alias):]
                    if value:
                        return clean_text(value)

        return ""

    # “十一、联系方式”之后分别解析招标人和代理机构。
    contact_start = -1
    for index, line in enumerate(lines):
        key = compact(line)
        if "联系方式" in key and len(key) <= 12:
            contact_start = index

    if contact_start >= 0:
        contact_lines = lines[contact_start + 1:]

        bidder_index = find_line(
            contact_start + 1,
            {"招标人", "招标人名称"},
        )
        agent_index = find_line(
            contact_start + 1,
            {"招标代理机构", "招标代理机构名称"},
        )

        if bidder_index >= 0:
            bidder_end = agent_index if agent_index > bidder_index else len(lines)
            bidder_block = lines[bidder_index:bidder_end]

            fill_if_empty(
                row,
                "招标人",
                value_after(bidder_block, {"招标人", "招标人名称"}),
            )
            fill_if_empty(
                row,
                "招标人地址",
                value_after(bidder_block, {"地址", "招标人地址"}),
            )
            fill_if_empty(
                row,
                "招标人联系人",
                value_after(bidder_block, {"联系人", "招标人联系人"}),
            )
            fill_if_empty(
                row,
                "招标人联系方式",
                value_after(
                    bidder_block,
                    {"电话", "联系电话", "联系方式", "招标人联系方式"},
                ),
            )

        if agent_index >= 0:
            agent_block = lines[agent_index:]

            # 避免把签章说明误当成字段值。
            for index, line in enumerate(agent_block):
                if "主要负责人" in compact(line) or compact(line) == "国家部委网站":
                    agent_block = agent_block[:index]
                    break

            fill_if_empty(
                row,
                "招标代理机构",
                value_after(agent_block, {"招标代理机构", "招标代理机构名称"}),
            )
            fill_if_empty(
                row,
                "招标代理机构地址",
                value_after(agent_block, {"地址", "招标代理机构地址"}),
            )
            fill_if_empty(
                row,
                "招标代理机构联系人",
                value_after(agent_block, {"联系人", "招标代理机构联系人"}),
            )
            fill_if_empty(
                row,
                "招标代理机构联系方式",
                value_after(
                    agent_block,
                    {"电话", "联系电话", "联系方式", "招标代理机构联系方式"},
                ),
            )

    # 监督部门通常是自然语言：“监督部门为……。电话为……”。
    supervision_match = re.search(
        r"监督部门(?:为|是|名称为)?[：:\s]*([^\n。；;]+)",
        raw_text,
    )
    if supervision_match:
        supervision = clean_text(supervision_match.group(1))
        supervision = re.sub(r"^本招标项目的", "", supervision)
        fill_if_empty(row, "监督部门", supervision)

    supervision_section = raw_text
    section_match = re.search(
        r"(?:十|10)[、.．\s]*监督部门(.*?)(?:(?:十一|11)[、.．\s]*联系方式|$)",
        raw_text,
        flags=re.S,
    )
    if section_match:
        supervision_section = section_match.group(1)

    phone_match = re.search(
        r"(?:电话|联系电话|联系方式)(?:为)?[：:\s]*"
        r"((?:\+?86[-\s]?)?(?:1[3-9]\d{9}|0\d{2,3}[-\s]?\d{7,8}))",
        supervision_section,
    )
    if phone_match:
        fill_if_empty(
            row,
            "监督部门联系方式",
            clean_text(phone_match.group(1)),
        )

    return row

def parse_notice_history(
    html_or_text: str,
    page_url: str,
    row: dict | None = None,
) -> dict:
    row = row or {}
    soup = BeautifulSoup(html_or_text or "", "html.parser")

    entries = []
    seen_urls = set()

    for anchor in soup.find_all("a", href=True):
        href = clean_text(anchor.get("href", ""))
        title = clean_text(
            anchor.get("title", "")
            or anchor.get_text(" ", strip=True)
        )

        if not title or not href:
            continue

        if not re.search(
            r"/f/new/notice/[01]/[0-9a-fA-F]{32}(?:$|[?#])",
            href,
        ):
            continue

        absolute_url = urljoin(page_url, href)

        if absolute_url in seen_urls:
            continue

        seen_urls.add(absolute_url)
        entries.append(f"{title}|{absolute_url}")

    existing = clean_text(row.get("公告历史", ""))

    history_parts = []
    if not is_empty(existing):
        history_parts.extend(
            part.strip()
            for part in existing.splitlines()
            if part.strip()
        )

    for entry in entries:
        if entry not in history_parts:
            history_parts.append(entry)

    if history_parts:
        row["公告历史"] = "\n".join(history_parts)

    return row

