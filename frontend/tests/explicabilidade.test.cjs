const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/components/executive/explicabilidade.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = { exports: {}, Intl };
vm.runInNewContext(compiled, context);
const { montarExplicabilidade } = context.exports;
const rotulo = (n) => `R(${n})`;
const tudo = (x) => JSON.stringify(x);

test('nunca presume cliente estrategico, mesmo com valor alto', () => {
  const x = montarExplicabilidade({ decision_name: 'X', context: { valor: 500000, status: 'atrasado', dias_atraso: 40 }, signals: [] }, rotulo);
  assert.doesNotMatch(tudo(x), /estrat[eé]gic/i);
});

test('cliente em dia nao recebe "Pagamento atrasado" nem dias em atraso', () => {
  const x = montarExplicabilidade({ decision_name: 'X', context: { valor: 1000, status: 'aberto', dias_atraso: 0 }, signals: [] }, rotulo);
  assert.deepEqual([...x.marcadores], []);
  assert.doesNotMatch(tudo(x), /atraso|atrasado/i);
  assert.equal(x.fatores.find((f) => f.chave === 'contexto:status').valor, 'Em aberto');
});

test('atraso real gera os marcadores com os numeros da analise', () => {
  const x = montarExplicabilidade({ decision_name: 'X', context: { valor: 11100, status: 'atrasado', dias_atraso: 11 }, signals: [] }, rotulo);
  assert.deepEqual([...x.marcadores], ['Pagamento atrasado', '11 dias em atraso']);
});

test('sinais da analise aparecem com o rotulo, valor e descricao registrados', () => {
  const x = montarExplicabilidade({
    decision_name: 'X',
    signals: [{ name: 'dias_atraso', value: 11, description: 'Dias em atraso' }, { name: 'valor_alto', value: true, description: '' }],
    context: { valor: 11100, status: 'atrasado', dias_atraso: 11 },
  }, rotulo);
  const sinal = x.fatores.find((f) => f.chave === 'sinal:dias_atraso');
  assert.equal(sinal.titulo, 'R(dias_atraso)');
  assert.equal(sinal.valor, '11 dias');
  assert.equal(sinal.descricao, 'Dias em atraso');
  assert.equal(x.fatores.find((f) => f.chave === 'sinal:valor_alto').valor, 'Sim');
  assert.equal(x.fatores.find((f) => f.chave === 'sinal:valor_alto').descricao, null);
  // o dia em atraso nao e repetido a partir do contexto; o valor em aberto continua aparecendo
  assert.equal(x.fatores.filter((f) => /dias_atraso/.test(f.chave)).length, 1);
  assert.ok(x.fatores.some((f) => f.chave === 'contexto:valor'));
});

test('nada de pesos ou percentuais inventados', () => {
  const x = montarExplicabilidade({ decision_name: 'X', context: { valor: 40000, status: 'atrasado', dias_atraso: 5 }, signals: [] }, rotulo);
  for (const f of x.fatores) assert.deepEqual(Object.keys(f).sort(), ['chave', 'descricao', 'titulo', 'valor']);
  assert.equal(x.confianca, null);
});

test('dados ausentes sao omitidos e sinalizados', () => {
  const x = montarExplicabilidade({ decision_name: 'X', context: null, signals: null, reason: '', confidence_percentage: null }, rotulo);
  assert.equal(x.semDados, true);
  assert.deepEqual([...x.fatores], []);
  assert.deepEqual([...x.marcadores], []);
  assert.equal(x.justificativa, null);
  const y = montarExplicabilidade({ decision_name: 'X', context: { valor: NaN, status: '  ', dias_atraso: null }, signals: [{ name: 'a', value: null, description: 'x' }] }, rotulo);
  assert.equal(y.semDados, true);
});

test('sinal de valor sai em reais e o contexto nao repete o valor', () => {
  const x = montarExplicabilidade({ decision_name: 'X', signals: [{ name: 'valor', value: 11100, description: 'Valor em aberto' }], context: { valor: 11100 } }, rotulo);
  assert.match(x.fatores[0].valor, /R\$\s?11\.100,00/);
  assert.equal(x.fatores.length, 1);
});

test('justificativa e confianca vem da analise', () => {
  const x = montarExplicabilidade({ decision_name: 'X', reason: ' Conta com 11 dias de atraso. ', confidence_percentage: 92, context: {}, signals: [] }, rotulo);
  assert.equal(x.justificativa, 'Conta com 11 dias de atraso.');
  assert.equal(x.confianca, '92%');
});
