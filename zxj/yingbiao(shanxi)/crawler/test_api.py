import requests
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'http://shanxi.fzbidding.com/bidinfo',
    'Origin': 'http://shanxi.fzbidding.com'
}

base = 'http://shanxi.fzbidding.com:8001'

# 1. 测试列表API
print('=== 测试列表API ===')
list_url = base + '/hz/portal/portalBidding/list'

# 尝试不同的参数组合
param_sets = [
    {'pageNum': 1, 'pageSize': 10},
    {'page': 1, 'size': 10},
    {'pageNo': 1, 'pageSize': 10},
    {'current': 1, 'size': 10},
]

for params in param_sets:
    try:
        r = requests.post(list_url, json=params, headers=headers, timeout=10)
        print(f'\nParams: {params}')
        print(f'Status: {r.status_code}, Content-Type: {r.headers.get("Content-Type", "")}')
        if 'json' in r.headers.get('Content-Type', ''):
            data = r.json()
            print(f'Response keys: {list(data.keys()) if isinstance(data, dict) else "not dict"}')
            print(f'Data preview: {json.dumps(data, ensure_ascii=False)[:800]}')
            break
    except Exception as e:
        print(f'Params {params}: ERROR - {e}')

# 也试试GET方式
print('\n\n=== 尝试GET方式 ===')
try:
    r = requests.get(list_url, params={'pageNum': 1, 'pageSize': 10}, headers=headers, timeout=10)
    print(f'Status: {r.status_code}')
    print(f'Response: {r.text[:500]}')
except Exception as e:
    print(f'ERROR: {e}')

# 2. 测试详情API
print('\n\n=== 测试详情API ===')
detail_id = '304d5af14f854948842feb692a550f06'
detail_url = base + '/hz/portal/portalBidding/detail'

detail_param_sets = [
    {'id': detail_id},
    {'noticeId': detail_id},
    {'bidId': detail_id},
]

for params in detail_param_sets:
    try:
        r = requests.post(detail_url, json=params, headers=headers, timeout=10)
        print(f'\nParams: {params}')
        print(f'Status: {r.status_code}, Content-Type: {r.headers.get("Content-Type", "")}')
        if 'json' in r.headers.get('Content-Type', ''):
            data = r.json()
            print(f'Response keys: {list(data.keys()) if isinstance(data, dict) else "not dict"}')
            print(f'Data preview: {json.dumps(data, ensure_ascii=False)[:1500]}')
            break
    except Exception as e:
        print(f'Params {params}: ERROR - {e}')
