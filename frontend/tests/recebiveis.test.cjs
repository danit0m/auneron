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
const lib = load('lib/recebiveis.ts', (id) => {
  if (id === './cockpit') return cockpit;
  throw new Error(`import inesperado: ${id}`);
});
const {
  classificarContas,
  resumirFaixas,
  ordenarFila,
  filtrarFila,
  contarAbas,
  faixaPorAtraso,
  humorPorSituacao,
} = lib;

const HOJE = new Date(2026, 10, 12, 15, 30); // 12/11/2026, meio da tarde

function iso(deslocamento) {
  const d = new Date(2026, 10, 12 + deslocamento);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

let seq = 0;
// atraso > 0 => vencida ha N dias; atraso <= 0 => vence em -atraso dias.
function conta(atraso, over = {}) {
  seq += 1;
  return {
    id: seq,
    cliente: `Cliente ${seq}`,
    email: null,
    whatsapp: null,
    valor: 100,
    vencimento: iso(-atraso),
    status: 'aberto',
    ...over,
  };
}

// Resultados do vm sao de outro realm: normaliza antes de deepEqual.
const plain = (x) => JSON.parse(JSON.stringify(x));

const um = (atraso, over) => classificarContas([conta(atraso, over)], HOJE)[0];

test('limites das faixas: 0, 1, 30, 31, 60, 61, 90, 91 dias', () => {
  const casos = [
    [-5, 'a-vencer'],
    [0, 'a-vencer'],
    [1, '1-30'],
    [30, '1-30'],
    [31, '31-60'],
    [60, '31-60'],
    [61, '61-90'],
    [90, '61-90'],
    [91, 'mais-90'],
  ];
  for (const [atraso, faixa] of casos) {
    assert.equal(um(atraso).faixa, faixa, `atraso ${atraso}`);
    assert.equal(faixaPorAtraso(atraso), faixa, `faixaPorAtraso ${atraso}`);
  }
});

test('conta paga fica fora de tudo', () => {
  const contas = [conta(10, { status: 'pago' }), conta(0, { status: 'pago' }), conta(40)];
  const itens = classificarContas(contas, HOJE);
  assert.equal(itens.length, 1);
  const resumo = resumirFaixas(itens);
  assert.equal(resumo.faturas, 1);
  assert.equal(contarAbas(itens).todas, 1);
  assert.equal(filtrarFila(itens, 'vencidas').length, 1);
});

test('data invalida fica de fora', () => {
  assert.equal(classificarContas([conta(1, { vencimento: 'lixo' })], HOJE).length, 0);
});

test('status "atrasado" tambem conta como em aberto', () => {
  assert.equal(um(5, { status: 'atrasado' }).vencida, true);
});

test('humor em cada limite', () => {
  const casos = [
    [91, 'critico'],
    [31, 'critico'],
    [30, 'preocupado'],
    [1, 'preocupado'],
    [0, 'atento'], // vence hoje
    [-1, 'atento'],
    [-3, 'atento'],
    [-4, 'feliz'],
    [-30, 'feliz'],
  ];
  for (const [atraso, mood] of casos) {
    assert.equal(um(atraso).mood, mood, `atraso ${atraso}`);
  }
  assert.equal(humorPorSituacao(0, 3), 'atento');
  assert.equal(humorPorSituacao(0, 4), 'feliz');
});

test('ordenacao: vencidas por atraso desc, depois a vencer por prazo asc', () => {
  const a = conta(5);
  const b = conta(45);
  const c = conta(-2);
  const d = conta(-1);
  const e = conta(0);
  const f = conta(120);
  const g = conta(-10);
  const itens = classificarContas([a, b, c, d, e, f, g], HOJE);
  const ids = ordenarFila(itens).map((i) => i.conta.id);
  assert.deepEqual(plain(ids), [f.id, b.id, a.id, e.id, d.id, c.id, g.id]);
});

test('ordenacao: empate desempata por valor desc e depois por id', () => {
  const a = conta(10, { valor: 50 });
  const b = conta(10, { valor: 200 });
  const c = conta(10, { valor: 50 });
  const ids = ordenarFila(classificarContas([a, b, c], HOJE)).map((i) => i.conta.id);
  assert.deepEqual(plain(ids), [b.id, a.id, c.id]);
});

test('ordenacao nao altera a lista original', () => {
  const itens = classificarContas([conta(1), conta(50)], HOJE);
  const copia = itens.map((i) => i.conta.id);
  ordenarFila(itens);
  assert.deepEqual(
    itens.map((i) => i.conta.id),
    copia,
  );
});

test('contadores e filtros das abas', () => {
  const venc1 = conta(1);
  const venc2 = conta(95);
  const hoje = conta(0);
  const em3 = conta(-3);
  const em4 = conta(-4); // fora da janela
  const longe = conta(-40); // fora da janela
  const paga = conta(10, { status: 'pago' });
  const itens = classificarContas([venc1, venc2, hoje, em3, em4, longe, paga], HOJE);

  assert.deepEqual(plain(contarAbas(itens)), { todas: 4, vencidas: 2, 'ate-3-dias': 2 });

  const idsDe = (aba) => filtrarFila(itens, aba).map((i) => i.conta.id);
  assert.deepEqual(plain(idsDe('vencidas')), [venc2.id, venc1.id]);
  assert.deepEqual(plain(idsDe('ate-3-dias')), [hoje.id, em3.id]);
  assert.deepEqual(plain(idsDe('todas')), [venc2.id, venc1.id, hoje.id, em3.id]);
});

test('abas vazias quando nao ha contas elegiveis', () => {
  const itens = classificarContas([conta(-20)], HOJE);
  assert.deepEqual(plain(contarAbas(itens)), { todas: 0, vencidas: 0, 'ate-3-dias': 0 });
  assert.deepEqual(plain(filtrarFila(itens, 'todas')), []);
});

test('soma de valores e numero de faturas por faixa', () => {
  const contas = [
    conta(-10, { valor: 1000.5 }),
    conta(0, { valor: 200 }),
    conta(1, { valor: 10 }),
    conta(30, { valor: 20 }),
    conta(31, { valor: 300 }),
    conta(60, { valor: 400 }),
    conta(61, { valor: 5 }),
    conta(90, { valor: 6 }),
    conta(91, { valor: 7 }),
    conta(500, { valor: 8 }),
    conta(20, { valor: 9999, status: 'pago' }),
  ];
  const resumo = resumirFaixas(classificarContas(contas, HOJE));
  const por = Object.fromEntries(resumo.faixas.map((f) => [f.chave, f]));

  assert.deepEqual(
    plain(resumo.faixas.map((f) => f.chave)),
    ['a-vencer', '1-30', '31-60', '61-90', 'mais-90'],
  );
  assert.equal(por['a-vencer'].valor, 1200.5);
  assert.equal(por['a-vencer'].faturas, 2);
  assert.equal(por['1-30'].valor, 30);
  assert.equal(por['1-30'].faturas, 2);
  assert.equal(por['31-60'].valor, 700);
  assert.equal(por['31-60'].faturas, 2);
  assert.equal(por['61-90'].valor, 11);
  assert.equal(por['61-90'].faturas, 2);
  assert.equal(por['mais-90'].valor, 15);
  assert.equal(por['mais-90'].faturas, 2);
  assert.equal(resumo.total, 1200.5 + 30 + 700 + 11 + 15);
  assert.equal(resumo.faturas, 10);
});

test('sem contas: faixas zeradas', () => {
  const resumo = resumirFaixas([]);
  assert.equal(resumo.total, 0);
  assert.equal(resumo.faturas, 0);
  assert.equal(resumo.faixas.length, 5);
});

test('dias de atraso ignoram a hora do dia', () => {
  const tarde = new Date(2026, 10, 12, 23, 59);
  const cedo = new Date(2026, 10, 12, 0, 1);
  const c = conta(5);
  assert.equal(classificarContas([c], tarde)[0].diasAtraso, 5);
  assert.equal(classificarContas([c], cedo)[0].diasAtraso, 5);
});
