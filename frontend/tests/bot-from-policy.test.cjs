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
const OVERDUE = 'account.mark_overdue';
const ESCALATE = 'escalate_to_human';
const single = (key) => ({ decision_type: 'single_action', selected_actions: [key] });
const bundle = (...keys) => ({ decision_type: 'action_bundle', selected_actions: keys });
const noAction = () => ({ decision_type: 'no_action', selected_actions: [] });

// Space "real": conta aberta e vencida, as duas acoes recomendaveis.
const bothSpace = () =>
  space(
    [
      action(OVERDUE, { system_recommendable: true }),
      action(ESCALATE, { system_recommendable: true }),
      action('account.mark_paid', { requires_external_fact: 'payment_observed' }),
    ],
    [OVERDUE, ESCALATE],
    false,
  );
const overdueOnlySpace = () =>
  space([action(OVERDUE, { system_recommendable: true })], [OVERDUE], false);
const escalateOnlySpace = () =>
  space([action(ESCALATE, { system_recommendable: true })], [ESCALATE], false);
const pick = (r) => ({ mood: r.mood, message: r.message });
const NEUTRAL_MOOD = 'pensando';

test('REGRESSAO: space com as duas recomendaveis, decisao single mark_overdue -> preocupado', () => {
  const r = botFromPolicy(single(OVERDUE), bothSpace());
  assert.equal(r.mood, 'preocupado');
  assert.match(r.message, /marcar este título como vencido/);
  assert.match(r.message, /decisão é de quem aprova/);
  assert.doesNotMatch(r.message, /atendimento humano/);
});

test('single escalate_to_human -> critico, atendimento humano', () => {
  for (const sp of [escalateOnlySpace(), bothSpace()]) {
    const r = botFromPolicy(single(ESCALATE), sp);
    assert.equal(r.mood, 'critico');
    assert.match(r.message, /atendimento humano/);
    assert.doesNotMatch(r.message, /vencido/);
  }
});

test('action_bundle com as duas acoes -> critico, cita as duas, em qualquer ordem', () => {
  for (const keys of [[OVERDUE, ESCALATE], [ESCALATE, OVERDUE]]) {
    const r = botFromPolicy(bundle(...keys), bothSpace());
    assert.equal(r.mood, 'critico');
    assert.match(r.message, /marcar este título como vencido/);
    assert.match(r.message, /atendimento humano/);
    assert.match(r.message, /A decisão é de quem aprova\.$/);
  }
});

test('invariante do space violado: lista vazia com no_action=false -> pensando', () => {
  const sp = space([action(OVERDUE, { reason: null })], [], false);
  for (const d of [noAction(), single(OVERDUE)]) {
    const r = botFromPolicy(d, sp);
    assert.equal(r.mood, NEUTRAL_MOOD);
    assert.doesNotMatch(r.message, /indica|recomend/i);
  }
  assert.equal(botFromPolicy(noAction(), space([], [], false)).mood, NEUTRAL_MOOD);
});

test('invariante do space violado: lista nao vazia com no_action=true -> pensando', () => {
  const sp = space([action(OVERDUE, { system_recommendable: true })], [OVERDUE], true);
  for (const d of [noAction(), single(OVERDUE)]) {
    const r = botFromPolicy(d, sp);
    assert.equal(r.mood, NEUTRAL_MOOD);
    assert.doesNotMatch(r.message, /indica|recomend/i);
  }
});

test('no_action + already_paid -> feliz com motivo traduzido', () => {
  const r = botFromPolicy(noAction(), space([
    action(OVERDUE, { reason: 'already_paid' }),
    action('account.mark_paid', { reason: 'already_paid' }),
  ], [], true));
  assert.equal(r.mood, 'feliz');
  assert.match(r.message, /Nenhuma ação necessária\./);
  assert.match(r.message, /Esta conta já foi paga\./);
});

test('no_action + outros motivos conhecidos -> dormindo', () => {
  const expected = {
    not_overdue: /ainda não está em atraso/,
    status_not_open: /não está em aberto/,
    due_date_mismatch: /Data de vencimento não corresponde ao episódio atual\./,
  };
  for (const [reason, re] of Object.entries(expected)) {
    const r = botFromPolicy(noAction(), space([action(OVERDUE, { reason })], [], true));
    assert.equal(r.mood, 'dormindo', reason);
    assert.match(r.message, /Nenhuma ação necessária\./, reason);
    assert.match(r.message, re, reason);
  }
});

test('no_action com motivo desconhecido, nulo, ausente ou do prototype -> dormindo, sem rotulo', () => {
  for (const reason of ['novo_motivo', null, 'constructor', '__proto__', 'toString']) {
    const r = botFromPolicy(noAction(), space([action(OVERDUE, { reason })], [], true));
    assert.equal(r.mood, 'dormindo', String(reason));
    assert.equal(r.message, 'Nenhuma ação necessária.', String(reason));
  }
  const semAcao = botFromPolicy(noAction(), space([], [], true));
  assert.equal(semAcao.mood, 'dormindo');
  assert.equal(semAcao.message, 'Nenhuma ação necessária.');
});

