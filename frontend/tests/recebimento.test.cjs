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

const pagamento = load('lib/pagamento.ts', () => ({}));
const lib = load('lib/recebimento.ts', (id) => {
  if (id === './pagamento') return pagamento;
  throw new Error(`import inesperado: ${id}`);
});
const {
  JANELA_RECEBIDO_DIAS,
  recebidoNoPeriodo,
  atrasoMedioPagamento,
  pagosSemData,
  descreverAtrasoMedio,
  descreverPagosEmDia,
  descreverTitulosPagos,
  descreverSemData,
} = lib;

const HOJE = new Date(2026, 10, 12, 23, 59); // 12/11/2026, fim do dia

let seq = 0;
function conta(extra) {
  seq += 1;
  return {
    id: seq, cliente: 'C', email: null, whatsapp: null, valor: 100,
    vencimento: '2026-11-01', status: 'pago', paid_on: '2026-11-01', ...extra,
  };
}

test('janela e de 30 dias', () => {
  assert.equal(JANELA_RECEBIDO_DIAS, 30);
});

test('janela: bordas inclusivas (hoje e hoje-30) e exclusivas (hoje-31, futuro)', () => {
  const contas = [
    conta({ paid_on: '2026-11-12', valor: 10 }), // hoje
    conta({ paid_on: '2026-10-13', valor: 20 }), // hoje-30
    conta({ paid_on: '2026-10-12', valor: 40 }), // hoje-31, fora
    conta({ paid_on: '2026-11-13', valor: 80 }), // futuro, fora
  ];
  const r = recebidoNoPeriodo(contas, HOJE);
  assert.deepEqual({ ...r }, { valor: 30, quantidade: 2, dias: 30 });
});

test('janela atravessa virada de ano sem deslocar por fuso', () => {
  const hoje = new Date(2027, 0, 5, 0, 5);
  const r = recebidoNoPeriodo(
    [conta({ paid_on: '2026-12-06' }), conta({ paid_on: '2026-12-05' })],
    hoje,
  );
  assert.equal(r.quantidade, 1);
});

test('recebido: sem pagos com data devolve null; datados fora da janela dao zero real', () => {
  assert.equal(recebidoNoPeriodo([], HOJE), null);
  assert.equal(recebidoNoPeriodo([conta({ paid_on: null })], HOJE), null);
  const r = recebidoNoPeriodo([conta({ paid_on: '2026-01-01' })], HOJE);
  assert.equal(r.valor, 0);
  assert.equal(r.quantidade, 0);
});

test('titulo aberto/atrasado com paid_on espurio e ignorado', () => {
  const contas = [
    conta({ status: 'aberto', paid_on: '2026-11-10', valor: 999 }),
    conta({ status: 'atrasado', paid_on: '2026-11-10', valor: 999 }),
  ];
  assert.equal(recebidoNoPeriodo(contas, HOJE), null);
  assert.equal(atrasoMedioPagamento(contas), null);
  assert.equal(pagosSemData(contas), null);
});

test('atraso medio com positivos, negativos e zero', () => {
  const contas = [
    conta({ vencimento: '2026-11-01', paid_on: '2026-11-07' }), // +6
    conta({ vencimento: '2026-11-10', paid_on: '2026-11-08' }), // -2
    conta({ vencimento: '2026-11-05', paid_on: '2026-11-05' }), // 0
  ];
  const r = atrasoMedioPagamento(contas);
  assert.equal(r.mediaExata, 4 / 3);
  assert.equal(r.mediaDias, 1);
  assert.equal(r.emDia, 2);
  assert.equal(r.comAtraso, 1);
  assert.equal(r.total, 3);
});

test('atraso medio: arredondamento, media negativa e sem -0', () => {
  const meio = atrasoMedioPagamento([
    conta({ vencimento: '2026-11-01', paid_on: '2026-11-03' }),
    conta({ vencimento: '2026-11-01', paid_on: '2026-11-04' }),
  ]);
  assert.equal(meio.mediaDias, 3); // 2,5 -> 3
  const neg = atrasoMedioPagamento([
    conta({ vencimento: '2026-11-05', paid_on: '2026-11-03' }),
    conta({ vencimento: '2026-11-05', paid_on: '2026-11-02' }),
  ]);
  assert.equal(neg.mediaDias, -3); // -2,5 -> -3
  const quaseZero = atrasoMedioPagamento([
    conta({ vencimento: '2026-11-05', paid_on: '2026-11-04' }),
    conta({ vencimento: '2026-11-05', paid_on: '2026-11-05' }),
    conta({ vencimento: '2026-11-05', paid_on: '2026-11-05' }),
  ]);
  assert.equal(Object.is(quaseZero.mediaDias, 0), true);
});

test('atraso medio cruza virada de mes/ano e aceita vencimento com horario', () => {
  const r = atrasoMedioPagamento([
    conta({ vencimento: '2026-12-30', paid_on: '2027-01-02' }), // +3
    conta({ vencimento: '2026-10-01T00:00:00', paid_on: '2026-11-01' }), // +31
  ]);
  assert.equal(r.mediaExata, 17);
});

test('pagos sem data ficam fora das medias e sao contados a parte', () => {
  const contas = [
    conta({ vencimento: '2026-11-01', paid_on: '2026-11-03', valor: 50 }),
    conta({ paid_on: null, valor: 70 }),
    conta({ paid_on: undefined, valor: 30 }),
    conta({ paid_on: 'lixo', valor: 5 }),
  ];
  assert.deepEqual({ ...pagosSemData(contas) }, { quantidade: 3, valor: 105 });
  assert.equal(atrasoMedioPagamento(contas).total, 1);
  assert.equal(recebidoNoPeriodo(contas, HOJE).valor, 50);
  assert.equal(pagosSemData([conta({})]), null);
});

test('lista vazia ou sem datas devolve null', () => {
  assert.equal(atrasoMedioPagamento([]), null);
  assert.equal(atrasoMedioPagamento([conta({ paid_on: null })]), null);
  assert.equal(pagosSemData([]), null);
});

test('textos de singular/plural', () => {
  assert.equal(descreverAtrasoMedio(4), '4 dias depois do vencimento');
  assert.equal(descreverAtrasoMedio(1), '1 dia depois do vencimento');
  assert.equal(descreverAtrasoMedio(-1), '1 dia antes do vencimento');
  assert.equal(descreverAtrasoMedio(-2), '2 dias antes do vencimento');
  assert.equal(descreverAtrasoMedio(0), 'no vencimento');
  assert.equal(descreverPagosEmDia(3, 5), '3 de 5');
  assert.equal(descreverTitulosPagos(1), '1 título');
  assert.equal(descreverTitulosPagos(2), '2 títulos');
  assert.equal(
    descreverSemData({ quantidade: 1, valor: 1 }, 'R$ 1,00'),
    '1 título pago sem data registrada (R$ 1,00) fica fora das médias.',
  );
  assert.equal(
    descreverSemData({ quantidade: 2, valor: 1 }, 'R$ 1,00'),
    '2 títulos pagos sem data registrada (R$ 1,00) ficam fora das médias.',
  );
});
