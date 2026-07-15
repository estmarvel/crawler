import re
from bs4 import BeautifulSoup

from shanxi_crawler.columns import DEFAULT_VALUE
from shanxi_crawler.text_utils import clean_value, normalize_inline, normalize_text, is_empty_value

SKIP_KEYWORDS = [
    "招标控制价变更", "招标控制价", "控制价变更", "控制价",
    "最高投标限价", "最高限价", "投标限价",
]
TERMINATE_KEYWORDS = ["终止公告", "废标公告", "终止招标", "撤销公告", "招标公告撤销公告", "招标撤销公告"]
AMENDMENT_KEYWORDS = ["变更公告", "更正公告", "变更公示", "更正公示", "澄清公告", "澄清答疑", "延期公告", "二次延期公告", "变更通知"]
NOTICE_KEYWORDS = ["招标公告", "二次招标公告", "二次重新招标公告", "三次招标公告", "三次重新招标公告", "预审公告", "资审公告", "资格预审公告"]
CANDIDATE_KEYWORDS = ["中标候选人公示"]
RESULT_KEYWORDS = ["中标结果公示澄清", "中标结果公示"]


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
    t = forced_type or classify_title(raw_title)
    if t == "plan":
        return "招标计划"
    if t == "notice":
        if "资格预审" in raw_title or "预审公告" in raw_title or "资审公告" in raw_title:
            return "资格预审公告"
        return "招标公告"
    if t == "amendment":
        if "更正" in raw_title:
            return "更正公告"
        if "澄清" in raw_title:
            return "澄清公告"
        if "延期" in raw_title:
            return "延期公告"
        return "变更公告"
    if t == "candidate":
        return "中标候选人公示"
    if t == "result":
        return "中标结果公示"
    if t == "terminate":
        if "废标" in raw_title:
            return "废标公告"
        if "撤销" in raw_title:
            return "撤销公告"
        return "终止公告"
    if t == "skip":
        return "控制价公告"
    return "未知公告"


def clean_title(title: str) -> str:
    suffixes = [
        "中标结果公示澄清", "中标结果公示", "中标候选人公示",
        "变更通知", "变更公告", "更正公告", "变更公示", "更正公示",
        "澄清公告", "澄清答疑", "延期公告", "二次延期公告",
        "招标计划", "招标控制价变更", "招标控制价", "控制价变更", "控制价",
        "最高投标限价公示", "最高投标限价", "最高限价", "投标限价",
        "二次重新招标公告", "三次重新招标公告", "二次招标公告", "三次招标公告",
        "资格预审公告", "预审公告", "资审公告", "招标公告",
        "撤销公告", "招标公告撤销公告", "招标撤销公告", "终止公告", "废标公告", "其他公告",
    ]
    t = normalize_inline(title)
    changed = True
    while changed:
        changed = False
        for s in suffixes:
            if t.endswith(s):
                t = t[:-len(s)].strip()
                changed = True
                break
    t = re.sub(r"（?\(?第?\d+\s*标段\)?）?$", "", t).strip()
    t = re.sub(r"\(?\d+\s*标段\)?$", "", t).strip()
    t = re.sub(r"其它第一标段$", "", t).strip()
    return t or normalize_inline(title)


def get_content_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    content_el = soup.select_one("table.gycq-table td") or soup.select_one(".cs_xq_content")
    return normalize_text(content_el.get_text(separator="\n") if content_el else soup.get_text(separator="\n"))


def section_between(text: str, start_patterns, end_patterns) -> str:
    start_pos = -1
    for p in start_patterns:
        m = re.search(p, text or "", re.S)
        if m:
            start_pos = m.start()
            break
    if start_pos == -1:
        return ""
    sub = text[start_pos:]
    end_pos = len(sub)
    for p in end_patterns:
        m = re.search(p, sub, re.S)
        if m and 0 < m.start() < end_pos:
            end_pos = m.start()
    return sub[:end_pos].strip()


def extract_first(patterns, text: str) -> str:
    for p in patterns:
        m = re.search(p, text or "", re.S)
        if m:
            val = clean_value(m.group(1))
            if not is_empty_value(val):
                return val
    return DEFAULT_VALUE


