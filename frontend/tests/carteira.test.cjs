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

const cockpit = load('lib/cockpit.ts', () => ({}));
const receb = load('lib/recebiveis.ts', (id) => {
  if (id === './cockpit') return cockpit;
  throw new Error(`import inesperado: ${id}`);
});
const lib = load('lib/carteira.ts', (id) => {
  if (id === './recebiveis') return receb;
  if (id === './cockpit') return cockpit;
  throw new Error(`import inesperado: ${id}`);
});
const {
  agruparClientes,
  faixaPorPiorAtraso,
  resumirFaixasCarteira,
  calcularConcentracao,
  calcularSemContato,
  montarSaudeCarteira,
  pluralizarClientes,
  situacaoConta,
  filtrarContas,
  resumirCadastro,
} = lib;

const HOJE = new Date(2026, 10, 12, 15, 30);
const plain = (x) => JSON.parse(JSON.stringify(x));

function iso(deslocamento) {
  const d = new Date(2026, 10, 12 + deslocamento);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

let seq = 0;
// atraso > 0 => vencida ha N dias; atraso <= 0 => vence em -atraso dias.
function t(cliente, atraso, valor, over = {}) {
  seq += 1;
  return {
    id: seq,
    cliente,
    email: 'a@b.com',
    whatsapp: null,
    valor,
    vencimento: iso(-atraso),
    status: atraso > 0 ? 'atrasado' : 'aberto',
    ...over,
  };
}

function cliente(contas, nome) {
  return agruparClientes(contas, HOJE).find((c) => c.nome === nome);
}

function cliente(contas, nome) {
  return agruparClientes(contas, HOJE).find((c) => c.nome === nome);
}

function muitos(n, valor = 10, atraso = 0) {
  return Array.from({ length: n }, (_, i) => t(`Cli${i}`, atraso, valor));
}

test('agrupa por nome com espacos e maiusculas diferentes', () => {
  const cs = agruparClientes(
    [t('Acme', 0, 100), t('  ACME ', 0, 50), t('acme', 0, 25), t('Beta', 0, 10)],
    HOJE,
  );
  assert.equal(cs.length, 2);
  const acme = cs.find((c) => c.nome === 'Acme');
  assert.equal(acme.contas.length, 3);
  assert.equal(acme.valorTotal, 175);
});

test('faixa pelo pior titulo, limites 0, 1, 30 e 31 dias', () => {
  assert.equal(faixaPorPiorAtraso(0), 'em-dia');
  assert.equal(faixaPorPiorAtraso(1), 'ate-30');
  assert.equal(faixaPorPiorAtraso(30), 'ate-30');
  assert.equal(faixaPorPiorAtraso(31), 'mais-30');
  assert.equal(cliente([t('A', 0, 1)], 'A').faixa, 'em-dia');
  assert.equal(cliente([t('A', 1, 1)], 'A').faixa, 'ate-30');
  assert.equal(cliente([t('A', 30, 1)], 'A').faixa, 'ate-30');
  assert.equal(cliente([t('A', 31, 1)], 'A').faixa, 'mais-30');
  assert.equal(cliente([t('A', 0, 1, { vencimento: iso(0) })], 'A').faixa, 'em-dia');
});

test('cliente cai na faixa do titulo mais atrasado', () => {
  const c = cliente([t('A', 5, 10), t('A', 40, 20), t('A', -3, 7)], 'A');
  assert.equal(c.faixa, 'mais-30');
  assert.equal(c.piorAtraso, 40);
  assert.equal(c.valorVencido, 30);
  assert.equal(c.valorEmAberto, 7);
});

test('cliente so com titulos pagos cai em Pagos, nao em Em dia', () => {
  const contas = [t('P', 90, 500, { status: 'pago' }), t('P', 10, 100, { status: 'pago' })];
  const c = cliente(contas, 'P');
  assert.equal(c.faixa, 'pagos');
  assert.equal(c.temAberto, false);
  const f = resumirFaixasCarteira(agruparClientes(contas, HOJE));
  assert.deepEqual(plain(f.pagos), { faixa: 'pagos', clientes: 1, valor: 600 });
  assert.equal(f['em-dia'].clientes, 0);
});

test('cliente com pago e em aberto em dia conta em Em dia', () => {
  const c = cliente([t('A', 0, 10, { status: 'pago' }), t('A', -4, 20)], 'A');
  assert.equal(c.faixa, 'em-dia');
});

test('particao em 4 grupos com soma igual ao total de clientes', () => {
  const contas = [
    t('A', -5, 100), t('A', -10, 50),
    t('B', 10, 200), t('B', -2, 30),
    t('C', 45, 300), t('C', 5, 100),
    t('D', 31, 1000, { status: 'pago' }),
    t('E', 0, 5, { status: 'pago' }),
  ];
  const f = plain(resumirFaixasCarteira(agruparClientes(contas, HOJE)));
  assert.deepEqual(f['em-dia'], { faixa: 'em-dia', clientes: 1, valor: 150 });
  assert.deepEqual(f['ate-30'], { faixa: 'ate-30', clientes: 1, valor: 200 });
  assert.deepEqual(f['mais-30'], { faixa: 'mais-30', clientes: 1, valor: 400 });
  assert.deepEqual(f.pagos, { faixa: 'pagos', clientes: 2, valor: 1005 });
  const soma = Object.values(f).reduce((s, x) => s + x.clientes, 0);
  assert.equal(soma, 5);
  assert.equal(soma, resumirCadastro(contas).clientes);
});

test('resumo do cabecalho: clientes unicos e soma de todos os titulos', () => {
  const r = resumirCadastro([t('A', 0, 100), t(' a', 0, 50, { status: 'pago' }), t('B', 0, 10)]);
  assert.deepEqual(plain(r), { clientes: 2, valorCadastrado: 160 });
});

test('concentracao some com menos de 10 clientes', () => {
  assert.equal(calcularConcentracao(agruparClientes(muitos(9), HOJE)), null);
  assert.notEqual(calcularConcentracao(agruparClientes(muitos(10), HOJE)), null);
});

test('concentracao usa valor em aberto, nao o cadastrado', () => {
  const contas = [
    ...muitos(9, 10),
    t('Grande', 0, 1000, { status: 'pago' }), // pago: nao entra
    t('Aberto', 0, 90),
  ];
  const c = plain(calcularConcentracao(agruparClientes(contas, HOJE)));
  assert.equal(c.totalAberto, 180);
  assert.equal(c.barras[0].nome, 'Aberto');
  assert.equal(c.barras[0].percentual, 50);
  assert.ok(!c.barras.some((b) => b.nome === 'Grande'));
  assert.equal(c.alerta, undefined);
});

test('concentracao: top 3, barras e outros', () => {
  const contas = [
    t('A', 0, 400), t('B', 0, 300), t('C', 0, 200), t('D', 0, 50),
    t('E', 0, 30), t('F', 0, 20), ...muitos(4, 0),
  ];
  const c = plain(calcularConcentracao(agruparClientes(contas, HOJE)));
  assert.equal(c.quantosNoTopo, 3);
  assert.equal(c.percentualTopo, 90);
  assert.deepEqual(c.barras.map((b) => [b.nome, b.percentual]), [
    ['A', 40], ['B', 30], ['C', 20], ['D', 5],
  ]);
  assert.deepEqual(c.outros, { clientes: 2, percentual: 5 });
});

test('concentracao: total em aberto zero nao divide por zero', () => {
  const contas = muitos(10, 10).map((c) => ({ ...c, status: 'pago' }));
  assert.equal(calcularConcentracao(agruparClientes(contas, HOJE)), null);
  assert.equal(calcularConcentracao(agruparClientes(muitos(10, 0), HOJE)), null);
  assert.equal(calcularConcentracao([]), null);
});

test('sem contato: espacos contam como vazio e so conta quem tem valor em aberto', () => {
  const contas = [
    t('A', 0, 100, { email: '   ', whatsapp: ' ' }),
    t('B', 0, 50, { email: null, whatsapp: null }),
    t('C', 0, 70, { email: '', whatsapp: '', status: 'pago' }), // sem aberto: fora
    t('D', 0, 10, { email: 'd@d.com' }),
  ];
  const s = plain(calcularSemContato(agruparClientes(contas, HOJE)));
  assert.deepEqual(s, { clientes: 2 });
});

test('sem contato: contato em so um dos titulos tira o cliente', () => {
  const contas = [
    t('Acme', 0, 100, { email: null, whatsapp: null }),
    t('acme ', 0, 100, { email: null, whatsapp: '11999990000' }),
    t('Beta', 0, 10, { email: null, whatsapp: null }),
  ];
  assert.deepEqual(plain(calcularSemContato(agruparClientes(contas, HOJE))), { clientes: 1 });
  assert.equal(calcularSemContato(agruparClientes([t('A', 0, 1)], HOJE)), null);
});

test('texto de status da tabela', () => {
  assert.equal(situacaoConta(t('A', 31, 1), HOJE).texto, 'Atrasado · 31 dias');
  assert.equal(situacaoConta(t('A', 1, 1), HOJE).texto, 'Atrasado · 1 dia');
  assert.equal(situacaoConta(t('A', 0, 1, { vencimento: iso(0) }), HOJE).texto, 'Em aberto');
  assert.equal(situacaoConta(t('A', -5, 1), HOJE).texto, 'Em aberto');
  assert.equal(situacaoConta(t('A', 90, 1, { status: 'pago' }), HOJE).texto, 'Pago');
  assert.equal(situacaoConta(t('A', 3, 1, { status: 'aberto' }), HOJE).tipo, 'atrasado');
});

test('tabela e cards usam a mesma regra de atraso', () => {
  const contas = [t('A', 5, 1, { status: 'aberto' }), t('B', -5, 1, { status: 'atrasado' })];
  // status do backend nao manda: a data decide
  assert.equal(situacaoConta(contas[0], HOJE).tipo, 'atrasado');
  assert.equal(situacaoConta(contas[1], HOJE).tipo, 'aberto');
  const f = resumirFaixasCarteira(agruparClientes(contas, HOJE));
  assert.equal(f['ate-30'].clientes, 1);
  assert.equal(f['em-dia'].clientes, 1);
});

test('filtros: card devolve titulos dos clientes do grupo; null devolve tudo', () => {
  const contas = [
    t('A', 5, 10), t('A', 40, 10), // mais-30 (2 titulos)
    t('B', 3, 10), // ate-30
    t('C', 0, 10, { status: 'pago' }), // pagos
    t('D', -2, 10), // em-dia
  ];
  const ids = (f) => filtrarContas(contas, HOJE, f).map((c) => c.cliente);
  assert.deepEqual(ids('mais-30'), ['A', 'A']);
  assert.deepEqual(ids('ate-30'), ['B']);
  assert.deepEqual(ids('pagos'), ['C']);
  assert.deepEqual(ids('em-dia'), ['D']);
  assert.equal(filtrarContas(contas, HOJE, null).length, 5);
});

test('filtro sem contato: so clientes com valor em aberto e sem contato em nenhum titulo', () => {
  const contas = [
    t('A', 0, 10, { email: null }),
    t('A', 0, 10, { email: null, status: 'pago' }),
    t('B', 0, 10, { email: null, status: 'pago' }), // pago: fora
    t('C', 0, 10, { email: null }),
    t('c', 0, 10, { email: 'c@c.com' }), // contato em um titulo: fora
  ];
  const r = filtrarContas(contas, HOJE, 'sem-contato');
  assert.deepEqual(r.map((c) => c.cliente), ['A', 'A']);
});

test('painel vazio com 0 contas e plural correto', () => {
  assert.equal(montarSaudeCarteira([], HOJE), null);
  assert.equal(pluralizarClientes(1), '1 cliente');
  assert.equal(pluralizarClientes(2), '2 clientes');
  assert.equal(pluralizarClientes(0), '0 clientes');
});
