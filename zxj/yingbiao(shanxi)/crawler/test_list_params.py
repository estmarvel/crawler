import requests
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'http://shanxi.fzbidding.com/bidinfo',
    'Origin': 'http://shanxi.fzbidding.com'
}

base = 'http://shanxi.fzbidding.com:8001'
list_url = base + '/hz/portal/portalBidding/list'

# 测试不同的参数组合
param_sets = [
    {'moduleId': 3, 'page': 1, 'limit': 10},
    {'moduleId': '1', 'page': 1, 'limit': 10},
    {'moduleId': '0', 'page': 1, 'limit': 10},
    {'page': 1, 'limit': 10},
    {'pageNum': 1, 'pageSize': 10, 'moduleId': 3},
    {'page': 1, 'pageSize': 10, 'moduleId': 3},
]

print('=== 测试列表API参数 ===')
for params in param_sets:
    try:
        r = requests.post(list_url, json=params, headers=headers, timeout=10)
        data = r.json()
        records_count = len(data.get('records', []))
        total = data.get('total', 0)
        print(f'\nParams: {params}')
        print(f'Status: {r.status_code}, total: {total}, records: {records_count}')
        if records_count > 0:
            print('First record keys:', list(data['records'][0].keys()))
            print('First record preview:', json.dumps(data['records'][0], ensure_ascii=False)[:300])
    except Exception as e:
        print(f'Params {params}: ERROR - {e}')

# 测试不同的moduleId
print('\n\n=== 测试不同moduleId ===')
for mid in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
    try:
        r = requests.post(list_url, json={'moduleId': mid, 'page': 1, 'limit': 5}, headers=headers, timeout=10)
        data = r.json()
        total = data.get('total', 0)
        records_count = len(data.get('records', []))
        print(f'moduleId={mid}: total={total}, records={records_count}')
        if records_count > 0:
            first_type = data['records'][0].get('portalBiddingType', '')
            first_name = data['records'][0].get('noticeName', '')[:30]
            print(f'  类型: {first_type}, 名称: {first_name}')
    except Exception as e:
        print(f'moduleId={mid}: ERROR - {e}')
