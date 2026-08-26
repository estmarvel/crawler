import requests
import re
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'http://shanxi.fzbidding.com/bidinfo'
}

# 1. 先访问列表页，获取HTML中的JS文件
url = 'http://shanxi.fzbidding.com/bidinfo'
response = requests.get(url, headers=headers)
print('=== 列表页响应 ===')
print('Status:', response.status_code)
print('Content length:', len(response.text))

# 查找JS文件
js_files = re.findall(r'src=["\']([^"\']*\.js[^"\']*)["\']', response.text)
print('\nJS files:', js_files[:10])

# 查找API相关的字符串
api_patterns = re.findall(r'["\'](/[a-z]+/[a-z]+[^"\']*)["\']', response.text, re.IGNORECASE)
print('\nAPI-like patterns:', list(set(api_patterns))[:20])

# 2. 尝试常见的API路径
common_api_paths = [
    '/api/bid/list',
    '/api/notice/list',
    '/api/bidinfo/list',
    '/gateway/api/bid/list',
    '/api/bid/detail',
    '/api/notice/detail',
]

print('\n=== 尝试常见API路径 ===')
base = 'http://shanxi.fzbidding.com'
for path in common_api_paths:
    try:
        r = requests.get(base + path, headers=headers, timeout=5)
        print(f'{path}: {r.status_code}, len={len(r.text)}, type={r.headers.get("Content-Type", "")}')
        if r.status_code == 200 and 'json' in r.headers.get('Content-Type', ''):
            print('  Response:', r.text[:500])
    except Exception as e:
        print(f'{path}: ERROR - {e}')

# 3. 尝试详情页API
detail_id = '304d5af14f854948842feb692a550f06'
detail_api_paths = [
    f'/api/bid/detail/{detail_id}',
    f'/api/notice/detail/{detail_id}',
    f'/api/bidinfo/detail/{detail_id}',
    f'/gateway/api/bid/detail/{detail_id}',
    f'/api/notice/getById?id={detail_id}',
    f'/api/bid/getById?id={detail_id}',
]

print('\n=== 尝试详情页API ===')
for path in detail_api_paths:
    try:
        r = requests.get(base + path, headers=headers, timeout=5)
        print(f'{path}: {r.status_code}, len={len(r.text)}, type={r.headers.get("Content-Type", "")}')
        if r.status_code == 200 and 'json' in r.headers.get('Content-Type', ''):
            print('  Response:', r.text[:1000])
    except Exception as e:
        print(f'{path}: ERROR - {e}')
