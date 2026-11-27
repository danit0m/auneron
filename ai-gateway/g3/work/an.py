import json, math, collections, statistics as st, sys, os
E = 'C:/Users/Tomaz/Downloads/arquivos gerados/area3-evidence-20261105/'
W = os.path.dirname(os.path.abspath(__file__)) + '/'
runs = {'nothink16': ('aval-qwen-vencidos-20261116', 'cases_v1.json'),
        'think2048_v1': ('aval-qwen-think-v1-20261118', 'cases_v1.json'),
        'think4096_v1': ('aval-qwen-think4096-v1-20261118', 'cases_v1.json'),
        'think4096_v2': ('aval-qwen-think4096-v2-prova-20261118', 'cases_v2.json')}


def cp(k, n, a=0.05):
    def cdf(x, p):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, x + 1))
    lo = 0.0
    hi = 1.0
    if k > 0:
        a_, b_ = 0, 1
        for _ in range(60):
            m = (a_ + b_) / 2
            if 1 - cdf(k - 1, m) < a / 2:
                a_ = m
            else:
                b_ = m
        lo = (a_ + b_) / 2
    if k < n:
        a_, b_ = 0, 1
        for _ in range(60):
            m = (a_ + b_) / 2
            if cdf(k, m) > a / 2:
                a_ = m
            else:
                b_ = m
        hi = (a_ + b_) / 2
    return round(lo, 4), round(hi, 4)


def pct(v, p):
    v = sorted(v)
    return v[max(1, math.ceil(p / 100 * len(v))) - 1]


for name, (d, cf) in runs.items():
    r = json.load(open(E + d + '/results.json'))
    cs = {c['id']: c for c in json.load(open(W + cf))['cases']}
    print('=====', name, r['config'].get('options'), r['metrics']['correct'], '/', r['metrics']['total_cases'])
    cat = collections.defaultdict(lambda: [0, 0, []])
    for x in r['cases']:
        c = cs[x['id']]['category']
        cat[c][0] += 1
        cat[c][1] += x['outcome'] == 'correct'
        if x['outcome'] != 'correct':
            cat[c][2].append((x['id'], x['outcome'], x['error'], cs[x['id']]['days_after_due'], x.get('output_tokens')))
    for c, (n, k, bad) in sorted(cat.items()):
        print(f'  {c:24s} {k}/{n}', bad)
    tok = [x['output_tokens'] for x in r['cases'] if x.get('output_tokens') is not None]
    lat = [x['latency_ms'] for x in r['cases'] if x['latency_ms']]
    if tok:
        print('  tokens n', len(tok), 'mean', round(st.mean(tok), 1), 'p50', pct(tok, 50), 'p95', pct(tok, 95), 'max', max(tok))
    print('  lat p50', pct(lat, 50), 'p95', pct(lat, 95), 'max', max(lat), 'sum_s', sum(lat) / 1000)
    if tok:
        mpt = [x['latency_ms'] / x['output_tokens'] for x in r['cases'] if x['output_tokens'] and x['latency_ms']]
        print('  ms/token mean', round(st.mean(mpt), 1), 'tok/s', round(1000 / st.mean(mpt), 1))
        bycat = collections.defaultdict(list)
        for x in r['cases']:
            if x['output_tokens']:
                bycat[cs[x['id']]['category']].append(x.get('output_tokens'))
        print('  mean tok/cat', {c: round(st.mean(v)) for c, v in sorted(bycat.items())})
        print('  tokens>=3000:', sum(t >= 3000 for t in tok), ' >=3500', sum(t >= 3500 for t in tok), ' >=cap', sum(t >= r['config']['max_output_tokens'] for t in tok))
        # by class
        for cls in ('no_action', 'mark_overdue'):
            v = [x['output_tokens'] for x in r['cases'] if x['expected'] == cls and x['output_tokens']]
            print('  class', cls, 'mean tok', round(st.mean(v)), 'max', max(v))
    n = len(r['cases'])
    k = r['metrics']['correct']
    print('  CP95 acc', cp(k, n), ' CP95 invalid', cp(r['metrics']['invalid_responses'], n))
    print('  first call', r['cases'][0]['latency_ms'], 'memory', r['config']['free_memory_gib_at_preflight'])
print('CP upper 0/40', cp(0, 40)[1], ' 0/80', cp(0, 80)[1], ' 0/120', cp(0, 120)[1], ' 0/60', cp(0, 60)[1], '0/160', cp(0, 160)[1])
print('pooled think4096 159/160', cp(159, 160), 'invalid 1/160', cp(1, 160), '1/80', cp(1, 80))
