import requests
import json
import time
import re
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'http://shanxi.fzbidding.com/bidinfo',
    'Origin': 'http://shanxi.fzbidding.com'
}

base = 'http://shanxi.fzbidding.com:8001'
list_url = base + '/hz/portal/portalBidding/list'
detail_url = base + '/hz/portal/portalBidding/detail'


def get_list(page=1, limit=50):
    params = {'page': page, 'limit': limit}
    try:
        r = requests.post(list_url, json=params, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f'获取列表失败 (page={page}): {e}')
    return None


def get_detail(notice_id, retry=3):
    for i in range(retry):
        try:
            r = requests.post(detail_url, json={'id': notice_id}, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json().get('detail', {})
        except Exception as e:
            if i < retry - 1:
                time.sleep(1)
                continue
            print(f'获取详情失败 (id={notice_id}): {e}')
    return None


def clean_html(html):
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n')
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)


def extract_field(text, patterns, default=''):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip()
            value = re.sub(r'^[：:、\s]+', '', value)
            value = re.sub(r'\s+', ' ', value)
            value = value.strip('（）() 　')
            if value:
                return value
    return default


def parse_notice_fields(detail):
    html = detail.get('noticeContext', '') or ''
    text = clean_html(html)
    
    fields = {
        # 基础信息
        'id': detail.get('id', ''),
        '公告标题': detail.get('noticeName', ''),
        '发布时间': detail.get('noticeSendTime', ''),
        '省份': detail.get('provinceName', ''),
        '地市': detail.get('regionName', ''),
        '所属行业': detail.get('industriesType', ''),
        '项目类型': detail.get('tenderProjectType', ''),
        '招标方式': detail.get('tenderMode', ''),
        '浏览量': detail.get('biddingCount', 0),
        '公告类型代码': detail.get('portalBiddingType', ''),
        'PDF链接': detail.get('signaturePdfUrl', ''),
        '数据来源': detail.get('dataSource', ''),
        
        # 项目基本信息
        '项目名称': extract_field(text, [
            r'^项目名称[：:]\s*(.+)$',
            r'^工程名称[：:]\s*(.+)$',
            r'^招标项目名称[：:]\s*(.+)$',
            r'^采购项目名称[：:]\s*(.+)$',
        ]),
        '项目编号': extract_field(text, [
            r'项目编号[：:]\s*([^\n）)]+)',
            r'招标编号[：:]\s*([^\n）)]+)',
            r'采购编号[：:]\s*([^\n）)]+)',
        ]),
        '项目性质': extract_field(text, [
            r'项目性质[：:]\s*([^\n]+)',
        ]),
        '组织形式': extract_field(text, [
            r'组织形式[：:]\s*([^\n]+)',
            r'招标组织形式[：:]\s*([^\n]+)',
        ]),
        '开标时间': extract_field(text, [
            r'开标时间[：:]\s*([^\n]+)',
            r'投标截止时间[：:]\s*([^\n]+)',
            r'递交截止时间[：:]\s*([^\n]+)',
            r'响应文件递交截止时间[：:]\s*([^\n]+)',
        ]),
        '公示开始时间': extract_field(text, [
            r'公示开始时间[：:]\s*([^\n]+)',
        ]),
        '公示结束时间': extract_field(text, [
            r'公示结束时间[：:]\s*([^\n]+)',
        ]),
        '项目总投资': extract_field(text, [
            r'项目总投资[：:]\s*([^\n]+)',
            r'总投资[：:]\s*([^\n]+)',
            r'估算金额[：:]\s*([^\n]+)',
            r'项目估算[：:]\s*([^\n]+)',
        ]),
        '招标金额': extract_field(text, [
            r'招标金额[：:]\s*([^\n]+)',
            r'预算金额[：:]\s*([^\n]+)',
            r'最高限价[：:]\s*([^\n]+)',
            r'采购预算[：:]\s*([^\n]+)',
        ]),
        '资金来源': extract_field(text, [
            r'资金来源[：:]\s*([^\n]+)',
        ]),
        '项目地点': extract_field(text, [
            r'项目地点[：:]\s*([^\n]+)',
            r'建设地点[：:]\s*([^\n]+)',
            r'实施地点[：:]\s*([^\n]+)',
            r'交货地点[：:]\s*([^\n]+)',
        ]),
        '建设内容及规模': extract_field(text, [
            r'建设内容及规模[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．])',
            r'项目规模[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．])',
        ]),
        '招标内容与范围': extract_field(text, [
            r'招标内容[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．]\s*[^\n]*资格)',
            r'招标范围[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．]\s*[^\n]*资格)',
            r'采购范围[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．]\s*[^\n]*资格)',
            r'招标内容与范围[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．]\s*[^\n]*资格)',
        ]),
        '工期': extract_field(text, [
            r'^工期[：:]\s*([^\n]+)',
            r'服务期[：:]\s*([^\n]+)',
            r'供货期[：:]\s*([^\n]+)',
            r'交货期[：:]\s*([^\n]+)',
        ]),
        '质量要求': extract_field(text, [
            r'质量要求[：:]\s*([^\n]+)',
            r'质量标准[：:]\s*([^\n]+)',
            r'质量目标[：:]\s*([^\n]+)',
        ]),
        
        # 招标人信息
        '招标人名称': extract_field(text, [
            r'^招标人[：:]\s*(.+)$',
            r'^招标人名称[：:]\s*(.+)$',
            r'^采购人[：:]\s*(.+)$',
            r'^采购人名称[：:]\s*(.+)$',
            r'^招标人/采购人[：:]\s*(.+)$',
        ]),
        '招标人地址': extract_field(text, [
            r'招标人地址[：:]\s*([^\n]+)',
            r'采购人地址[：:]\s*([^\n]+)',
        ]),
        '招标人联系人': extract_field(text, [
            r'招标人联系人[：:]\s*([^\n]+)',
            r'采购人联系人[：:]\s*([^\n]+)',
            r'项目联系人[：:]\s*([^\n]+)',
        ]),
        '招标人联系方式': extract_field(text, [
            r'招标人联系方式[：:]\s*([^\n]+)',
            r'采购人联系方式[：:]\s*([^\n]+)',
            r'联系电话[：:]\s*([^\n]+)',
            r'招标人联系电话[：:]\s*([^\n]+)',
        ]),
        
        # 招标代理信息
        '招标代理机构': extract_field(text, [
            r'^招标代理机构[：:]\s*(.+)$',
            r'^代理机构[：:]\s*(.+)$',
            r'^采购代理机构[：:]\s*(.+)$',
        ]),
        '招标代理机构地址': extract_field(text, [
            r'招标代理机构地址[：:]\s*([^\n]+)',
            r'代理机构地址[：:]\s*([^\n]+)',
            r'采购代理机构地址[：:]\s*([^\n]+)',
        ]),
        '招标代理机构联系人': extract_field(text, [
            r'招标代理机构联系人[：:]\s*([^\n]+)',
            r'代理机构联系人[：:]\s*([^\n]+)',
            r'采购代理机构联系人[：:]\s*([^\n]+)',
        ]),
        '招标代理机构联系方式': extract_field(text, [
            r'招标代理机构联系方式[：:]\s*([^\n]+)',
            r'代理机构联系方式[：:]\s*([^\n]+)',
            r'代理机构联系电话[：:]\s*([^\n]+)',
            r'采购代理机构联系方式[：:]\s*([^\n]+)',
        ]),
        
        # 投标人资格
        '投标人资格要求': extract_field(text, [
            r'投标人资格要求[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．]\s*[^\n]*(?:文件|获取|发售|报名|递交))',
            r'申请人资格要求[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．]\s*[^\n]*(?:文件|获取|发售|报名|递交))',
            r'资格要求[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．]\s*[^\n]*(?:文件|获取|发售|报名|递交))',
        ]),
        
        # 文件获取信息
        '文件获取时间': extract_field(text, [
            r'文件获取时间[：:]\s*([^\n]+)',
            r'招标文件获取时间[：:]\s*([^\n]+)',
            r'报名时间[：:]\s*([^\n]+)',
            r'发售时间[：:]\s*([^\n]+)',
        ]),
        '获取方式': extract_field(text, [
            r'获取方式[：:]\s*([^\n]+)',
            r'报名方式[：:]\s*([^\n]+)',
        ]),
        '递交方法': extract_field(text, [
            r'递交方法[：:]\s*([^\n]+)',
            r'递交方式[：:]\s*([^\n]+)',
        ]),
        
        # 中标/候选人信息
        '中标人名称': extract_field(text, [
            r'^中标人名称[：:]\s*(.+)$',
            r'^中标单位[：:]\s*(.+)$',
            r'^中标供应商[：:]\s*(.+)$',
            r'^成交供应商[：:]\s*(.+)$',
        ]),
        '中标价': extract_field(text, [
            r'^中标价[：:]\s*(.+)$',
            r'^中标金额[：:]\s*(.+)$',
            r'^成交价[：:]\s*(.+)$',
            r'^成交金额[：:]\s*(.+)$',
        ]),
        '项目经理': extract_field(text, [
            r'^项目经理[：:]\s*(.+)$',
            r'^项目负责人[：:]\s*(.+)$',
        ]),
        
        # 中标候选人
        '第一中标候选人': extract_field(text, [
            r'第一中标候选人[：:]\s*([^\n]+)',
            r'第一名[：:]\s*([^\n]+)',
        ]),
        '第一中标候选人报价': extract_field(text, [
            r'第一中标候选人报价[：:]\s*([^\n]+)',
            r'第一名报价[：:]\s*([^\n]+)',
        ]),
        '第二中标候选人': extract_field(text, [
            r'第二中标候选人[：:]\s*([^\n]+)',
            r'第二名[：:]\s*([^\n]+)',
        ]),
        '第二中标候选人报价': extract_field(text, [
            r'第二中标候选人报价[：:]\s*([^\n]+)',
            r'第二名报价[：:]\s*([^\n]+)',
        ]),
        '第三中标候选人': extract_field(text, [
            r'第三中标候选人[：:]\s*([^\n]+)',
            r'第三名[：:]\s*([^\n]+)',
        ]),
        '第三中标候选人报价': extract_field(text, [
            r'第三中标候选人报价[：:]\s*([^\n]+)',
            r'第三名报价[：:]\s*([^\n]+)',
        ]),
        
        # 监督部门
        '行政监督部门': extract_field(text, [
            r'行政监督部门[：:]\s*([^\n]+)',
            r'监督部门[：:]\s*([^\n]+)',
            r'监管部门[：:]\s*([^\n]+)',
        ]),
        '监督部门联系方式': extract_field(text, [
            r'监督部门联系方式[：:]\s*([^\n]+)',
            r'监督电话[：:]\s*([^\n]+)',
            r'投诉电话[：:]\s*([^\n]+)',
        ]),
        
        # 变更公告
        '公告变更内容': extract_field(text, [
            r'变更内容[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．])',
            r'更正内容[：:]\s*(.+?)(?=\n\s*[一二三四五六七八九十\d]+[、.．])',
        ]),
        
        # 合同履约
        '合同名称': extract_field(text, [
            r'合同名称[：:]\s*([^\n]+)',
        ]),
        '合同金额': extract_field(text, [
            r'合同金额[：:]\s*([^\n]+)',
        ]),
        '合同期限': extract_field(text, [
            r'合同期限[：:]\s*([^\n]+)',
        ]),
        '合同签署时间': extract_field(text, [
            r'合同签署时间[：:]\s*([^\n]+)',
            r'签订时间[：:]\s*([^\n]+)',
        ]),
        
        # 公告正文
        '公告正文': text,
        '原始HTML': html,
    }
    
    return fields


