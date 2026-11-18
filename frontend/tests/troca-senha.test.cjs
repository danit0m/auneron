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
  vm.runInNewContext(compiled, { exports: mod.exports, require: () => ({}), Boolean, String });
  return mod.exports;
}

const lib = load('lib/trocaSenha.ts');

test('precisaTrocarSenha so e true com must_change_password === true', () => {
  assert.equal(lib.precisaTrocarSenha({ must_change_password: true }), true);
  assert.equal(lib.precisaTrocarSenha({ must_change_password: false }), false);
  assert.equal(lib.precisaTrocarSenha({}), false);
  assert.equal(lib.precisaTrocarSenha(null), false);
  assert.equal(lib.precisaTrocarSenha(undefined), false);
});

test('erro 403 password_change_required e reconhecido; outros nao', () => {
  const ok = { detail: { code: 'password_change_required', message: 'x' } };
  assert.equal(lib.ehErroTrocaObrigatoria(403, ok), true);
  assert.equal(lib.ehErroTrocaObrigatoria(401, ok), false);
  assert.equal(lib.ehErroTrocaObrigatoria(403, { detail: 'Seu perfil nao possui permissao' }), false);
  assert.equal(lib.ehErroTrocaObrigatoria(403, { detail: { code: 'outro' } }), false);
  assert.equal(lib.ehErroTrocaObrigatoria(403, null), false);
  assert.equal(lib.ehErroTrocaObrigatoria(undefined, ok), false);
});

test('redirecionamento: troca pendente leva a tela de troca de qualquer rota', () => {
  const u = { must_change_password: true };
  assert.equal(lib.destinoDeRedirecionamento(true, u, '/'), '/trocar-senha');
  assert.equal(lib.destinoDeRedirecionamento(true, u, '/approvals'), '/trocar-senha');
  assert.equal(lib.destinoDeRedirecionamento(true, u, '/trocar-senha'), null);
});

test('redirecionamento: sem pendencia ou sem sessao nao redireciona', () => {
  assert.equal(lib.destinoDeRedirecionamento(true, { must_change_password: false }, '/'), null);
  assert.equal(lib.destinoDeRedirecionamento(true, {}, '/clientes'), null);
  assert.equal(lib.destinoDeRedirecionamento(false, { must_change_password: true }, '/'), null);
  assert.equal(lib.destinoDeRedirecionamento(false, null, '/'), null);
});

test('destino apos a troca volta ao app e ignora origens perigosas', () => {
  assert.equal(lib.destinoAposTroca('/clientes?x=1'), '/clientes?x=1');
  assert.equal(lib.destinoAposTroca(undefined), '/');
  assert.equal(lib.destinoAposTroca('/trocar-senha'), '/');
  assert.equal(lib.destinoAposTroca('/login'), '/');
  assert.equal(lib.destinoAposTroca('//evil.example'), '/');
  assert.equal(lib.destinoAposTroca('https://evil.example'), '/');
});

test('validacao do formulario de troca de senha', () => {
  const nova = 'Nova-Senha-Auneron-456!';
  assert.equal(lib.validarTrocaDeSenha('Temp-123', nova, nova), null);
  assert.equal(lib.validarTrocaDeSenha('', nova, nova), lib.MSG_SENHA_ATUAL_OBRIGATORIA);
  assert.equal(lib.validarTrocaDeSenha('Temp-123', 'curta', 'curta'), lib.MSG_SENHA_CURTA);
  assert.equal(lib.validarTrocaDeSenha('Temp-123', 'a'.repeat(129), 'a'.repeat(129)), lib.MSG_SENHA_LONGA);
  assert.equal(lib.validarTrocaDeSenha(nova, nova, nova), lib.MSG_SENHA_IGUAL);
  assert.equal(lib.validarTrocaDeSenha('Temp-123', nova, nova + 'x'), lib.MSG_CONFIRMACAO_DIFERENTE);
  assert.equal(lib.validarTrocaDeSenha('Temp-123', 'a'.repeat(11), 'a'.repeat(11)), lib.MSG_SENHA_CURTA);
  assert.equal(lib.validarTrocaDeSenha('Temp-123', 'a'.repeat(12), 'a'.repeat(12)), null);
});