def extract_label_value(block: str, label_patterns, next_label_patterns=None) -> str:
    if not block:
        return DEFAULT_VALUE
    if next_label_patterns is None:
        next_label_patterns = [
            r"招\s*标\s*人", r"招\s*标\s*代\s*理\s*机\s*构", r"监督部门", r"监督单位",
            r"地\s*址", r"地址", r"联\s*系\s*人", r"联系人",
            r"电\s*话", r"电话", r"联系电话", r"联系方式", r"项目负责人", r"电子邮件", r"邮\s*箱", r"邮箱",
        ]
    label_alt = "|".join(label_patterns)
    next_alt = "|".join(next_label_patterns)
    pattern = rf"(?:{label_alt})\s*[:：]\s*(.*?)(?=\n\s*(?:{next_alt})\s*[:：]|$)"
    m = re.search(pattern, block, re.S)
    if m:
        return clean_value(m.group(1))
    flat = normalize_inline(block)
    pattern = rf"(?:{label_alt})\s*[:：]\s*([^：:]+?)(?=(?:{next_alt})\s*[:：]|$)"
    m = re.search(pattern, flat, re.S)
    if m:
        return clean_value(m.group(1))
    return DEFAULT_VALUE


def parse_plan_detail_from_html(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.select_one("table.bid_msgTable")
    result = {}
    if not table:
        return result
    for row in table.select("tr"):
        tds = row.select("td")
        label_tds = [(i, td) for i, td in enumerate(tds) if td.find("b")]
        for idx, ltd in label_tds:
            label = ltd.get_text(strip=True).rstrip("：: ")
            label_clean = re.sub(r"[（(][^）)]*[）)]", "", label).strip()
            val = ""
            for j in range(idx + 1, len(tds)):
                if not tds[j].find("b"):
                    val = tds[j].get_text(strip=True)
                    break
            val = clean_value(val)
            if label_clean == "项目类型":
                result["所属行业"] = val
            elif label_clean == "招标方式":
                result["组织形式"] = val
    return result


def extract_open_time(full_text: str) -> str:
    section = section_between(
        full_text,
        [r"六\s*[、\.．]\s*开标时间", r"开标时间及地点", r"开标时间"],
        [r"七\s*[、\.．]", r"提交投标保证金", r"投标保证金", r"八\s*[、\.．]", r"$"],
    )
    return extract_first([
        r"开标时间\s*[:：]\s*([0-9]{4}[-年]\s*[0-9]{1,2}[-月]\s*[0-9]{1,2}(?:日)?\s*[0-9]{1,2}[:时：]\s*[0-9]{1,2}(?:分|:[0-9]{1,2})?)",
        r"开标时间\s*[:：]\s*([^\n；;。]+)",
        r"递交截止时间\s*[:：]\s*([^\n；;。]+)",
    ], section or full_text)


def extract_file_sale_time(full_text: str) -> str:
    section = section_between(
        full_text,
        [r"四\s*[、\.．]\s*招标文件的获取", r"招标文件的获取", r"文件的获取"],
        [r"五\s*[、\.．]\s*投标文件", r"投标文件的递交", r"递交截止时间", r"$"],
    )
    return extract_first([
        r"获取时间\s*[:：]\s*([0-9]{4}[-年]\s*[0-9]{1,2}[-月]\s*[0-9]{1,2}.*?(?:--|至|到).*?[0-9]{4}[-年]\s*[0-9]{1,2}[-月]\s*[0-9]{1,2}.*?[0-9]{1,2}[:时：]\s*[0-9]{1,2}(?:分)?)",
        r"获取时间\s*[:：]\s*([^\n]+(?:--|至|到)[^\n]+)",
        r"招标文件获取时间\s*[:：]\s*([^\n。；;]+)",
    ], section or full_text)


def split_contact_sections(contact_section: str):
    tenderer_block = section_between(
        contact_section,
        [r"招\s*标\s*人\s*[:：]"],
        [r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]", r"招标人或招标代理机构", r"主要负责人", r"签章"],
    )
    agency_block = section_between(
        contact_section,
        [r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]"],
        [r"招标人或招标代理机构", r"主要负责人", r"签章", r"$"],
    )
    return tenderer_block, agency_block


def extract_contact_fields(full_text: str) -> dict:
    contact_section = section_between(
        full_text,
        [r"十[一1]\s*[、\.．]\s*联系方式", r"[一二三四五六七八九十]+[、\.．]\s*联系方式", r"联系方式"],
        [r"招标人或招标代理机构", r"主要负责人", r"签章", r"山西省行政审批服务管理局", r"$"],
    )
    tenderer_block, agency_block = split_contact_sections(contact_section)
    result = {}
    result["招标人"] = extract_first([r"招\s*标\s*人\s*[:：]\s*(.*?)(?=\n\s*(?:地\s*址|地址)\s*[:：])", r"招\s*标\s*人\s*[:：]\s*([^\n]+)"], tenderer_block)
    result["招标人地址"] = extract_label_value(tenderer_block, [r"地\s*址", r"地址"])
    result["招标人联系人"] = extract_label_value(tenderer_block, [r"联\s*系\s*人", r"联系人"])
    result["招标人联系方式"] = extract_label_value(tenderer_block, [r"电\s*话", r"电话", r"联系电话", r"联系方式"])
    result["招标代理机构"] = extract_first([r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]\s*(.*?)(?=\n\s*(?:地\s*址|地址)\s*[:：])", r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]\s*([^\n]+)"], agency_block)
    result["招标代理机构地址"] = extract_label_value(agency_block, [r"地\s*址", r"地址"])
    result["招标代理机构联系人"] = extract_label_value(agency_block, [r"联\s*系\s*人", r"联系人", r"项目负责人"])
    result["招标代理机构联系方式"] = extract_label_value(agency_block, [r"电\s*话", r"电话", r"联系电话", r"联系方式"])
    return result


