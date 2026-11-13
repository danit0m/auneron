const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(rel, requireFn) {
  const source = fs.readFileSync(path.join(__dirname, '../src', rel), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const mod = { exports: {} };
  vm.runInNewContext(compiled, { exports: mod.exports, require: requireFn });
  return mod.exports;
}

const lib = load('lib/inteligencia.ts', (id) => {
  throw new Error(`import inesperado: ${id}`);
});
const {
  montarFunil,
  calcularTaxaAcerto,
  calcularMetricas,
  formatarDuracao,
  formatarTaxa,
  humorDaAprovacao,
} = lib;

const AGORA = new Date('2026-11-13T12:00:00Z');
const horasAtras = (h) => new Date(AGORA.getTime() - h * 3_600_000).toISOString();

function aprov(over) {
  return { request_id: 1, status: 'pending', created_at: horasAtras(1), resolved_at: null, ...over };
}

function resumo(over = {}) {
  return {
    eligible_accounts_identified: 10,
    autonomous_dispositions: 6,
    human_governed_dispositions: 3,
    autonomous_effect_verification: { verified: 4, checked_other: 1, not_yet_checked: 1, total: 6, verification_rate: 0.66 },
    ...over,
  };
}

test('funil: completo, com larguras proporcionais', () => {
  const f = montarFunil(resumo());
  assert.deepEqual([...f.map((e) => [e.chave, e.valor])], [
    ['identificadas', 10], ['autonomas', 6], ['humanas', 3], ['verificadas', 4],
  ]);
  assert.equal(f[0].percentual, 100);
  assert.equal(f[1].percentual, 60);
});

test('funil: vazio e nulo', () => {
  assert.equal(montarFunil(null).length, 0);
  assert.equal(montarFunil({}).length, 0);
});

test('funil: tudo zero não divide por zero', () => {
  const f = montarFunil(resumo({
    eligible_accounts_identified: 0, autonomous_dispositions: 0, human_governed_dispositions: 0,
    autonomous_effect_verification: { verified: 0, checked_other: 0, not_yet_checked: 0, total: 0, verification_rate: null },
  }));
  assert.equal(f.length, 4);
  assert.ok(f.every((e) => e.valor === 0 && e.percentual === 0));
});

test('funil: campos ausentes omitem a etapa', () => {
  const f = montarFunil({ eligible_accounts_identified: 5, human_governed_dispositions: 2 });
  assert.deepEqual([...f.map((e) => e.chave)], ['identificadas', 'humanas']);
});

test('taxa de acerto: só com verificadas > 0', () => {
  assert.equal(calcularTaxaAcerto(resumo()), 0.8);
  const zero = resumo({
    autonomous_effect_verification: { verified: 0, checked_other: 2, not_yet_checked: 0, total: 2, verification_rate: 0 },
  });
  assert.equal(calcularTaxaAcerto(zero), null);
  assert.equal(calcularTaxaAcerto({}), null);
  assert.equal(calcularTaxaAcerto(null), null);
});

test('métricas: lista vazia', () => {
  const m = calcularMetricas([], AGORA);
  assert.deepEqual({ ...m }, { tempoMedioMs: null, decididas: 0, rejeitadas: 0, paradas: 0, pendentes: 0 });
});

test('métricas: paradas > 24 h, fronteira exclusiva', () => {
  const m = calcularMetricas([
    aprov({ request_id: 1, created_at: horasAtras(24) }),
    aprov({ request_id: 2, created_at: horasAtras(24.01) }),
    aprov({ request_id: 3, created_at: horasAtras(23.9) }),
  ], AGORA);
  assert.equal(m.pendentes, 3);
  assert.equal(m.paradas, 1);
});

test('métricas: tempo médio e rejeitadas', () => {
  const m = calcularMetricas([
    aprov({ status: 'approved', created_at: horasAtras(10), resolved_at: horasAtras(9) }),
    aprov({ status: 'rejected', created_at: horasAtras(10), resolved_at: horasAtras(7) }),
    aprov({ status: 'expired', created_at: horasAtras(50), resolved_at: horasAtras(2) }),
  ], AGORA);
  assert.equal(m.decididas, 2);
  assert.equal(m.rejeitadas, 1);
  assert.equal(m.tempoMedioMs, 2 * 3_600_000);
});

test('métricas: timestamps ausentes ou inválidos são ignorados', () => {
  const m = calcularMetricas([
    aprov({ status: 'approved', created_at: horasAtras(5), resolved_at: null }),
    aprov({ status: 'approved', created_at: 'lixo', resolved_at: horasAtras(1) }),
    aprov({ status: 'approved', created_at: horasAtras(1), resolved_at: horasAtras(5) }),
    aprov({ status: 'pending', created_at: 'lixo' }),
  ], AGORA);
  assert.equal(m.decididas, 0);
  assert.equal(m.tempoMedioMs, null);
  assert.equal(m.paradas, 0);
  assert.equal(m.pendentes, 1);
});

test('formatação', () => {
  assert.equal(formatarDuracao(null), '—');
  assert.equal(formatarDuracao(10_000), 'menos de 1 min');
  assert.equal(formatarDuracao(45 * 60_000), '45 min');
  assert.equal(formatarDuracao(200 * 60_000), '3 h 20 min');
  assert.equal(formatarDuracao(3 * 3_600_000), '3 h');
  assert.equal(formatarDuracao(52 * 3_600_000), '2 d 4 h');
  assert.equal(formatarTaxa(0.8), '80%');
});

test('humor por risco', () => {
  assert.equal(humorDaAprovacao('critical'), 'critico');
  assert.equal(humorDaAprovacao('high'), 'preocupado');
  assert.equal(humorDaAprovacao('medium'), 'atento');
  assert.equal(humorDaAprovacao('low'), 'feliz');
  assert.equal(humorDaAprovacao(undefined), 'feliz');
});
