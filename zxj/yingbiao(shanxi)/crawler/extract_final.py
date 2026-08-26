import json
import re
import os
from bs4 import BeautifulSoup

def clean_html_smart(html):
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n')
    raw_lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    merged = []
    for line in raw_lines:
        if merged:
            prev = merged[-1]
            if (len(prev) <= 3 and not re.search(r'[。；！？\n]$', prev)) or \
               (len(line) <= 2 and not re.search(r'^[一二三四五六七八九十\d]', line)):
                if re.search(r'[：:]\s*$', prev) and len(prev) > 2:
                    merged.append(line)
                elif re.search(r'[：:]', line) and len(line) > 3:
                    merged.append(line)
                elif line in [':', '：']:
                    merged.append(line)
                else:
                    merged[-1] = prev + line
                    continue
        merged.append(line)
    
    final = []
    for line in merged:
        if final and len(final[-1]) <= 4 and not re.search(r'[：:。，；]$', final[-1]) and len(line) > 0:
            if not re.match(r'^[一二三四五六七八九十\d]+[、.]', line):
                final[-1] += line
                continue
        final.append(line)
    
    return '\n'.join(final)

def normalize_text(s):
    return re.sub(r'\s+', '', s)

def clean_value(val):
    """清理提取的值"""
    if not val:
        return ''
    val = val.strip()
    # 去除前导冒号
    val = re.sub(r'^[：:]+', '', val).strip()
    # 去除前导标点
    val = re.sub(r'^[、,\s]+', '', val).strip()
    # 如果值本身像字段名（含冒号且很短），清空
    if re.search(r'[：:]', val) and len(val) < 15:
        # 取冒号后的部分
        m = re.match(r'^.*[：:]\s*(.+)$', val)
        if m and m.group(1).strip():
            val = m.group(1).strip()
        else:
            return ''
    # 去除尾部签名盖章等
    val = re.sub(r'（签名）|（盖章）|（签章）', '', val).strip()
    # 去除尾部括号
    val = re.sub(r'[（(]\s*[）)]\s*$', '', val).strip()
    return val if val and val not in ['无', '/', '-', '（签名）', '（盖章）'] else ''

def find_section_info(lines, section_keywords):
    result = {'value': '', '地址': '', '联系人': '', '电话': ''}
    found_start = -1
    found_end = -1
    
    for i, line in enumerate(lines):
        norm = normalize_text(line)
        for kw in section_keywords:
            norm_kw = normalize_text(kw)
            if norm_kw in norm:
                if kw in ['采购人', '招标人', '招标人/采购人', '招标人名称']:
                    if '代理' in norm:
                        continue
                m = re.match(rf'^.*{re.escape(kw)}.*[：:]\s*(.+)$', line)
                if m and m.group(1).strip() and len(m.group(1).strip()) > 1:
                    val = clean_value(m.group(1).strip())
                    if val and '签名' not in val and '盖章' not in val:
                        result['value'] = val
                        found_start = i
                        found_end = i
                        break
                if re.search(r'[：:]$', line) and i + 1 < len(lines):
                    val = clean_value(lines[i + 1].strip())
                    if val and '签名' not in val and '盖章' not in val:
                        result['value'] = val
                        found_start = i
                        found_end = i + 1
                        break
                if i + 1 < len(lines) and lines[i + 1].strip() in [':', '：']:
                    if i + 2 < len(lines):
                        val = clean_value(lines[i + 2].strip())
                        if val and '签名' not in val and '盖章' not in val:
                            result['value'] = val
                            found_start = i
                            found_end = i + 2
                            break
        if found_start >= 0:
            break
    
    if found_start < 0:
        return result
    
    stop_keywords_norm = [normalize_text(x) for x in ['招标人', '采购人', '代理机构', '招标代理', '采购代理', '监督部门', '监管部门', '行政监督']]
    stop_keywords_norm = [x for x in stop_keywords_norm if x not in [normalize_text(k) for k in section_keywords]]
    
    for j in range(found_end + 1, min(found_end + 30, len(lines))):
        line = lines[j]
        norm = normalize_text(line)
        
        for stop_kw in stop_keywords_norm:
            if stop_kw in norm and re.search(r'[：:]', line):
                return result
        
        if not result['地址']:
            for addr_kw in ['地址', '通讯地址', '联系地址']:
                if normalize_text(addr_kw) in norm:
                    m = re.match(rf'^.*{re.escape(addr_kw)}.*[：:]\s*(.+)$', line)
                    if m and m.group(1).strip():
                        val = clean_value(m.group(1).strip())
                        if val and not re.search(r'[：:]', val):
                            result['地址'] = val
                            break
                    if re.search(r'[：:]$', line) and j + 1 < len(lines):
                        val = clean_value(lines[j + 1].strip())
                        if val and not re.search(r'[：:]', val):
                            result['地址'] = val
                            break
                    if j + 1 < len(lines) and lines[j + 1].strip() in [':', '：']:
                        if j + 2 < len(lines):
                            val = clean_value(lines[j + 2].strip())
                            if val:
                                result['地址'] = val
                                break
        
        if not result['联系人']:
            for ct_kw in ['联系人', '项目负责人']:
                if normalize_text(ct_kw) in norm:
                    m = re.match(rf'^.*{re.escape(ct_kw)}.*[：:]\s*(.+)$', line)
                    if m and m.group(1).strip():
                        val = clean_value(m.group(1).strip())
                        if val and not re.search(r'[：:]', val) and len(val) > 1:
                            result['联系人'] = val
                            break
                    if re.search(r'[：:]$', line) and j + 1 < len(lines):
                        val = clean_value(lines[j + 1].strip())
                        if val and not re.search(r'[：:]', val) and len(val) > 1:
                            result['联系人'] = val
                            break
                    if j + 1 < len(lines) and lines[j + 1].strip() in [':', '：']:
                        if j + 2 < len(lines):
                            val = clean_value(lines[j + 2].strip())
                            if val and len(val) > 1:
                                result['联系人'] = val
                                break
        
        if not result['电话']:
            for ph_kw in ['联系电话', '联系方式', '电话']:
                if normalize_text(ph_kw) in norm:
                    m = re.match(rf'^.*{re.escape(ph_kw)}.*[：:]\s*(.+)$', line)
                    if m and m.group(1).strip():
                        val = clean_value(m.group(1).strip())
                        if val and not re.search(r'[：:]', val):
                            result['电话'] = val
                            break
                    if re.search(r'[：:]$', line) and j + 1 < len(lines):
                        val = clean_value(lines[j + 1].strip())
                        if val and not re.search(r'[：:]', val):
                            result['电话'] = val
                            break
                    if j + 1 < len(lines) and lines[j + 1].strip() in [':', '：']:
                        if j + 2 < len(lines):
                            val = clean_value(lines[j + 2].strip())
                            if val:
                                result['电话'] = val
                                break
    
    return result

