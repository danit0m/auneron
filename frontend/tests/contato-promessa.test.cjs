const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(rel, requireFn = () => ({})) {
  const source = fs.readFileSync(path.join(__dirname, '../src', rel), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const mod = { exports: {} };
  vm.runInNewContext(compiled, {
    exports: mod.exports,
    require: requireFn,
    Date,
    Number,
    String,
    RegExp,
  });
  return mod.exports;
}

const lib = load('lib/contatoPromessa.ts');
const permissoes = load('auth/permissions.ts');
const cockpit = load('lib/cockpit.ts');
const recebiveis = load('lib/recebiveis.ts', (id) => {
  if (id === './cockpit') return cockpit;
  throw new Error(`import inesperado: ${id}`);
});

const plain = (x) => JSON.parse(JSON.stringify(x));
const comoPapel = (role) => (perm) => permissoes.getPermissionsForRole(role).includes(perm);

// ------------------------------------------------------------ permissao

test('registrar contato: so quem pode pedir pagamento (gerente e acima)', () => {
  for (const role of ['manager', 'executive', 'administrator', 'developer']) {
    assert.equal(permissoes.podeRegistrarContato(comoPapel(role)), true, role);
  }
  for (const role of ['viewer', 'analyst']) {
    assert.equal(permissoes.podeRegistrarContato(comoPapel(role)), false, role);
  }
});

// -------------------------------------------------------------- exibicao

test('ultimo contato: data em dd/mm/aaaa ou traco', () => {
  assert.equal(lib.descreverUltimoContato(null), '—');
  assert.equal(lib.descreverUltimoContato(undefined), '—');
  assert.equal(lib.descreverUltimoContato('lixo'), '—');
  // meio-dia local: evita virar o dia por fuso
  const local = new Date(2026, 10, 5, 12, 0, 0).toISOString();
  assert.equal(lib.descreverUltimoContato(local), '05/11/2026');
});

test('proxima promessa: data valida ou traco', () => {
  assert.equal(lib.descreverProximaPromessa({ promessa_vigente: '2026-11-20' }), '20/11/2026');
  assert.equal(lib.descreverProximaPromessa({ promessa_vigente: null }), '—');
  assert.equal(lib.descreverProximaPromessa({}), '—');
  assert.equal(lib.descreverProximaPromessa({ promessa_vigente: '2026-02-30' }), '—');
});

test('selo promessa vencida segue a API e some quando pago', () => {
  assert.equal(lib.temPromessaVencida({ promessa_vencida: true, status: 'atrasado' }), true);
  assert.equal(lib.temPromessaVencida({ promessa_vencida: false, status: 'atrasado' }), false);
  assert.equal(lib.temPromessaVencida({ status: 'atrasado' }), false);
  assert.equal(lib.temPromessaVencida({ promessa_vencida: true, status: 'pago' }), false);
});

test('tipos do modal: remarcar/encerrar so com promessa vigente', () => {
  assert.deepEqual(plain(lib.tiposDisponiveis({ promessa_vigente: null })), ['contato', 'promessa']);
  assert.deepEqual(plain(lib.tiposDisponiveis({})), ['contato', 'promessa']);
  assert.deepEqual(plain(lib.tiposDisponiveis({ promessa_vigente: '2026-11-20' })), [
    'contato',
    'remarcacao',
    'encerramento',
  ]);
});

// ---------------------------------------------------------- corpo do POST

test('contato: sem data; nota em branco some; nota e aparada', () => {
  assert.deepEqual(plain(lib.montarEventoContato({ tipo: 'contato', data: '2026-12-01', nota: '   ' })), {
    valido: true,
    corpo: { event_type: 'contato' },
  });
  assert.deepEqual(plain(lib.montarEventoContato({ tipo: 'encerramento', data: '', nota: ' ligou ' })), {
    valido: true,
    corpo: { event_type: 'encerramento', note: 'ligou' },
  });
});

test('promessa e remarcacao exigem data valida', () => {
  for (const tipo of ['promessa', 'remarcacao']) {
    assert.equal(lib.montarEventoContato({ tipo, data: '', nota: '' }).valido, false);
    assert.equal(lib.montarEventoContato({ tipo, data: '2026-02-30', nota: '' }).valido, false);
    assert.deepEqual(plain(lib.montarEventoContato({ tipo, data: '2026-12-01', nota: '' })), {
      valido: true,
      corpo: { event_type: tipo, promised_date: '2026-12-01' },
    });
  }
});

test('nota maior que 280 caracteres e recusada', () => {
  assert.equal(lib.montarEventoContato({ tipo: 'contato', data: '', nota: 'x'.repeat(280) }).valido, true);
  assert.equal(lib.montarEventoContato({ tipo: 'contato', data: '', nota: 'x'.repeat(281) }).valido, false);
});

// ----------------------------------------------------------- filtro da fila

test('aba Promessa vencida: so contas com o indicador da API, ordenadas', () => {
  const hoje = new Date(2026, 10, 12, 12, 0);
  const base = { email: null, whatsapp: null, valor: 100, status: 'atrasado' };
  const a = { ...base, id: 1, cliente: 'A', vencimento: '2026-11-02', promessa_vencida: true };
  const b = { ...base, id: 2, cliente: 'B', vencimento: '2026-10-01', promessa_vencida: true };
  const c = { ...base, id: 3, cliente: 'C', vencimento: '2026-10-01', promessa_vencida: false };
  const d = { ...base, id: 4, cliente: 'D', vencimento: '2026-12-30', status: 'aberto', promessa_vencida: true };
  const itens = recebiveis.classificarContas([a, b, c, d], hoje);

  assert.deepEqual(plain(recebiveis.filtrarFila(itens, 'promessa-vencida').map((i) => i.conta.id)), [2, 1, 4]);
  assert.equal(recebiveis.contarAbas(itens)['promessa-vencida'], 3);
  // as abas existentes nao mudam de criterio
  assert.deepEqual(plain(recebiveis.filtrarFila(itens, 'vencidas').map((i) => i.conta.id)), [2, 3, 1]);
});
