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
    Date,
    Number,
    String,
  });
  return mod.exports;
}

const {
  SKILL_MARK_PAID,
  MSG_DATA_OBRIGATORIA,
  MSG_DATA_INVALIDA,
  MSG_DATA_FUTURA,
  dataIsoLocal,
  dataIsoValida,
  formatarDataBr,
  validarDataPagamento,
  avisoDataAnteriorAoCadastro,
  estadoPagamento,
  rotuloEstadoPagamento,
  podeConcluirPagamento,
  descreverPagamento,
} = load('lib/pagamento.ts');

const plain = (x) => JSON.parse(JSON.stringify(x));

// 12/11/2026, meio da tarde (hora local).
const HOJE = new Date(2026, 10, 12, 15, 30);
const AGORA = HOJE;

function pedido(over = {}) {
  return {
    request_id: 1,
    action_type: 'skill_execution',
    skill_version_id: 1,
    skill_key: 'account.mark_paid',
    requester_actor_type: 'user',
    requester_user_id: 10,
    risk_level: 'high',
    status: 'pending',
    target_account_id: 7,
    target_user_id: null,
    expires_at: new Date(2026, 10, 12, 18, 0).toISOString(),
    resolved_at: null,
    created_at: new Date(2026, 10, 12, 9, 0).toISOString(),
    effective_date: '2026-11-10',
    ...over,
  };
}

const CONTA = { id: 7, status: 'aberto' };

test('dataIsoLocal usa o dia local com zeros a esquerda', () => {
  assert.equal(dataIsoLocal(new Date(2026, 0, 5, 23, 59)), '2026-01-05');
  assert.equal(dataIsoLocal(HOJE), '2026-11-12');
});

test('dataIsoValida aceita so datas reais de calendario', () => {
  assert.equal(dataIsoValida('2026-11-12'), true);
  assert.equal(dataIsoValida('2024-02-29'), true);
  assert.equal(dataIsoValida('2026-02-29'), false);
  assert.equal(dataIsoValida('2026-13-01'), false);
  assert.equal(dataIsoValida('2026-11-31'), false);
  assert.equal(dataIsoValida('12/11/2026'), false);
  assert.equal(dataIsoValida(''), false);
});

test('formatarDataBr converte AAAA-MM-DD em dd/mm/aaaa', () => {
  assert.equal(formatarDataBr('2026-11-05'), '05/11/2026');
  assert.equal(formatarDataBr(null), '—');
  assert.equal(formatarDataBr(undefined), '—');
  assert.equal(formatarDataBr('lixo'), '—');
  assert.equal(formatarDataBr('2026-02-30'), '—');
});

test('validarDataPagamento: obrigatoria, valida e nao futura', () => {
  assert.deepEqual(plain(validarDataPagamento('', HOJE)), { valida: false, erro: MSG_DATA_OBRIGATORIA });
  assert.deepEqual(plain(validarDataPagamento('   ', HOJE)), { valida: false, erro: MSG_DATA_OBRIGATORIA });
  assert.deepEqual(plain(validarDataPagamento('2026-02-30', HOJE)), { valida: false, erro: MSG_DATA_INVALIDA });
  assert.deepEqual(plain(validarDataPagamento('2026-11-13', HOJE)), { valida: false, erro: MSG_DATA_FUTURA });
  assert.deepEqual(plain(validarDataPagamento('2027-01-01', HOJE)), { valida: false, erro: MSG_DATA_FUTURA });
  // hoje e passado sao aceitos (inclusive antes do cadastro do titulo)
  assert.deepEqual(plain(validarDataPagamento('2026-11-12', HOJE)), { valida: true, erro: '' });
  assert.deepEqual(plain(validarDataPagamento('2026-11-11', HOJE)), { valida: true, erro: '' });
  assert.deepEqual(plain(validarDataPagamento('2019-05-01', HOJE)), { valida: true, erro: '' });
});

test('avisoDataAnteriorAoCadastro avisa so quando a data e anterior ao dia do cadastro', () => {
  const criado = new Date(2026, 10, 10, 14, 0).toISOString(); // cadastro em 10/11/2026
  const aviso = avisoDataAnteriorAoCadastro('2026-11-09', criado);
  assert.ok(aviso && aviso.includes('10/11/2026'));
  assert.equal(avisoDataAnteriorAoCadastro('2026-11-10', criado), null); // mesmo dia
  assert.equal(avisoDataAnteriorAoCadastro('2026-11-11', criado), null);
  assert.equal(avisoDataAnteriorAoCadastro('2026-11-09', null), null);
  assert.equal(avisoDataAnteriorAoCadastro('2026-11-09', 'nao-e-data'), null);
  assert.equal(avisoDataAnteriorAoCadastro('invalida', criado), null);
});