def find_multiformat(lines, keywords):
    for i, line in enumerate(lines):
        norm = normalize_text(line)
        for kw in keywords:
            norm_kw = normalize_text(kw)
            if norm_kw in norm:
                m = re.match(rf'^.*{re.escape(kw)}.*[：:]\s*(.+)$', line)
                if m and m.group(1).strip():
                    return clean_value(m.group(1).strip())
                if i + 1 < len(lines) and lines[i + 1].strip() in [':', '：']:
                    if i + 2 < len(lines) and lines[i + 2].strip():
                        return clean_value(lines[i + 2].strip())
                if i + 1 < len(lines) and re.search(r'[：:]$', line):
                    return clean_value(lines[i + 1].strip())
    return ''

def find_block_content(lines, keywords, max_lines=15):
    for i, line in enumerate(lines):
        norm = normalize_text(line)
        for kw in keywords:
            norm_kw = normalize_text(kw)
            if norm_kw in norm:
                m = re.match(rf'^.*{re.escape(kw)}.*[：:]\s*(.+)$', line)
                if m and m.group(1).strip():
                    return clean_value(m.group(1).strip())
                if i + 1 < len(lines) and lines[i + 1].strip() in [':', '：']:
                    block = []
                    for j in range(i + 2, min(i + 2 + max_lines, len(lines))):
                        nl = lines[j].strip()
                        if nl in [':', '：']:
                            continue
                        if re.match(r'^[^：:\s]{2,15}[：:]', nl) and len(nl) < 40:
                            break
                        if nl:
                            block.append(nl)
                    if block:
                        return '\n'.join(block)
                if i + 1 < len(lines) and re.search(r'[：:]$', line):
                    block = []
                    for j in range(i + 1, min(i + 1 + max_lines, len(lines))):
                        nl = lines[j].strip()
                        if re.match(r'^[^：:\s]{2,15}[：:]', nl) and len(nl) < 40:
                            break
                        if nl:
                            block.append(nl)
                    if block:
                        return '\n'.join(block)
    return ''

