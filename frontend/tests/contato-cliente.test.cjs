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
      throw new Error(`import inesperado: ${id}`);
    },
    RegExp,
  });
  return mod.exports;
}

const {
  normalizarWhatsapp,
  emailValido,
  validarContato,
  chaveCliente,
  titulosDoCliente,
  contatosDistintos,
  formularioInicial,
  MSG_SEM_CONTATO,
} = load('lib/contatoCliente.ts');

const plain = (x) => JSON.parse(JSON.stringify(x));

function conta(id, cliente, email = null, whatsapp = null) {
  return { id, cliente, email, whatsapp, valor: 10, vencimento: '2026-12-01', status: 'aberto' };
}

test('normalizarWhatsapp remove espaços, hífens e parênteses e mantém o +', () => {
  assert.equal(normalizarWhatsapp('+55 (11) 99999-8888'), '+5511999998888');
  assert.equal(normalizarWhatsapp('(11) 99999-8888'), '11999998888');
  assert.equal(normalizarWhatsapp('  11999998888 '), '11999998888');
});

test('normalizarWhatsapp respeita os limites de 10 e 15 dígitos', () => {
  assert.equal(normalizarWhatsapp('1'.repeat(9)), null);
  assert.equal(normalizarWhatsapp('1'.repeat(10)), '1'.repeat(10));
  assert.equal(normalizarWhatsapp('1'.repeat(15)), '1'.repeat(15));
  assert.equal(normalizarWhatsapp('1'.repeat(16)), null);
  assert.equal(normalizarWhatsapp('+' + '1'.repeat(15)), '+' + '1'.repeat(15));
  assert.equal(normalizarWhatsapp('+' + '1'.repeat(16)), null);
  assert.equal(normalizarWhatsapp('+' + '1'.repeat(9)), null);
});

test('normalizarWhatsapp recusa letras, + fora do início, ++ e vazio', () => {
  for (const ruim of ['abc', '11 9999a8888', '++5511999998888', '55+11999998888', '', '   ', null, undefined]) {
    assert.equal(normalizarWhatsapp(ruim), null, String(ruim));
  }
});

test('emailValido usa a regra do ClienteModal', () => {
  assert.equal(emailValido('a@b.com'), true);
  assert.equal(emailValido('  a@b.com '), true);
  for (const ruim of ['', 'a', 'a@b', 'a b@c.com', '@b.com', 'a@@b.com']) {
    assert.equal(emailValido(ruim), false, ruim);
  }
});

test('validarContato exige ao menos um contato', () => {
  const r = validarContato({ email: '  ', whatsapp: '' });
  assert.equal(r.valido, false);
  assert.equal(r.erros.geral, MSG_SEM_CONTATO);
  assert.equal(r.email, null);
  assert.equal(r.whatsapp, null);
});

test('validarContato envia só o campo preenchido e normalizado', () => {
  const soEmail = validarContato({ email: ' a@b.com ', whatsapp: '' });
  assert.deepEqual(plain(soEmail), { email: 'a@b.com', whatsapp: null, erros: {}, valido: true });

  const soZap = validarContato({ email: '', whatsapp: '(11) 99999-8888' });
  assert.deepEqual(plain(soZap), { email: null, whatsapp: '11999998888', erros: {}, valido: true });
});

test('validarContato aponta e-mail e WhatsApp inválidos por campo', () => {
  const r = validarContato({ email: 'ruim', whatsapp: '123' });
  assert.equal(r.valido, false);
  assert.ok(r.erros.email);
  assert.ok(r.erros.whatsapp);
  assert.equal(r.erros.geral, undefined);

  // um válido e outro inválido: não deixa passar
  assert.equal(validarContato({ email: 'a@b.com', whatsapp: '123' }).valido, false);
});

test('chaveCliente e titulosDoCliente casam como o backend (trim + caixa)', () => {
  assert.equal(chaveCliente('  ACME Ltda '), 'acme ltda');
  const contas = [
    conta(1, 'Acme Ltda'),
    conta(2, '  ACME LTDA '),
    conta(3, 'Acme'),
    conta(4, 'Acme Ltda S/A'),
  ];
  assert.deepEqual(titulosDoCliente(contas, 'acme ltda').map((c) => c.id), [1, 2]);
});

test('contatosDistintos: sem divergência quando há um valor só', () => {
  const r = contatosDistintos([
    conta(1, 'A', 'x@a.com', '11999998888'),
    conta(2, 'A', ' x@a.com ', null),
    conta(3, 'A', null, null),
  ]);
  assert.deepEqual(plain(r), { emails: ['x@a.com'], whatsapps: ['11999998888'], divergente: false });
});

test('contatosDistintos: detecta divergência de e-mail ou de WhatsApp', () => {
  const email = contatosDistintos([conta(1, 'A', 'x@a.com'), conta(2, 'A', 'y@a.com')]);
  assert.equal(email.divergente, true);
  assert.deepEqual(plain(email.emails), ['x@a.com', 'y@a.com']);

  const zap = contatosDistintos([
    conta(1, 'A', null, '11999998888'),
    conta(2, 'A', null, '11888887777'),
  ]);
  assert.equal(zap.divergente, true);
  assert.equal(zap.whatsapps.length, 2);
});

test('contatosDistintos ignora vazios e só espaços', () => {
  const r = contatosDistintos([conta(1, 'A', '  ', ''), conta(2, 'A', null, '   ')]);
  assert.deepEqual(plain(r), { emails: [], whatsapps: [], divergente: false });
});

test('formularioInicial usa o valor único e deixa vazio quando não há ou diverge', () => {
  assert.deepEqual(
    plain(formularioInicial([conta(1, 'A', 'x@a.com', null), conta(2, 'A', null, null)])),
    { email: 'x@a.com', whatsapp: '' },
  );
  assert.deepEqual(plain(formularioInicial([conta(1, 'A'), conta(2, 'A')])), { email: '', whatsapp: '' });
  assert.deepEqual(
    plain(formularioInicial([conta(1, 'A', 'x@a.com'), conta(2, 'A', 'y@a.com')])),
    { email: '', whatsapp: '' },
  );
});
