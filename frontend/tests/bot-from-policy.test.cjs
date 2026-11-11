const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(
  path.join(__dirname, '../src/components/recommendations/botFromPolicy.ts'),
  'utf8',
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { botFromPolicy, traduzirRazao } = context.exports;

function action(key, over = {}) {
  return {
    action_key: key,
    system_recommendable: false,
    requires_external_fact: null,
    reason: null,
    ...over,
  };
}
function space(actions, recommendable, noAction) {
  return {
    episode: { account_id: 1, due_date: '2026-01-01' },
    actions,
    recommendable_actions: recommendable,
    no_action: noAction,
  };
}
const pick = (r) => ({ mood: r.mood, message: r.message });
const NEUTRAL_MOOD = 'pensando';

test('mark_overdue recomendavel -> preocupado, decisao de quem aprova', () => {
  const r = botFromPolicy(space([
    action('account.mark_overdue', { system_recommendable: true }),
    action('account.mark_paid', { requires_external_fact: 'payment_observed' }),
  ], ['account.mark_overdue'], false));
  assert.equal(r.mood, 'preocupado');
  assert.match(r.message, /marcar este título como vencido/);
  assert.match(r.message, /decisão é de quem aprova/);
});

test('escalate_to_human recomendavel -> critico', () => {
  const r = botFromPolicy(space([
    action('escalate_to_human', { system_recommendable: true }),
  ], ['escalate_to_human'], false));
  assert.equal(r.mood, 'critico');
  assert.match(r.message, /atendimento humano/);
});

test('precedencia: escalate_to_human vence mark_overdue, em qualquer ordem', () => {
  const actions = [
    action('account.mark_overdue', { system_recommendable: true }),
    action('escalate_to_human', { system_recommendable: true }),
  ];
  for (const rec of [
    ['account.mark_overdue', 'escalate_to_human'],
    ['escalate_to_human', 'account.mark_overdue'],
  ]) {
    assert.equal(botFromPolicy(space(actions, rec, false)).mood, 'critico');
  }
});

test('invariante violado: lista vazia com no_action=false -> pensando', () => {
  const r = botFromPolicy(space([
    action('account.mark_overdue', { reason: null }),
    action('account.mark_paid', { requires_external_fact: 'payment_observed' }),
  ], [], false));
  assert.equal(r.mood, NEUTRAL_MOOD);
  assert.doesNotMatch(r.message, /indica|recomend/i);
  assert.equal(botFromPolicy(space([], [], false)).mood, NEUTRAL_MOOD);
});

test('invariante violado: lista nao vazia com no_action=true -> pensando', () => {
  const r = botFromPolicy(space([
    action('account.mark_overdue', { system_recommendable: true }),
  ], ['account.mark_overdue'], true));
  assert.equal(r.mood, NEUTRAL_MOOD);
  assert.doesNotMatch(r.message, /indica|recomend/i);
});

test('no_action + already_paid -> feliz com motivo traduzido', () => {
  const r = botFromPolicy(space([
    action('account.mark_overdue', { reason: 'already_paid' }),
    action('account.mark_paid', { reason: 'already_paid' }),
  ], [], true));
  assert.equal(r.mood, 'feliz');
  assert.match(r.message, /Esta conta já foi paga\./);
});

test('no_action + outros motivos conhecidos -> dormindo', () => {
  const expected = {
    not_overdue: /ainda não está em atraso/,
    status_not_open: /não está em aberto/,
    due_date_mismatch: /Data de vencimento não corresponde ao episódio atual\./,
  };
  for (const [reason, re] of Object.entries(expected)) {
    const r = botFromPolicy(space([action('account.mark_overdue', { reason })], [], true));
    assert.equal(r.mood, 'dormindo', reason);
    assert.match(r.message, re, reason);
  }
});

test('fail-closed: motivo desconhecido, ausente ou herdado do prototype -> pensando', () => {
  for (const reason of ['novo_motivo', null, 'constructor', '__proto__', 'toString']) {
    const r = botFromPolicy(space([action('account.mark_overdue', { reason })], [], true));
    assert.equal(r.mood, NEUTRAL_MOOD, String(reason));
  }
  assert.equal(botFromPolicy(space([], [], true)).mood, NEUTRAL_MOOD);
});

test('fail-closed: nunca "recomendavel" sem system_recommendable=true', () => {
  const cases = [
    // listada mas flag falso
    space([action('account.mark_overdue', { system_recommendable: false })], ['account.mark_overdue'], false),
    space([action('escalate_to_human', { system_recommendable: false })], ['escalate_to_human'], false),
    // listada mas sem avaliacao correspondente
    space([], ['account.mark_overdue'], false),
    // flag verdadeiro mas fora da lista -> nao recomenda
    space([action('account.mark_overdue', { system_recommendable: true })], [], false),
    // acao desconhecida / mark_paid recomendada
    space([action('acao.nova', { system_recommendable: true })], ['acao.nova'], false),
    space([action('account.mark_paid', { system_recommendable: true })], ['account.mark_paid'], false),
    // contraditorio: recomendavel e no_action
    space([action('account.mark_overdue', { system_recommendable: true })], ['account.mark_overdue'], true),
    // no_action com mark_overdue recomendavel na avaliacao
    space([action('account.mark_overdue', { system_recommendable: true, reason: 'already_paid' })], [], true),
  ];
  for (const c of cases) {
    const r = botFromPolicy(c);
    assert.equal(r.mood, NEUTRAL_MOOD);
    assert.doesNotMatch(r.message, /indica|recomend/i);
  }
});

test('dias de atraso e valor nao influenciam o resultado', () => {
  const base = space([
    action('account.mark_overdue', { system_recommendable: true }),
  ], ['account.mark_overdue'], false);
  const baseline = pick(botFromPolicy(base));
  for (const [days, amount] of [[0, 0], [1, 1], [365, 1e9], [-5, -1]]) {
    const withFacts = {
      ...base,
      days_overdue: days,
      amount,
      observed_facts: { days_overdue: days, amount },
    };
    assert.deepEqual(pick(botFromPolicy(withFacts)), baseline);
  }
});

test('codigo do modulo nao referencia dias, valor nem moodFromInvoice', () => {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  for (const token of ['days_overdue', 'amount', 'diasParaVencer', 'moodFromInvoice', 'observed_facts']) {
    assert.ok(!code.includes(token), token);
  }
});

test('traduzirRazao preserva textos originais do card', () => {
  assert.equal(traduzirRazao(null), null);
  assert.equal(traduzirRazao('account_paid'), 'Esta conta já foi paga.');
  assert.equal(traduzirRazao('codigo_novo'), 'codigo_novo');
});