def find_attachments_in_html(html):
    attachments = []
    if not html:
        return attachments
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True)
        if any(href.lower().split('?')[0].endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar']):
            if href.startswith('http'):
                attachments.append({'url': href, 'name': text or os.path.basename(href)})
    seen = set()
    unique = []
    for att in attachments:
        if att['url'] not in seen:
            seen.add(att['url'])
            unique.append(att)
    return unique

def parse_21_fields(html, detail_info):
    text = clean_html_smart(html)
    lines = text.split('\n')
    
    tenderer = find_section_info(lines, ['招标人', '采购人', '招标人/采购人', '招标人名称'])
    agent = find_section_info(lines, ['招标代理机构', '采购代理机构', '代理机构'])
    supervisor = find_section_info(lines, ['监督部门', '监管部门', '行政监督部门'])
    
    fields = {
        '公共类型': find_multiformat(lines, ['公共类型', '公告类型', '变更类型', '更正类型']),
        '项目名称': find_multiformat(lines, ['项目名称', '工程名称', '招标项目名称', '采购项目名称']) or detail_info.get('noticeName', ''),
        '所属行业': detail_info.get('industriesType', '') or find_multiformat(lines, ['所属行业', '行业分类', '行业类型']),
        '组织形式': find_multiformat(lines, ['组织形式', '招标组织形式']),
        '开标时间': find_multiformat(lines, ['开标时间', '投标截止时间', '递交截止时间', '响应文件递交截止时间', '开启时间']),
        '标书发售时间': find_multiformat(lines, ['标书发售时间', '发售时间', '文件获取时间', '招标文件获取时间', '报名时间', '获取时间']),
        '公告内容': find_block_content(lines, ['公告内容', '变更内容', '更正内容', '修改内容', '一、内容', '一、变更', '一、更正']) or text[:3000],
        '招标人地址': tenderer.get('地址', ''),
        '招标人联系人': tenderer.get('联系人', ''),
        '招标人联系方式': tenderer.get('电话', ''),
        '招标代理机构': agent.get('value', ''),
        '招标代理机构地址': agent.get('地址', ''),
        '招标代理机构联系人': agent.get('联系人', ''),
        '招标代理机构联系方式': agent.get('电话', ''),
        '监督部门地址': supervisor.get('地址', ''),
        '监督部门联系人': supervisor.get('联系人', ''),
        '监督部门联系方式': supervisor.get('电话', '') or find_multiformat(lines, ['监督电话', '投诉电话']),
        '依据文件': find_multiformat(lines, ['依据文件', '政策依据', '法规依据', '法律依据']),
        '依据文号': find_multiformat(lines, ['依据文号', '批准文号']),
        '发布日期': detail_info.get('noticeSendTime', '') or find_multiformat(lines, ['发布时间', '发布日期']),
        '发布网站': '山西招标采购服务平台',
    }
    
    return fields

def main():
    input_file = r'd:\TRAE coding\招标信息爬取结果.json'
    print(f'读取数据: {input_file}')
    with open(input_file, 'r', encoding='utf-8') as f:
        old_result = json.load(f)
    
    all_items = []
    for notice_type, items in old_result.items():
        for item in items:
            html = item.get('原始HTML', '')
            detail_info = {
                'id': item.get('id', ''),
                'noticeName': item.get('公告标题', ''),
                'noticeSendTime': item.get('发布时间', ''),
                'industriesType': item.get('所属行业', ''),
                'signaturePdfUrl': item.get('PDF链接', ''),
            }
            all_items.append({'html': html, 'detail_info': detail_info})
    
    print(f'共读取 {len(all_items)} 条数据')
    
    results = []
    for i, item in enumerate(all_items):
        if (i + 1) % 1000 == 0:
            print(f'  正在解析第 {i+1}/{len(all_items)} 条...')
        
        html = item['html']
        detail_info = item['detail_info']
        item_id = detail_info.get('id', '')
        
        fields = parse_21_fields(html, detail_info)
        fields['id'] = item_id
        
        attachments = []
        pdf_url = detail_info.get('signaturePdfUrl', '')
        if pdf_url:
            local_path = os.path.join(r'd:\TRAE coding\附件', f"{item_id}_0.pdf")
            attachments.append({'url': pdf_url, 'name': '签名PDF', 'type': 'pdf', 'local_path': local_path if os.path.exists(local_path) else None})
        html_attachments = find_attachments_in_html(html)
        attachments.extend(html_attachments)
        fields['附件列表'] = attachments
        
        results.append(fields)
    
    print(f'\n=== 字段覆盖率统计 (共{len(results)}条) ===')
    field_names = [k for k in results[0].keys() if k not in ('id', '附件列表')]
    for field in field_names:
        non_empty = sum(1 for r in results if r.get(field) and str(r.get(field, '')).strip())
        rate = non_empty / len(results) * 100
        print(f'  {field}: {rate:.1f}% ({non_empty}/{len(results)})')
    
    has_attach = sum(1 for r in results if r.get('附件列表'))
    total_attach = sum(len(r.get('附件列表', [])) for r in results)
    downloaded = sum(1 for r in results for a in r.get('附件列表', []) if a.get('local_path'))
    print(f'\n有附件的公告: {has_attach}/{len(results)}')
    print(f'附件总数: {total_attach}')
    print(f'已下载: {downloaded}')
    
    output_file = r'd:\TRAE coding\招标信息_21字段.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存到: {output_file}')

if __name__ == '__main__':
    main()