def extract_supervision_fields(full_text: str) -> dict:
    section = section_between(
        full_text,
        [r"十\s*[、\.．]\s*监督部门", r"[一二三四五六七八九十]+[、\.．]\s*监督部门", r"监督单位\s*[:：]", r"监督部门\s*[:：]", r"监督部门", r"招标投标监督部门"],
        [r"十[一1]\s*[、\.．]\s*联系方式", r"[一二三四五六七八九十]+[、\.．]\s*联系方式", r"联系方式", r"[一二三四五六七八九十]+[、\.．]\s*其他", r"$"],
    )
    base = section or full_text
    result = {}
    result["监督部门"] = extract_first([
        r"本招标项目的监督部门为\s*([^。\n；;]+)",
        r"监督部门为\s*([^。\n；;]+)",
        r"招标投标监督部门\s*[:：]\s*([^。\n；;]+)",
        r"监督单位\s*[:：]\s*([^。\n；;]+)",
        r"监督部门\s*[:：]\s*([^。\n；;]+)",
    ], base)
    result["监督部门地址"] = extract_label_value(section, [r"联系地址", r"地\s*址", r"地址"])
    result["监督部门联系人"] = extract_label_value(section, [r"联\s*系\s*人", r"联系人"])
    phone = extract_first([
        r"电话为\s*([0-9\-、，,/／\s]{6,})",
        r"联系电话\s*[:：]?\s*([0-9\-、，,/／\s]{6,})",
        r"联系方式\s*[:：]?\s*([0-9\-、，,/／\s]{6,})",
        r"电\s*话\s*[:：]?\s*([0-9\-、，,/／\s]{6,})",
    ], base)
    if not is_empty_value(phone):
        phone = re.sub(r"\s+", "", phone).strip("。；;，,")
    result["监督部门联系方式"] = phone
    return result


def parse_notice_detail_from_text(full_text: str) -> dict:
    full_text = normalize_text(full_text)
    result = {
        "公告内容": full_text if full_text else DEFAULT_VALUE,
        "开标时间": extract_open_time(full_text),
        "标书发售时间": extract_file_sale_time(full_text),
    }
    result.update(extract_contact_fields(full_text))
    result.update(extract_supervision_fields(full_text))
    return result


def parse_notice_detail_from_html(html: str) -> dict:
    return parse_notice_detail_from_text(get_content_text_from_html(html))


def parse_supervision_detail_from_text(full_text: str) -> dict:
    return extract_supervision_fields(normalize_text(full_text))


def parse_supervision_detail_from_html(html: str) -> dict:
    return parse_supervision_detail_from_text(get_content_text_from_html(html))
