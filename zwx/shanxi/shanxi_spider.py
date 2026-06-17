"""
山西省公共资源交易平台爬虫 v3.1
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time, random, re, os
from datetime import datetime

# ==================== 配置 ====================
BASE_URL = "https://prec.sxzwfw.gov.cn"
LIST_URL = "https://prec.sxzwfw.gov.cn/queryContent-jyxx.jspx"

CHANNEL = {
    "招标计划":   198,
    "招标公告":   12,
    "变更公告":   13,
    "中标候选人": 14,
    "中标结果":   15,
}

OUTPUT_DIR = "/home/intsig/zwx/shanxi/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TODAY_STR = datetime.now().strftime("%Y%m%d")
CSV_PATH = os.path.join(OUTPUT_DIR, f"{TODAY_STR}_山西省.csv")
LOG_DIR = "/home/intsig/zwx/shanxi/log"
os.makedirs(LOG_DIR, exist_ok=True)
CHECK_LOG_PATH = os.path.join(LOG_DIR, f"{TODAY_STR}_check.log")

CRAWL_DAYS = 4

SESSION = requests.Session()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml",
    "Content-Type": "application/x-www-form-urlencoded",
}

COLUMNS = [
    "项目名称","交易地区", "统一代码",  "项目类型", "招标内容",
    "招标人名称", "发布类型", "建设地点", "建设内容及规模",
    "项目总投资", "招标方式", "行政监督部门", "发布单位",
    "招标公告发布日期", "招标文件获取时间", "投标人资格要求", "招标公告链接",
    "变更公告链接", "中标候选人名称", "中标候选人公示链接",
    "中标人", "中标结果公示链接", "终止公告链接", "公告历史"
]

SKIP_KEYWORDS = ["控制价", "招标控制价"]
TERMINATE_KEYWORDS = ["终止公告", "废标公告", "终止招标", "撤销公告", "招标公告撤销公告", "招标撤销公告"]
AMENDMENT_KEYWORDS = ["变更公告", "更正公告", "变更公示", "更正公示", "澄清公告", "澄清答疑", "延期公告", "二次延期公告"]

# 自检日志：收集跳过的未知公告
SKIPPED_UNKNOWN = []

# ==================== 工具函数 ====================

def sleep():
    time.sleep(random.uniform(1.0, 2.0))

def get(url, retries=3):
    for i in range(retries):
        try:
            resp = SESSION.get(url, headers=HEADERS, timeout=15)
            resp.encoding = "utf-8"
            return resp
        except Exception as e:
            print(f"  [GET失败{i+1}/{retries}] {e}")
            time.sleep(2)
    return None

def post_req(url, data, retries=3, headers=None):
    h = headers if headers else HEADERS
    for i in range(retries):
        try:
            resp = SESSION.post(url, headers=h, data=data, timeout=30)
            resp.encoding = "utf-8"
            return resp
        except Exception as e:
            print(f"  [POST失败{i+1}/{retries}] {e}")
            time.sleep(3)
    return None

def clean_title(title):
    suffixes = [
        "中标结果公示", "中标结果公示澄清", "中标候选人公示",
        "变更公告", "更正公告", "变更公示", "更正公示", "澄清公告", "澄清答疑", "延期公告", "二次延期公告",
        "招标计划",
        "招标控制价变更", "招标控制价", "控制价变更", "控制价",
        "招标公告", "二次招标公告", "二次重新招标公告", "三次招标公告", "三次重新招标公告",
        "预审公告", "撤销公告", "招标公告撤销公告", "招标撤销公告", "终止公告", "废标公告",
        "其他公告",
    ]
    t = title.strip()
    for s in suffixes:
        if t.endswith(s):
            t = t[:-len(s)].strip()
            break
    return t

def should_skip(raw_title):
    for kw in SKIP_KEYWORDS:
        if kw in raw_title:
            return True
    return False

def should_terminate(raw_title):
    for kw in TERMINATE_KEYWORDS:
        if kw in raw_title:
            return True
    return False

def is_amendment(raw_title):
    for kw in AMENDMENT_KEYWORDS:
        if kw in raw_title:
            return True
    return False

def parse_location(loc_text):
    loc_text = re.sub(r"交易场所[：:]", "", loc_text).strip()
    if "省本级" in loc_text or loc_text == "省":
        return "山西省本级"
    m = re.search(r'([^\s]+市)', loc_text)
    if m:
        return m.group(1)
    return loc_text or "暂无"

def empty_row(project_name, location="暂无"):
    row = {col: "暂无" for col in COLUMNS}
    row["项目名称"] = project_name
    row["交易地区"] = location
    return row

# ==================== 列表页爬取 ====================

def fetch_list(channel_id, days=4, search_title="", verbose=True):
    results = []
    page = 1
    prev_url = "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml"
    while True:
        if page == 1:
            url = LIST_URL
        else:
            url = LIST_URL.replace("queryContent-", f"queryContent_{page}-")

        data = {
            "title": search_title,
            "channelId": channel_id,
            "inDates": days,
            "beginTime": "", "endTime": "", "origin": "", "ext": "",
        }
        headers = dict(HEADERS)
        headers["Referer"] = prev_url
        if verbose: print(f"  [列表] channelId={channel_id} 第{page}页...")
        resp = post_req(url, data, headers=headers)
        prev_url = url
        if not resp:
            if verbose: print("  [列表] 请求失败")
            break
        if len(resp.text) < 100:
            if verbose: print(f"  [列表] 响应内容异常(长度={len(resp.text)})，跳过")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".cs_two_c_2")
        if not items:
            if page == 1 and verbose:
                print(f"  [列表] 无数据，状态码={resp.status_code}")
            break

        for item in items:
            link = item.get("href", "")
            if not link.startswith("http"):
                link = BASE_URL + link
            link = link.replace("http://prec.sxzwfw.gov.cn:80", "https://prec.sxzwfw.gov.cn")
            link = link.replace("http://prec.sxzwfw.gov.cn", "https://prec.sxzwfw.gov.cn")

            title_el = item.select_one(".cs_bz_cont")
            raw_title = title_el.get_text(strip=True) if title_el else ""
            pure_title = clean_title(raw_title)

            date_el = item.select_one(".cs_bz_cont_1_time")
            date = date_el.get_text(strip=True) if date_el else ""

            loc_spans = item.select(".cs_bz_cont_1 span")
            loc_text = ""
            for sp in loc_spans:
                t = sp.get_text(strip=True)
                if "交易场所" in t:
                    loc_text = t
                    break
            location = parse_location(loc_text)

            results.append({
                "raw_title": raw_title,
                "title": pure_title,
                "link": link,
                "date": date,
                "location": location,
            })

        count_match = re.search(r",count:\s*(\d+)", resp.text)
        if count_match:
            total = int(count_match.group(1))
            if verbose: print(f"  [列表] 总条数={total}，当前已获取={len(results)}")
            if page * 10 >= total:
                break
        else:
            break

        page += 1
        sleep()

    if verbose: print(f"  [列表] 共 {len(results)} 条")
    return results

# ==================== 详情页解析 ====================

def parse_plan_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {}

    table = soup.select_one("table.bid_msgTable")
    if not table:
        return result

    label_map = {
        "投资项目统一代码": "统一代码",
        "项目类型": "项目类型",
        "项目总投资": "项目总投资",
        "招标内容": "招标内容",
        "招标方式": "招标方式",
        "招标人名称": "招标人名称",
        "行政监督部门": "行政监督部门",
        "发布类型": "发布类型",
        "发布单位": "发布单位",
        "建设地点": "建设地点",
        "建设内容及规模": "建设内容及规模",
    }

    rows = table.select("tr")
    for row in rows:
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
            if "预计发布时间" in label:
                result["招标公告发布日期"] = val
            elif label_clean in label_map:
                result[label_map[label_clean]] = val

    return result


def parse_notice_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    content_el = soup.select_one("table.gycq-table td") or soup.select_one(".cs_xq_content")
    full_text = content_el.get_text() if content_el else soup.get_text()
    result = {"招标公告链接": url}

    # 招标文件获取时间
    acq_patterns = [
        r'获取时间[：:]\s*([^\n。]{5,80})',
        r'文件获取时间[：:]\s*([^\n。]{5,80})',
    ]
    for p in acq_patterns:
        m = re.search(p, full_text)
        if m:
            val = m.group(1).strip().rstrip("。；")
            if len(val) > 5:
                result["招标文件获取时间"] = val
                break

    # 投标人/申请人资格要求
    STOP_SECTIONS = [
        "招标文件的获取", "招标文件获取", "文件的获取", "电子招标文件",
        "预审文件的获取", "申请文件的递交",
        "投标文件的递交", "投标文件递交", "开标时间", "开标地点",
        "投标保证金", "提出异议", "异议的渠道",
        "联系方式", "监督部门", "招标代理机构",
        "盖章", "备案号", "晋ICP", "行政审批服务管理局", "投诉举报"
    ]
    kw_pos = full_text.find("投标人资格要求")
    if kw_pos == -1:
        kw_pos = full_text.find("投标人资格条件")
    if kw_pos == -1:
        kw_pos = full_text.find("申请人资格要求")
    if kw_pos == -1:
        kw_pos = full_text.find("申请投标人资格要求")
    if kw_pos == -1:
        kw_pos = full_text.find("投标资格要求")
    if kw_pos != -1:
        after = full_text[kw_pos:]
        nl = after.find('\n')
        if nl != -1:
            content_after = after[nl:]
            cut_pos = len(content_after)
            for kw in STOP_SECTIONS:
                p = content_after.find(kw)
                if p != -1 and p < cut_pos:
                    cut_pos = p
            qual_text = re.sub(r'\n+', ' ', content_after[:cut_pos].strip()).strip()
            if len(qual_text) > 20:
                result["投标人资格要求"] = qual_text

    return result


def parse_candidate_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {"中标候选人公示链接": url}

    candidates = []
    tables = soup.select("table")
    SKIP_NAMES = {"中标候选人名称", "中标候选人", "名称", "排序", "序号", ""}

    for table in tables:
        cls_str = " ".join(table.get("class") or [])
        if "gycq-table" in cls_str:
            continue
        all_rows = table.select("tr")
        if len(all_rows) < 2:
            continue
        first_row_cells = all_rows[0].select("td, th")
        if len(first_row_cells) <= 1:
            continue
        header_cells = all_rows[0].select("th") or all_rows[0].select("td")
        headers = [c.get_text(strip=True) for c in header_cells]
        candidate_col = -1
        for ci, h in enumerate(headers):
            if h in ["中标候选人名称", "中标候选人"]:
                candidate_col = ci
                break
        if candidate_col == -1:
            continue
        data_rows = all_rows[1:]
        for row in data_rows:
            cells = row.select("td")
            if len(cells) > candidate_col:
                name = cells[candidate_col].get_text(strip=True)
                if name and len(name) >= 2 and name not in SKIP_NAMES:
                    if name not in candidates:
                        candidates.append(name)
        if candidates:
            break

    if candidates:
        result["中标候选人名称"] = "、".join(candidates)

    return result


def parse_result_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    content_el = soup.select_one("table.gycq-table td") or soup.select_one(".cs_xq_content")
    full_text = content_el.get_text() if content_el else soup.get_text()
    result = {"中标结果公示链接": url}

    winners = []
    p1 = r'中\s*标\s*人[：:]\s*([^\s　\n\r，。；]{2,40}(?:有限公司|集团|联合体|股份公司|建设有限公司|工程有限公司|咨询股份))'
    for m in re.finditer(p1, full_text):
        w = m.group(1).strip().rstrip("，。；　 ")
        if w and w not in winners:
            winners.append(w)

    p2 = r'(?:确定|确认)[^\n，。（(]{0,10}?([^\n，。（(【\s]{2,30}(?:有限公司|集团|联合体|股份公司|股份有限公司|建设有限公司|工程有限公司|咨询股份))(?=为中标人|中标)'
    for m in re.finditer(p2, full_text):
        w = m.group(1).strip()
        if w and w not in winners:
            winners.append(w)

    p3 = r'中标单位[：:]\s*([^\s　\n\r，。；]{2,40}(?:有限公司|集团|联合体|股份公司))'
    for m in re.finditer(p3, full_text):
        w = m.group(1).strip()
        if w and w not in winners:
            winners.append(w)

    p4 = r'中标人应为([^\s，。；\n]{2,40}(?:有限公司|集团|联合体|股份公司|股份有限公司|建设有限公司|工程有限公司|咨询股份))'
    for m in re.finditer(p4, full_text):
        w = m.group(1).strip()
        if w and w not in winners:
            winners.append(w)

    def clean_winner(w):
        w = re.split(r'中标价', w)[0]
        return w.strip().rstrip("，。；　 、")

    winners = [clean_winner(w) for w in winners]
    winners = [w for w in winners if len(w) >= 2]

    if winners:
        result["中标人"] = "、".join(winners)

    return result

# ==================== 搜索历史 ====================

def search_history(project_name, channel_id):
    return fetch_list(channel_id, days=4000, search_title=project_name, verbose=False)


def find_and_fill_history(project_name, location, from_step=2):
    """
    根据调用步骤控制搜索范围，同时汇总公告历史
    step2 → 搜招标计划
    step3 → 搜招标计划 + 招标公告
    step4 → 搜招标计划 + 招标公告 + 变更公告
    step5 → 搜全部四个
    step6（终止）→ 搜全部五个
    """
    print(f"  [历史] 搜索中...")
    row = empty_row(project_name, location)
    history_parts = []  # 汇总公告历史

    # 招标计划：所有步骤都搜
    items = search_history(project_name, CHANNEL["招标计划"])
    if items:
        sleep()
        detail = parse_plan_detail(items[0]["link"])
        for k, v in detail.items():
            if k in COLUMNS and v:
                row[k] = v
        if items[0]["location"] != "暂无":
            row["交易地区"] = items[0]["location"]
        for i in items:
            history_parts.append(f"{i['raw_title']}|{i['link']}")
        print(f"    ✓ 招标计划")

    # 招标公告：step3及以后
    if from_step >= 3:
        items = search_history(project_name, CHANNEL["招标公告"])
        notice_items = [i for i in items if not should_skip(i["raw_title"]) and not is_amendment(i["raw_title"]) and not should_terminate(i["raw_title"])]
        if notice_items:
            sleep()
            detail = parse_notice_detail(notice_items[0]["link"])
            for k, v in detail.items():
                if k in COLUMNS and v:
                    row[k] = v
            if notice_items[0]["date"]:
                row["招标公告发布日期"] = notice_items[0]["date"]
            print(f"    ✓ 招标公告")
        for i in items:
            history_parts.append(f"{i['raw_title']}|{i['link']}")

    # 变更公告：step4及以后
    if from_step >= 4:
        items = search_history(project_name, CHANNEL["变更公告"])
        if items:
            row["变更公告链接"] = "；".join([i["link"] for i in items])
            for i in items:
                history_parts.append(f"{i['raw_title']}|{i['link']}")
            print(f"    ✓ 变更公告 {len(items)}条")

    # 中标候选人：step5及以后
    if from_step >= 5:
        items = search_history(project_name, CHANNEL["中标候选人"])
        if items:
            sleep()
            detail = parse_candidate_detail(items[0]["link"])
            for k, v in detail.items():
                if k in COLUMNS and v:
                    row[k] = v
            for i in items:
                history_parts.append(f"{i['raw_title']}|{i['link']}")
            print(f"    ✓ 候选人")

    # 中标结果：step6（终止公告触发）
    if from_step >= 6:
        items = search_history(project_name, CHANNEL["中标结果"])
        if items:
            sleep()
            detail = parse_result_detail(items[0]["link"])
            for k, v in detail.items():
                if k in COLUMNS and v:
                    row[k] = v
            for i in items:
                history_parts.append(f"{i['raw_title']}|{i['link']}")
            print(f"    ✓ 结果")

    if history_parts:
        row["公告历史"] = "；".join(history_parts)

    return row


def update_history(df, idx, new_items):
    """把新公告追加到公告历史字段"""
    existing = str(df.at[idx, "公告历史"])
    new_parts = [f"{i['raw_title']}|{i['link']}" for i in new_items]
    for part in new_parts:
        if existing == "暂无":
            existing = part
        elif part not in existing:
            existing = existing + "；" + part
    df.at[idx, "公告历史"] = existing
    return df

# ==================== CSV操作 ====================

def load_csv():
    if os.path.exists(CSV_PATH):
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("暂无")
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = "暂无"
        df = df[COLUMNS]
        BAD_KW = ["备案号", "晋ICP", "网站标识码", "行政审批服务管理局", "投诉举报热线"]
        def is_bad_qual(v):
            return any(kw in str(v) for kw in BAD_KW)
        bad_mask = df["投标人资格要求"].apply(is_bad_qual)
        if bad_mask.any():
            print(f"[CSV] 清除 {bad_mask.sum()} 行错误的投标人资格要求数据")
            df.loc[bad_mask, "投标人资格要求"] = "暂无"
        print(f"[CSV] 加载 {len(df)} 行")
    else:
        df = pd.DataFrame(columns=COLUMNS)
        print("[CSV] 新建")
    return df

def save_csv(df):
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

def find_row(df, project_name):
    if df.empty:
        return -1
    mask = df["项目名称"] == project_name
    if mask.any():
        return df[mask].index[0]
    min_len = 5
    for idx, row in df.iterrows():
        name = str(row["项目名称"])
        if len(name) >= min_len and len(project_name) >= min_len:
            if project_name in name or name in project_name:
                return idx
    return -1

def update_row(df, idx, updates):
    for k, v in updates.items():
        if k not in df.columns or not v or v == "暂无":
            continue
        if k in ("变更公告链接", "终止公告链接"):
            existing = str(df.at[idx, k])
            if existing == "暂无":
                df.at[idx, k] = v
            elif v not in existing:
                df.at[idx, k] = existing + "；" + v
        else:
            df.at[idx, k] = v
    return df

def insert_row(df, row):
    return pd.concat([df, pd.DataFrame([row], columns=COLUMNS)], ignore_index=True)

# ==================== 五步爬取 ====================

def step1_plan(df):
    print("\n===== 第一步：招标计划 =====")
    items = fetch_list(CHANNEL["招标计划"], CRAWL_DAYS)
    for item in items:
        name = item["title"]
        print(f"\n  [{name}]")
        if find_row(df, name) >= 0:
            print("  → 已存在，跳过")
            continue
        sleep()
        detail = parse_plan_detail(item["link"])
        row = empty_row(name, item["location"])
        for k, v in detail.items():
            if k in COLUMNS and v:
                row[k] = v
        row["公告历史"] = f"{item['raw_title']}|{item['link']}"
        df = insert_row(df, row)
        print(f"  → 插入: 统一代码={row.get('统一代码','暂无')}, 预计={row.get('招标公告发布日期','暂无')}")
        save_csv(df)
    return df


def step2_notice(df):
    print("\n===== 第二步：招标/资质公告 =====")
    items = fetch_list(CHANNEL["招标公告"], CRAWL_DAYS)
    for item in items:
        raw = item["raw_title"]
        name = item["title"]
        real_date = item["date"]

        # 1. 控制价：跳过并记录
        if should_skip(raw):
            print(f"\n  [跳过-控制价] {raw}")
            continue

        # 2. 终止/废标/撤销公告：更新终止公告链接
        if should_terminate(raw):
            print(f"\n  [终止公告] {name}")
            idx = find_row(df, name)
            if idx >= 0:
                df = update_row(df, idx, {"终止公告链接": item["link"]})
                df = update_history(df, idx, [item])
                print(f"  → 更新终止公告链接")
            else:
                row = find_and_fill_history(name, item["location"], from_step=6)
                row["终止公告链接"] = item["link"]
                df = insert_row(df, row)
            save_csv(df)
            continue

        # 3. 变更/更正/澄清/延期公告：写入变更公告链接
        if is_amendment(raw):
            print(f"\n  [变更公告-来自招标频道] {name}")
            idx = find_row(df, name)
            if idx >= 0:
                df = update_row(df, idx, {"变更公告链接": item["link"]})
                df = update_history(df, idx, [item])
                print(f"  → 更新变更链接")
            else:
                row = find_and_fill_history(name, item["location"], from_step=2)
                row["变更公告链接"] = item["link"]
                df = insert_row(df, row)
            save_csv(df)
            continue

        # 4. 其余当招标公告处理
        print(f"\n  [{name}] 日期={real_date}")
        sleep()
        detail = parse_notice_detail(item["link"])
        updates = {
            "招标公告发布日期": real_date,
            "招标文件获取时间": detail.get("招标文件获取时间", ""),
            "投标人资格要求": detail.get("投标人资格要求", ""),
            "招标公告链接": item["link"],
        }
        idx = find_row(df, name)
        if idx >= 0:
            print(f"  → 更新")
            df = update_row(df, idx, updates)
            df = update_history(df, idx, [item])
        else:
            row = find_and_fill_history(name, item["location"], from_step=2)
            for k, v in updates.items():
                if v:
                    row[k] = v
            df = insert_row(df, row)
        save_csv(df)
    return df


def step3_amendment(df):
    print("\n===== 第三步：变更公告 =====")
    items = fetch_list(CHANNEL["变更公告"], CRAWL_DAYS)
    for item in items:
        name = item["title"]
        print(f"\n  [{name}]")
        idx = find_row(df, name)
        if idx >= 0:
            df = update_row(df, idx, {"变更公告链接": item["link"]})
            df = update_history(df, idx, [item])
            print(f"  → 更新变更链接")
        else:
            row = find_and_fill_history(name, item["location"], from_step=3)
            row["变更公告链接"] = item["link"]
            df = insert_row(df, row)
        save_csv(df)
    return df


def step4_candidate(df):
    print("\n===== 第四步：中标候选人公示 =====")
    items = fetch_list(CHANNEL["中标候选人"], CRAWL_DAYS)
    for item in items:
        name = item["title"]
        print(f"\n  [{name}]")
        sleep()
        detail = parse_candidate_detail(item["link"])
        updates = {
            "中标候选人名称": detail.get("中标候选人名称", ""),
            "中标候选人公示链接": item["link"],
        }
        idx = find_row(df, name)
        if idx >= 0:
            print(f"  → 更新候选人: {updates.get('中标候选人名称','')[:30]}")
            df = update_row(df, idx, updates)
            df = update_history(df, idx, [item])
        else:
            row = find_and_fill_history(name, item["location"], from_step=4)
            for k, v in updates.items():
                if v:
                    row[k] = v
            df = insert_row(df, row)
        save_csv(df)
    return df


def step5_result(df):
    print("\n===== 第五步：中标结果公示 =====")
    items = fetch_list(CHANNEL["中标结果"], CRAWL_DAYS)
    for item in items:
        name = item["title"]
        print(f"\n  [{name}]")
        sleep()
        detail = parse_result_detail(item["link"])
        updates = {
            "中标人": detail.get("中标人", ""),
            "中标结果公示链接": item["link"],
        }
        idx = find_row(df, name)
        if idx >= 0:
            print(f"  → 更新中标人: {updates.get('中标人','')[:30]}")
            df = update_row(df, idx, updates)
            df = update_history(df, idx, [item])
        else:
            row = find_and_fill_history(name, item["location"], from_step=5)
            for k, v in updates.items():
                if v:
                    row[k] = v
            df = insert_row(df, row)
        save_csv(df)
    return df

# ==================== 文本索引 + 自检日志 ====================

def build_index(df):
    path = CSV_PATH.replace(".csv", "_index.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"山西省公共资源交易平台 文本索引\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"总记录数: {len(df)}\n")
        f.write("=" * 60 + "\n\n")
        for idx, row in df.iterrows():
            f.write(f"[{idx+1}] {row['项目名称']}\n")
            f.write(f"  地区: {row['交易地区']} | 招标人: {row['招标人名称']}\n")
            f.write(f"  发布日期: {row['招标公告发布日期']} | 获取时间: {row['招标文件获取时间']}\n")
            f.write(f"  中标人: {row['中标人']}\n")
            f.write(f"  公告链接: {row['招标公告链接']}\n\n")
    print(f"[索引] → {path}")


def build_check_log(df, skipped_unknown):
    """生成自检日志，记录两类问题"""
    with open(CHECK_LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"山西省公共资源交易平台 自检日志\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")

        # 类型1：遇到但跳过的未知类型公告
        f.write(f"【类型1】未识别公告（共{len(skipped_unknown)}条）\n")
        f.write("-" * 40 + "\n")
        if skipped_unknown:
            for item in skipped_unknown:
                f.write(f"  标题: {item['raw_title']}\n")
                f.write(f"  链接: {item['link']}\n\n")
        else:
            f.write("  无\n\n")

        # 类型2：有链接但字段提取失败
        f.write(f"【类型2】有链接但字段提取失败\n")
        f.write("-" * 40 + "\n")
        problems = []
        for idx, row in df.iterrows():
            issues = []
            if row["招标公告链接"] != "暂无":
                if row["投标人资格要求"] == "暂无":
                    issues.append("投标人资格要求未提取")
                if row["招标文件获取时间"] == "暂无":
                    issues.append("招标文件获取时间未提取")
            if row["中标候选人公示链接"] != "暂无" and row["中标候选人名称"] == "暂无":
                issues.append("中标候选人名称未提取")
            if row["中标结果公示链接"] != "暂无" and row["中标人"] == "暂无":
                issues.append("中标人未提取")
            if issues:
                problems.append((row["项目名称"], issues))

        if problems:
            for name, issues in problems:
                f.write(f"  项目: {name}\n")
                for issue in issues:
                    f.write(f"    - {issue}\n")
                f.write("\n")
        else:
            f.write("  无\n\n")

    print(f"[自检] → {CHECK_LOG_PATH}")

# ==================== 主程序 ====================

def main():
    print("=" * 60)
    print("山西省公共资源交易平台爬虫 v3.1")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"输出: {CSV_PATH}")
    print("=" * 60)

    print("初始化Session...")
    try:
        SESSION.get("https://prec.sxzwfw.gov.cn/jyxx/index.jhtml", headers=HEADERS, timeout=15)
        print("Session初始化成功")
    except Exception as e:
        print(f"Session初始化失败: {e}")

    df = load_csv()

    df = step1_plan(df)
    df = step2_notice(df)
    df = step3_amendment(df)
    df = step4_candidate(df)
    df = step5_result(df)

    save_csv(df)
    build_index(df)
    build_check_log(df, SKIPPED_UNKNOWN)

    print(f"\n{'='*60}")
    print(f"完成！共 {len(df)} 条记录 → {CSV_PATH}")
    print("=" * 60)

if __name__ == "__main__":
    main()