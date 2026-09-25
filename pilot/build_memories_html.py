import json, sys, yaml, collections
import subprocess
# Regenerate with: .venv/bin/python pilot/build_memories_html.py [table_prefix] [out.html] [as-of date]   (reads the ledger from the docker Postgres)
# Output contains real users' messages: keep it private.
PREFIX = sys.argv[1] if len(sys.argv) > 1 else 'habitantes'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'pilot/run6_memories.html'
SQL = ("select coalesce(json_agg(row_to_json(t)),'[]') from (select memory_id, version, type, status, scope, user_id, content, "
       "payload, evidence, assertion, durability, observed_at, valid_until, links, seen_count, verified, conflicts_with, created_by "
       f"from {PREFIX}_memory order by memory_id, version) t")
rows = json.loads(subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'memhub', '-d', 'memhub', '-At', '-c', SQL],
                                 capture_output=True, text=True, check=True).stdout)
cfg = yaml.safe_load(open('memhub.yaml'))
seeds = {a['key']: a for a in cfg['areas']['seeds']}
LABEL = {'nationality': 'Nationality', 'city': 'City', 'age': 'Age', 'residence_status': 'Residence',
         'studies': 'Studies', 'work': 'Work', 'family': 'Family', 'documents': 'Documents', 'goal': 'Goal',
         'language': 'Language', 'scope': 'Scope', 'style': 'Style', 'detail': 'Length & detail', 'format': 'Format',
         'emoji': 'Emoji', 'tone': 'Tone', 'sources': 'Sources', 'address': 'Call them'}
labels = {t: {k: LABEL.get(k, k.replace('_', ' ').title()) for k in cfg['types'][t].get('keys', {})} for t in ('profile', 'preference') if t in cfg['types']}
by_mem = collections.defaultdict(list)
for r in rows: by_mem[r['memory_id']].append(r)
items = []
for mid, vs in by_mem.items():
    vs.sort(key=lambda r: r['version'])
    cur = next((v for v in reversed(vs) if v['status'] in ('active', 'candidate', 'archived')), None)
    if not cur: continue
    ev = cur['evidence'][0] if cur['evidence'] else {}
    p = cur['payload']
    areas_of = [l['memory_id'] for l in cur['links'] if l['kind'] == 'in_area']  # a fact may sit in up to three areas
    area = areas_of[0] if areas_of else None
    it = {'id': mid, 'type': cur['type'], 'status': cur['status'], 'scope': cur['scope'],
          'user': (cur['user_id'] or '')[:10], 'key': p.get('key'), 'content': cur['content'],
          'observed': (ev.get('observed_at') or cur['observed_at'] or '')[:10] or (cur['observed_at'] or '')[:10],
          'valid_until': (cur['valid_until'] or '')[:10] or None, 'assertion': cur['assertion'],
          'verified': cur['verified'], 'seen': cur['seen_count'], 'quote': ev.get('quote'),
          'claim_source': ev.get('claim_source'), 'area': area, 'areas': areas_of, 'conflict': cur['conflicts_with'],
          'version': cur['version'],
          'history': [{'v': v['version'], 'content': v['content'], 'status': v['status'], 'by': v['created_by'],
                       'observed': (v['observed_at'] or '')[:10]} for v in vs]}
    if cur['type'] == 'area':
        it['title'] = p.get('title'); it['summary'] = p.get('summary') or ''
        it['icon'] = seeds.get(p.get('key'), {}).get('icon', '📁'); it['proposed'] = p.get('proposed', False)
    if cur['type'] == 'case':
        it.update({k: p.get(k) for k in ('symptom', 'root_cause', 'action', 'outcome')}); it['by'] = cur['created_by']
    if cur['type'] == 'episode':
        it['situation'] = p.get('situation'); it['outcome'] = p.get('outcome')
    if cur['type'] == 'term':
        it['aliases'] = p.get('aliases', []); it['related'] = p.get('related', [])
    items.append(it)
areas = {i['id'] for i in items if i['type'] == 'area'}
for i in items:
    for a in i['areas']: assert a in areas, i
users = sorted({i['user'] for i in items if i['user']})
data = {'asof': sys.argv[3] if len(sys.argv) > 3 else '2026-10-20', 'users': users, 'items': items, 'labels': labels,
        'order': {t: list(labels[t]) for t in labels},
        'strict': {t: cfg['types'][t].get('strict_keys', True) and bool(cfg['types'][t].get('keyed')) for t in labels}}  # Profile is free text: no key list, every row shown
out = open('pilot/memories_template.html').read().replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
open(OUT, 'w').write(out)
print(len(users), 'users', len(items), 'items', collections.Counter(i['type'] for i in items))
