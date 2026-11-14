const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(rel) {
  const source = fs.readFileSync(path.join(__dirname, '../src', rel), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const mod = { exports: {} };
  vm.runInNewContext(compiled, {
    exports: mod.exports,
    require: (id) => {
      if (id === './pagamento') return load('lib/pagamento.ts');
      throw new Error(`import inesperado: ${id}`);
    },
    RegExp,
    Date,
    Number,
    String,
    Set,
  });
  return mod.exports;
}

const {
  MAX_PEDIDOS_CONFERIDOS,
  MSG_SEM_AVISOS,
  selecionarPedidosParaConcluir,
  contarPagamentosParaConcluir,
  montarAvisos,
  totalAvisos,
} = load('lib/avisos.ts');

const plain = (v) => JSON.parse(JSON.stringify(v));
const AGORA = new Date('2026-11-10T12:00:00Z');
const FUTURO = '2026-12-01T00:00:00Z';
const PASSADO = '2026-11-01T00:00:00Z';

function pedido(id, conta, extra = {}) {
  return {
    request_id: id,
    skill_key: 'account.mark_paid',
    status: 'approved',
    target_account_id: conta,
    expires_at: FUTURO,
    ...extra,
  };
}

const contas = [
  { id: 1, status: 'pendente' },
  { id: 2, status: 'pago' },
  { id: 3, status: 'vencido' },
];

test('seleciona so aprovados, validos, de titulo nao pago', () => {
  const lista = [
    pedido(10, 1),
    pedido(11, 2),
    pedido(12, 3, { expires_at: PASSADO }),
    pedido(13, 3, { skill_key: 'account.mark_overdue' }),
    pedido(14, 3, { status: 'pending' }),
    pedido(15, 99),
  ];
  const r = selecionarPedidosParaConcluir(lista, contas, AGORA);
  assert.deepEqual(plain(r.pedidos.map((p) => p.request_id)), [10]);
  assert.equal(r.truncado, false);
});

test('limita aos mais recentes e marca truncado', () => {
  const muitas = [];
  const cs = [];
  for (let i = 1; i <= 12; i += 1) {
    cs.push({ id: i, status: 'pendente' });
    muitas.push(pedido(100 + i, i));
  }
  const r = selecionarPedidosParaConcluir(muitas, cs, AGORA);
  assert.equal(r.pedidos.length, MAX_PEDIDOS_CONFERIDOS);
  assert.equal(r.pedidos[0].request_id, 112);
  assert.equal(r.truncado, true);
});

test('conta so quando decisor conhecido e diferente do usuario', () => {
  const pedidos = [pedido(10, 1), pedido(12, 3)];
  const total = (dec, pode = true, usuario = 7) =>
    contarPagamentosParaConcluir(pedidos, contas, dec, pode, usuario, AGORA);

  assert.equal(total({ 10: 5, 12: 5 }), 2);
  assert.equal(total({ 10: 7, 12: 5 }), 1);
  assert.equal(total({ 10: null }), 0);
  assert.equal(total({ 10: 5, 12: 5 }, false), 0);
  assert.equal(total({ 10: 5, 12: 5 }, true, null), 0);
});

test('montarAvisos: sem nada, sem itens e sem contador', () => {
  const e = { podeVerAprovacoes: true, podeDecidir: true, aprovacoesPendentes: 0, pagamentos: { total: 0, truncado: false } };
  assert.deepEqual(plain(montarAvisos(e)), []);
  assert.equal(totalAvisos([], e), null);
  assert.equal(MSG_SEM_AVISOS, 'Nada esperando por você.');
});

test('montarAvisos: dois itens com destino e singular/plural', () => {
  const e = { podeVerAprovacoes: true, podeDecidir: true, aprovacoesPendentes: 1, pagamentos: { total: 3, truncado: false } };
  const itens = montarAvisos(e);
  assert.equal(itens.length, 2);
  assert.equal(itens[0].texto, '1 aprovação pendente');
  assert.equal(itens[0].destino, '/approvals');
  assert.equal(itens[1].texto, '3 pagamentos aprovados aguardando conclusão');
  assert.equal(itens[1].destino, '/recebiveis');
  assert.equal(totalAvisos(itens, e), 4);
});

test('montarAvisos: truncado informa o limite', () => {
  const e = { podeVerAprovacoes: true, podeDecidir: true, aprovacoesPendentes: null, pagamentos: { total: 2, truncado: true } };
  const itens = montarAvisos(e);
  assert.equal(itens.length, 1);
  assert.match(itens[0].texto, /entre os 10 mais recentes/);
});

test('montarAvisos: fail-closed e permissoes', () => {
  const falha = { podeVerAprovacoes: true, podeDecidir: true, aprovacoesPendentes: null, pagamentos: null };
  assert.deepEqual(plain(montarAvisos(falha)), []);
  assert.equal(totalAvisos([], falha), null);

  const semDecidir = { podeVerAprovacoes: true, podeDecidir: false, aprovacoesPendentes: 2, pagamentos: { total: 5, truncado: false } };
  assert.deepEqual(plain(montarAvisos(semDecidir).map((i) => i.chave)), ['aprovacoes']);

  const semLer = { podeVerAprovacoes: false, podeDecidir: true, aprovacoesPendentes: 2, pagamentos: { total: 5, truncado: false } };
  assert.deepEqual(plain(montarAvisos(semLer)), []);
});