test('estadoPagamento: sem pedido, aguardando, aprovado e pago', () => {
  assert.deepEqual(plain(estadoPagamento(CONTA, [], AGORA)), { estado: 'sem-pedido', pedido: null });

  const pendente = pedido({ request_id: 5, status: 'pending' });
  const aguardando = estadoPagamento(CONTA, [pendente], AGORA);
  assert.equal(aguardando.estado, 'aguardando-aprovacao');
  assert.equal(aguardando.pedido.request_id, 5);

  const aprovado = pedido({ request_id: 6, status: 'approved' });
  const concluir = estadoPagamento(CONTA, [pendente, aprovado], AGORA);
  assert.equal(concluir.estado, 'aprovado-aguardando-conclusao');
  assert.equal(concluir.pedido.request_id, 6);

  assert.deepEqual(
    plain(estadoPagamento({ id: 7, status: 'pago' }, [aprovado], AGORA)),
    { estado: 'pago', pedido: null },
  );
});

test('estadoPagamento ignora outra conta, outra skill, expirados, rejeitados e cancelados', () => {
  const ignorados = [
    pedido({ request_id: 1, target_account_id: 8 }),
    pedido({ request_id: 2, skill_key: 'account.mark_overdue' }),
    pedido({ request_id: 3, expires_at: new Date(2026, 10, 12, 15, 0).toISOString() }),
    pedido({ request_id: 4, status: 'approved', expires_at: new Date(2026, 10, 11, 9, 0).toISOString() }),
    pedido({ request_id: 5, status: 'rejected' }),
    pedido({ request_id: 6, status: 'cancelled' }),
    pedido({ request_id: 7, status: 'expired' }),
    pedido({ request_id: 8, expires_at: 'invalida' }),
  ];
  assert.deepEqual(plain(estadoPagamento(CONTA, ignorados, AGORA)), { estado: 'sem-pedido', pedido: null });
});

test('estadoPagamento escolhe o pedido mais recente do mesmo estado', () => {
  const a = pedido({ request_id: 3, status: 'pending' });
  const b = pedido({ request_id: 9, status: 'pending' });
  assert.equal(estadoPagamento(CONTA, [b, a], AGORA).pedido.request_id, 9);
});

test('rotuloEstadoPagamento', () => {
  assert.equal(rotuloEstadoPagamento({ estado: 'sem-pedido', pedido: null }), '');
  assert.equal(rotuloEstadoPagamento({ estado: 'aguardando-aprovacao', pedido: null }), 'Aguardando aprovação');
  assert.equal(
    rotuloEstadoPagamento({ estado: 'aprovado-aguardando-conclusao', pedido: null }),
    'Aprovado — falta concluir',
  );
  assert.equal(rotuloEstadoPagamento({ estado: 'pago', pedido: null }), 'Pago');
});

test('podeConcluirPagamento exige aprovado, permissao e usuario diferente do decisor', () => {
  const aprovado = { estado: 'aprovado-aguardando-conclusao', pedido: pedido({ status: 'approved' }) };
  assert.equal(podeConcluirPagamento(aprovado, true, 10, 20), true);
  assert.equal(podeConcluirPagamento(aprovado, true, 20, 20), false); // decisor
  assert.equal(podeConcluirPagamento(aprovado, false, 10, 20), false); // sem permissao
  assert.equal(podeConcluirPagamento(aprovado, true, 10, null), false); // decisor desconhecido
  assert.equal(podeConcluirPagamento(aprovado, true, null, 20), false); // sem usuario
  assert.equal(
    podeConcluirPagamento({ estado: 'aguardando-aprovacao', pedido: pedido() }, true, 10, 20),
    false,
  );
  assert.equal(podeConcluirPagamento({ estado: 'sem-pedido', pedido: null }, true, 10, 20), false);
});

test('descreverPagamento mostra "Pago em" so para titulo pago com data', () => {
  assert.equal(descreverPagamento({ status: 'pago', paid_on: '2026-11-03' }), 'Pago em 03/11/2026');
  assert.equal(descreverPagamento({ status: 'pago', paid_on: null }), null);
  assert.equal(descreverPagamento({ status: 'pago' }), null);
  assert.equal(descreverPagamento({ status: 'pago', paid_on: 'lixo' }), null);
  assert.equal(descreverPagamento({ status: 'aberto', paid_on: '2026-11-03' }), null);
});

test('a skill do pedido e account.mark_paid', () => {
  assert.equal(SKILL_MARK_PAID, 'account.mark_paid');
});
