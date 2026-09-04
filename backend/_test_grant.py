import requests
BASE = 'http://127.0.0.1:8000'
r = requests.post(f'{BASE}/auth/login', json={'username': 'admin', 'password': 'Admin@1234'})
token = r.json()['access_token']
H = {'Authorization': f'Bearer {token}'}

# Get existing customers
r2 = requests.get(f'{BASE}/customers', headers=H)
print(f'Customers: {r2.status_code}')
if r2.status_code == 200:
    custs = r2.json()
    for c in custs[:2]:
        print(f'  {c.get("external_id")} - {c.get("name")}')

# Pick first customer ext_id
ext_id = custs[0]['external_id'] if custs else None
if ext_id:
    r3 = requests.get(f'{BASE}/consents/summary/{ext_id}', headers=H)
    print(f'\nSummary: {r3.status_code}')
    if r3.status_code == 200:
        cons = r3.json().get('consents', [])
        print(f'Consents: {len(cons)}')
        if cons:
            cid = cons[0]['id']
            r4 = requests.post(f'{BASE}/consents/{cid}/grant', headers=H, json={
                'collection_method': 'UI',
                'consent_text': 'I consent to data collection',
                'affirmative_action': 'CLICK',
                'client_context': {'language': 'en', 'screen_id': 'consent_portal', 'ui_control_id': 'consent_accept_marketing'},
                'notice_id': 2,
            })
            print(f'Grant: {r4.status_code}')
            if r4.status_code == 200:
                r5 = requests.get(f'{BASE}/consents/{cid}', headers=H)
                ev = r5.json().get('evidence', [])
                for e in ev[-2:]:
                    print(f'  Evidence: {e.get("evidence_ref")}')
                    print(f'    notice_id={e.get("notice_id")} terms_hash={e.get("terms_hash","")[:16]} sig={"YES" if e.get("signature") else "NO"}')
            else:
                print(f'  Error: {r4.text[:200]}')