def classify_notice(detail, fields):
    title = detail.get('noticeName', '')
    
    # 按标题关键词分类（优先级从高到低）
    if '合同' in title or '履约' in title:
        return '合同与履约'
    elif '招标计划' in title or '招标方案' in title or '采购计划' in title:
        return '招标计划'
    elif '资格预审' in title:
        return '资格预审公告'
    elif '定标候选人' in title:
        return '定标候选人公示'
    elif '中标候选人' in title or '成交候选人' in title:
        return '中标候选人公示'
    elif '中标结果' in title or '中标公告' in title or '成交公告' in title or '定标结果' in title or '中选公告' in title:
        return '中标结果公示'
    elif '更正' in title or '变更' in title or '修改' in title or '澄清' in title or '补遗' in title or '延期' in title:
        return '更正结果公示'
    elif '招标公告' in title or '采购公告' in title or '竞争性磋商' in title or '询价公告' in title or '询比采购' in title or '比选公告' in title or '谈判公告' in title:
        return '招标公告'
    else:
        # 根据portalBiddingType兜底
        pbt = str(detail.get('portalBiddingType', ''))
        if pbt == '0':
            return '招标公告'
        elif pbt == '1':
            return '中标结果公示'
        elif pbt == '2':
            return '中标候选人公示'
        elif pbt == '4':
            return '更正结果公示'
        else:
            return '其他'


