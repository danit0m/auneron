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
  vm.runInNewContext(compiled, { exports: mod.exports, require: () => ({}) });
  return mod.exports;
}

const { podeDecidirPedido, usuarioEhSolicitante } = load('lib/aprovacoes.ts');

const pedido = (extra = {}) => ({
  status: 'pending',
  requester_actor_type: 'user',
  requester_user_id: 7,
  ...extra,
});

test('item 2: solicitante nao ve Aprovar/Rejeitar no proprio pedido', () => {
  assert.equal(podeDecidirPedido(pedido(), 7, true), false);
  assert.equal(usuarioEhSolicitante(pedido(), 7), true);
});

test('item 3: outro usuario com permissao decide pedido pendente', () => {
  assert.equal(podeDecidirPedido(pedido(), 8, true), true);
  assert.equal(usuarioEhSolicitante(pedido(), 8), false);
});

test('sem permissao nao decide', () => {
  assert.equal(podeDecidirPedido(pedido(), 8, false), false);
});

test('pedido nao pendente nao decide', () => {
  for (const status of ['approved', 'rejected', 'expired', 'cancelled']) {
    assert.equal(podeDecidirPedido(pedido({ status }), 8, true), false);
  }
});

test('usuario desconhecido nao decide', () => {
  assert.equal(podeDecidirPedido(pedido(), null, true), false);
  assert.equal(podeDecidirPedido(pedido(), undefined, true), false);
});

test('solicitante que nao e usuario (agente) nao conta como o usuario logado', () => {
  const p = pedido({ requester_actor_type: 'agent', requester_user_id: null });
  assert.equal(usuarioEhSolicitante(p, 7), false);
  assert.equal(podeDecidirPedido(p, 7, true), true);
});