test('fail-closed: decision_type incoerente com a lista', () => {
  const sp = bothSpace();
  const cases = [
    { decision_type: 'no_action', selected_actions: [OVERDUE] },
    { decision_type: 'single_action', selected_actions: [] },
    { decision_type: 'single_action', selected_actions: [OVERDUE, ESCALATE] },
    { decision_type: 'action_bundle', selected_actions: [OVERDUE] },
    { decision_type: 'action_bundle', selected_actions: [] },
    { decision_type: 'tipo_novo', selected_actions: [OVERDUE] },
  ];
  for (const d of cases) {
    assert.equal(botFromPolicy(d, sp).mood, NEUTRAL_MOOD, JSON.stringify(d));
  }
});

test('fail-closed: acao selecionada fora de recommendable_actions ou sem system_recommendable', () => {
  const cases = [
    // fora de recommendable_actions (mas com flag verdadeiro)
    [single(ESCALATE), space([
      action(OVERDUE, { system_recommendable: true }),
      action(ESCALATE, { system_recommendable: true }),
    ], [OVERDUE], false)],
    // na lista mas flag falso
    [single(OVERDUE), space([action(OVERDUE, { system_recommendable: false })], [OVERDUE], false)],
    // na lista mas sem avaliacao correspondente
    [single(OVERDUE), space([], [OVERDUE], false)],
    // uma das duas do bundle nao recomendavel
    [bundle(OVERDUE, ESCALATE), overdueOnlySpace()],
    // mark_paid nunca e recomendavel
    [single('account.mark_paid'), space(
      [action('account.mark_paid', { system_recommendable: true })],
      ['account.mark_paid'], false)],
  ];
  for (const [d, sp] of cases) {
    const r = botFromPolicy(d, sp);
    assert.equal(r.mood, NEUTRAL_MOOD, JSON.stringify(d));
    assert.doesNotMatch(r.message, /indica|recomend/i);
  }
});

test('fail-closed: acao desconhecida selecionada', () => {
  const sp = space(
    [action('acao.nova', { system_recommendable: true }), action(OVERDUE, { system_recommendable: true })],
    ['acao.nova', OVERDUE], false);
  assert.equal(botFromPolicy(single('acao.nova'), sp).mood, NEUTRAL_MOOD);
  assert.equal(botFromPolicy(bundle(OVERDUE, 'acao.nova'), sp).mood, NEUTRAL_MOOD);
});

test('fail-closed: item duplicado', () => {
  assert.equal(botFromPolicy(bundle(OVERDUE, OVERDUE), overdueOnlySpace()).mood, NEUTRAL_MOOD);
  assert.equal(botFromPolicy(bundle(OVERDUE, OVERDUE), bothSpace()).mood, NEUTRAL_MOOD);
});

test('fail-closed: decisao no_action com recomendaveis no space', () => {
  for (const sp of [overdueOnlySpace(), bothSpace()]) {
    const r = botFromPolicy(noAction(), sp);
    assert.equal(r.mood, NEUTRAL_MOOD);
    assert.doesNotMatch(r.message, /Nenhuma ação necessária/);
  }
});

test('fail-closed: decisao com acao mas space sem recomendaveis -> pensando', () => {
  const sp = space([action(OVERDUE, { reason: 'already_paid' })], [], true);
  assert.equal(botFromPolicy(single(OVERDUE), sp).mood, NEUTRAL_MOOD);
});

test('dias de atraso e valor nao influenciam o resultado', () => {
  const base = overdueOnlySpace();
  const baseline = pick(botFromPolicy(single(OVERDUE), base));
  for (const [days, amount] of [[0, 0], [1, 1], [365, 1e9], [-5, -1]]) {
    const withFacts = {
      ...base,
      days_overdue: days,
      amount,
      observed_facts: { days_overdue: days, amount },
    };
    assert.deepEqual(pick(botFromPolicy(single(OVERDUE), withFacts)), baseline);
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

test('traduzirRazao traduz motivos de marcacao (mark_overdue / mark_paid)', () => {
  assert.equal(traduzirRazao('status_not_open'), 'Esta conta não está em aberto.');
  assert.equal(traduzirRazao('already_paid'), 'Esta conta já foi paga.');
  assert.equal(traduzirRazao('not_overdue'), 'Esta conta ainda não está em atraso.');
});

test('traduzirRazao mantem os 4 motivos de escalacao inalterados', () => {
  assert.equal(
    traduzirRazao('due_date_mismatch'),
    'Data de vencimento não corresponde ao episódio atual.',
  );
  assert.equal(traduzirRazao('account_paid'), 'Esta conta já foi paga.');
  assert.equal(traduzirRazao('lifecycle_not_overdue'), 'Esta conta não está mais em atraso.');
  assert.equal(
    traduzirRazao('active_escalation_exists'),
    'Este episódio já tem um escalonamento em andamento.',
  );
});

test('traduzirRazao: desconhecido, null e chaves do prototype devolvem o proprio valor', () => {
  assert.equal(traduzirRazao('motivo_inexistente'), 'motivo_inexistente');
  assert.equal(traduzirRazao(null), null);
  assert.equal(traduzirRazao('__proto__'), '__proto__');
  assert.equal(traduzirRazao('toString'), 'toString');
});
