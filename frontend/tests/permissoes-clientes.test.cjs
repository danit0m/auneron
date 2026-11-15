const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/auth/permissions.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const mod = { exports: {} };
vm.runInNewContext(compiled, { exports: mod.exports, require: () => ({}) });
const { getPermissionsForRole, podeCadastrarCliente } = mod.exports;

const comoPapel = (role) => (perm) => getPermissionsForRole(role).includes(perm);

test('botao Novo cliente: viewer nao ve', () => {
  assert.equal(podeCadastrarCliente(comoPapel('viewer')), false);
});

test('botao Novo cliente: analista, gerente e acima veem', () => {
  for (const role of ['analyst', 'manager', 'executive', 'administrator', 'developer']) {
    assert.equal(podeCadastrarCliente(comoPapel(role)), true, role);
  }
});
