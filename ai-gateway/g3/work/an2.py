import json
E = 'C:/Users/Tomaz/Downloads/arquivos gerados/area3-evidence-20261105/'
a = {c['id']: c for c in json.load(open(E + 'aval-qwen-think-v1-20261118/results.json'))['cases']}
b = {c['id']: c for c in json.load(open(E + 'aval-qwen-think4096-v1-20261118/results.json'))['cases']}
same = diff = 0
for i in a:
    if a[i]['outcome'] == 'correct':
        if a[i]['output_tokens'] == b[i]['output_tokens'] and a[i]['thinking_length'] == b[i]['thinking_length']:
            same += 1
        else:
            diff += 1
print('valid in 2048 run:', same + diff, 'same tokens+thinking in 4096 run:', same, 'diff:', diff)
rec = [(i, b[i]['output_tokens']) for i in a if a[i]['outcome'] != 'correct']
print('13 truncated at 2048 -> tokens at 4096:', sorted(t for _, t in rec))
lat_a = [a[i]['latency_ms'] for i in a if a[i]['outcome'] == 'correct']
# latency of identical-token cases across runs
import statistics
pairs = [(a[i]['latency_ms'], b[i]['latency_ms']) for i in a if a[i]['outcome'] == 'correct' and a[i]['output_tokens'] == b[i]['output_tokens']]
print('latency ratio run2048/run4096 median:', round(statistics.median(x / y for x, y in pairs), 2), 'n', len(pairs))
print('first call 2048 run', a[min(a)]['latency_ms'])
print('output tokens vs latency: first call in order', [c['latency_ms'] for c in json.load(open(E + 'aval-qwen-think-v1-20261118/results.json'))['cases'][:3]])
c = json.load(open(E + 'aval-qwen-think4096-v2-prova-20261118/results.json'))['cases']
top = sorted(c, key=lambda x: -x['output_tokens'])[:5]
print('v2 top tokens:', [(x['id'], x['output_tokens'], x['outcome']) for x in top])
