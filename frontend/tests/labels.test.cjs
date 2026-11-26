const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/design/labels.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { humanizarNome, rotuloDecisao, rotuloEvento, rotuloAgente, rotuloSinal } = context.exports;

test('decisoes conhecidas em qualquer grafia', () => {
  assert.equal(rotuloDecisao('CriticalOverdueDecision'), 'Análise de inadimplência crítica');
  assert.equal(rotuloDecisao('Criticaloverduedecision'), 'Análise de inadimplência crítica');
  assert.equal(rotuloDecisao('ALTO_VALOR_EM_ATRASO'), 'Cliente estratégico em atraso');
});

test('fallback separa palavras e nunca cola', () => {
  assert.equal(rotuloDecisao('SomeNewDecision'), 'Some new decision');
  assert.equal(humanizarNome('receivable_lifecycle_changed'), 'Receivable lifecycle changed');
  assert.equal(humanizarNome(''), '');
});

test('eventos, agentes e sinais', () => {
  assert.equal(rotuloEvento('receivable_lifecycle_changed'), 'Mudança no ciclo de vida da conta a receber');
  assert.equal(rotuloAgente('ReceivablesMonitorAgent'), 'Monitor de contas a receber');
  assert.equal(rotuloAgente('CollectionsHelperAgent'), 'Collections helper');
  assert.equal(rotuloSinal('dias_atraso'), 'Dias em atraso');
});