def crawl_all(max_pages=None):
    print(f'开始爬取...')
    start_time = time.time()
    
    # 先获取总数
    first_page = get_list(page=1, limit=100)
    if not first_page:
        print('获取第一页失败')
        return {}
    
    total = first_page.get('total', 0)
    page_size = 100
    pages = (total + page_size - 1) // page_size
    print(f'总条数: {total}, 总页数: {pages}')
    
    if max_pages and max_pages < pages:
        pages = max_pages
        print(f'限制爬取页数: {pages}')
    
    all_details = []
    error_count = 0
    
    # 爬取列表
    for page in range(1, pages + 1):
        elapsed = time.time() - start_time
        print(f'[{elapsed:.0f}s] 正在获取第 {page}/{pages} 页 (已获取{len(all_details)}条, 失败{error_count}条)...')
        
        list_data = get_list(page=page, limit=page_size)
        if not list_data:
            print(f'  第 {page} 页获取失败，跳过')
            error_count += 1
            continue
        
        records = list_data.get('records', [])
        if not records:
            print(f'  第 {page} 页无数据')
            break
        
        # 获取每条的详情
        for i, record in enumerate(records):
            notice_id = record.get('id', '')
            if not notice_id:
                continue
            
            detail = get_detail(notice_id)
            if detail:
                all_details.append(detail)
            else:
                error_count += 1
            
            if (i + 1) % 20 == 0:
                time.sleep(0.5)
        
        time.sleep(0.3)
    
    print(f'\n共获取 {len(all_details)} 条详情，失败 {error_count} 条')
    
    # 解析字段并分类
    result = {
        '招标计划': [],
        '资格预审公告': [],
        '招标公告': [],
        '中标候选人公示': [],
        '定标候选人公示': [],
        '中标结果公示': [],
        '更正结果公示': [],
        '合同与履约': [],
        '其他': [],
    }
    
    for detail in all_details:
        fields = parse_notice_fields(detail)
        notice_type = classify_notice(detail, fields)
        result[notice_type].append(fields)
    
    print('\n分类统计:')
    total_count = 0
    for k, v in result.items():
        print(f'  {k}: {len(v)} 条')
        total_count += len(v)
    print(f'  总计: {total_count} 条')
    
    elapsed = time.time() - start_time
    print(f'\n爬取完成，耗时 {elapsed:.1f} 秒 ({elapsed/60:.1f} 分钟)')
    
    return result


if __name__ == '__main__':
    # 爬取全部数据
    result = crawl_all(max_pages=None)
    
    # 保存结果
    output_file = r'd:\TRAE coding\招标信息爬取结果.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f'\n结果已保存到: {output_file}')
