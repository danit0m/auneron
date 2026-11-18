<#
.SYNOPSIS
    Script de homologacao local do Auneron (Windows PowerShell 5.1).

.DESCRIPTION
    Opera a homologacao local com um conjunto FIXO de comandos. Os segredos
    ficam em um cofre cifrado (DPAPI do usuario do Windows) FORA do repositorio.
    Nenhum comando imprime, exporta ou copia valores do cofre.

    Comandos: configurar, status, versao-banco, pendentes, backup, ensaiar, migrar,
              registrar-acao-pagamento, registrar-acao-atraso, detectar-vencidos, consultar,
              provisionar-deteccao-vencidos, vincular-deteccao-vencidos, desvincular-deteccao-vencidos,
              provisionar-orcamento-shadow, provisionar-conta-vencida-sintetica, redefinir-senha,
              subir-backend, parar-backend, subir-frontend, parar-frontend,
              preparar-instancia-importacao, subir-backend-importacao, parar-backend-importacao,
              subir-frontend-importacao, parar-frontend-importacao, remover-instancia-importacao

    INSTANCIA DE TESTE DA IMPORTACAO (A6). Uma copia SEPARADA da homologacao, em outro banco, onde a
    confirmacao de importacao pode ser exercida. Nunca toca no banco da homologacao nem nas portas
    8000/5180. Veja backend\docs\operations\A6_IMPORTACAO_RUNBOOK.md.
      preparar-instancia-importacao -Pasta <hml> -ConfirmarBackup <dump>: valida o dump (como o ensaiar),
        cria o banco auneron_test NO MESMO servidor da homologacao, restaura o dump nele e roda
        alembic upgrade head. Recusa se auneron_test ja existir (remova antes com
        remover-instancia-importacao) e se o servidor nao for local (a trava da importacao exige host
        local). A URL e derivada da DATABASE_URL do cofre trocando so o nome do banco, so no ambiente do
        filho; nunca e impressa. Sem -Confirmar (ou com -Simular) apenas mostra o que faria.
      subir-backend-importacao -Pasta <hml>: segundo backend na porta 8001 apontando para auneron_test,
        com APP_ENV=test, IMPORT_CONFIRMATION_TEST_ENABLED=true, MAINTENANCE_ENABLED=false (nenhum loop de
        manutencao roda) e INTELLIGENCE_MODE=off. Confere que o banco alvo e auneron_test e local.
      subir-frontend-importacao -Pasta <hml>: Vite na porta 5181 com AUNERON_BACKEND_URL apontando para a 8001.
      parar-backend-importacao / parar-frontend-importacao: param so os da 8001 / 5181.
      remover-instancia-importacao -Pasta <hml> -Confirmar: EXCLUI o banco auneron_test (e nenhum outro;
        o nome e conferido literalmente antes). E uma delecao: exige aprovacao de Daniel. Exige a porta
        8001 livre (rode parar-backend-importacao antes). Sem -Confirmar (ou com -Simular) apenas mostra.

    provisionar-orcamento-shadow: cria UM orcamento de implantacao (deployment_period, 7 dias) para a
      rodada S5 do shadow de vencidos, via backend\scripts\create_shadow_budget.py (modulo fixo). O limite
      de TOKENS e a reserva de pior caso de uma unica chamada do perfil local-qwen3-4b (preco zero: o
      limite de dinheiro nao restringe chamadas). Idempotente (informa "ja provisionado" e o id). Recusa
      se o banco nao for auneron_neon_synthetic (4). Sem -Confirmar apenas mostra o que faria. Exige -Pasta.
      Codigos: 0 ok/ja provisionado, 3 existente divergente ou periodo vencido, 4 banco errado.

    provisionar-conta-vencida-sintetica: cria UMA conta (cliente "SINTETICO S5 SHADOW", s5.shadow@example.com,
      valor 10.00, aberto, vencimento = hoje no BUSINESS_TIMEZONE - 10 dias), via
      backend\scripts\create_shadow_synthetic_account.py (modulo fixo). Idempotente pelo email. Nada mais e
      criado. Recusa se o banco nao for auneron_neon_synthetic (4). Sem -Confirmar apenas mostra o que faria.
      Exige -Pasta. Recusa (codigo 9) se a porta 8000 estiver ocupada: o backend deve estar PARADO, pois a
      deteccao roda sozinha no backend (~60 s) e, sem -Shadow, criaria o pedido sem o shadow observar.
      Codigos: 0 ok/ja provisionada, 3 existente divergente, 4 banco errado, 9 backend no ar.

    redefinir-senha -Email <email> [-PermitirUltimoAdmin]: recuperacao manual de senha. Executa o modulo fixo
      scripts.reset_user_password (backend\scripts\reset_user_password.py): gera senha TEMPORARIA forte,
      grava o hash, exige troca no primeiro acesso, vence em 30 min e REVOGA todas as sessoes do usuario na
      mesma transacao. A senha temporaria aparece UMA vez, na saida deste comando (nao e gravada em log nem no
      cofre). Recusa usuario inexistente (3), banco diferente de auneron_neon_synthetic (4), conta role=system
      (5) e o UNICO administrador ativo sem -PermitirUltimoAdmin (6). Sem -Confirmar (ou com -Simular) apenas
      mostra o que faria. Exige -Pasta e -Email. O backend pode estar no ar.
      Codigos: 0 ok, 2 uso/e-mail invalido, 3 inexistente, 4 banco errado, 5 system, 6 ultimo admin.

    subir-backend -Shadow -DigestModelo <64 hex> -OrcamentoShadow <id>: sobe o backend com o shadow do
      agente de vencidos ligado (INTELLIGENCE_MODE=shadow, OVERDUE_SHADOW_*, 1 conta por rodada,
      INTELLIGENCE_MAX_ATTEMPTS=1, INTELLIGENCE_CALL_TIMEOUT_SECONDS=300 = teto do shadow local com raciocinio). So com -Confirmar (ou -Simular, que
      mostra tudo sem subir). Antes de subir: memoria fisica livre >= 14 GiB (senao codigo 6) e, por GET em
      loopback (sem inferencia) em http://127.0.0.1:11434/api/version e /api/tags, qwen3:4b presente com
      digest igual a -DigestModelo (senao codigo 7 = Ollama/modelo ausente, 8 = digest diferente). Sem -Shadow
      nada muda (INTELLIGENCE_MODE=off continua o padrao). Recusa APP_ENV=production.
      Veja backend\docs\operations\S5_SHADOW_RUNBOOK.md.

    consultar -Relatorio shadow: orcamentos de inteligencia, chamadas de modelo e contagens/ultimos registros
      do dominio governado (contas SINTETICO, eventos, pedidos, decisoes, consumos, propostas) para comparar
      antes/depois da rodada. Somente leitura.

    provisionar-deteccao-vencidos: cria a conta de sistema sistema.vencimentos@auneron.core
      (role=system, active) se ela nao existir, via backend\scripts\create_system_principal.py
      (modulo fixo). Recusa se o banco conectado nao for auneron_neon_synthetic. Nao altera
      conta existente. Sem -Confirmar apenas mostra o que faria. Exige -Pasta.
      Codigos de saida: 0 ok/ja provisionada, 3 conta existente divergente, 4 banco errado.

    vincular-deteccao-vencidos / desvincular-deteccao-vencidos: vincula (ou desabilita, nunca
      apaga) o OverdueDetectionAgent a versao publicada de account.mark_overdue, via
      backend\scripts\create_overdue_detection_binding.py (modulo fixo). Recusa se o banco nao for
      auneron_neon_synthetic (4) e, ao vincular, se houver autoridade L3 ativa (5). Idempotente.
      Sem -Confirmar apenas mostra o que faria. Exige -Pasta. Codigos: 0 ok, 3 inelegivel, 4, 5.

    detectar-vencidos: varredura diaria de vencimento (POST /accounts/detect-overdue).
      Le a API_KEY vigente do cofre na hora e a entrega ao script
      backend\scripts\scheduled_detect_overdue.py so por variavel de ambiente.
      Email/senha da conta de servico vem do arquivo de credenciais externo.
      Repassa o codigo de saida do script (0 ok, 1 erro, 2 falhas parciais).
      Exige -Pasta. Para a tarefa agendada:
        powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\backend\scripts\homologacao.ps1 detectar-vencidos -Pasta <checkout>

    Exemplos:
      .\homologacao.ps1 configurar                 (o diretor, no terminal dele)
      .\homologacao.ps1 status
      .\homologacao.ps1 versao-banco -Pasta C:\auneron
      .\homologacao.ps1 backup
      .\homologacao.ps1 ensaiar -Pasta C:\auneron -ConfirmarBackup C:\...\hml-X.dump
      .\homologacao.ps1 migrar -Pasta C:\auneron -ConfirmarBackup C:\...\hml-X.dump
      .\homologacao.ps1 consultar -Relatorio pagamento -Conta 123   (somente leitura)
      .\homologacao.ps1 consultar -Relatorio catalogo
      .\homologacao.ps1 consultar -Relatorio sistema   (banco/porta e usuario de sistema, somente leitura)
      .\homologacao.ps1 consultar -Relatorio deteccao   (skill, binding, autoridade L3 e contas vencidas, somente leitura)
      .\homologacao.ps1 vincular-deteccao-vencidos -Pasta C:\auneron -Confirmar
      .\homologacao.ps1 provisionar-orcamento-shadow -Pasta C:\auneron -Confirmar
      .\homologacao.ps1 provisionar-conta-vencida-sintetica -Pasta C:\auneron -Confirmar
      .\homologacao.ps1 consultar -Relatorio shadow   (somente leitura)
      .\homologacao.ps1 redefinir-senha -Pasta C:\auneron -Email pessoa@empresa.com -Simular
      .\homologacao.ps1 redefinir-senha -Pasta C:\auneron -Email pessoa@empresa.com -Confirmar
      .\homologacao.ps1 subir-backend -Pasta C:\auneron -Shadow -DigestModelo <64 hex> -OrcamentoShadow <id> -Simular
      .\homologacao.ps1 subir-backend -Pasta C:\auneron -Shadow -DigestModelo <64 hex> -OrcamentoShadow <id> -Confirmar
      .\homologacao.ps1 parar-backend -Confirmar
      .\homologacao.ps1 preparar-instancia-importacao -Pasta C:\auneron -ConfirmarBackup C:\...\hml-X.dump -Simular
      .\homologacao.ps1 preparar-instancia-importacao -Pasta C:\auneron -ConfirmarBackup C:\...\hml-X.dump -Confirmar
      .\homologacao.ps1 subir-backend-importacao -Pasta C:\auneron -Confirmar
      .\homologacao.ps1 subir-frontend-importacao -Pasta C:\auneron -Confirmar
      .\homologacao.ps1 parar-frontend-importacao -Confirmar
      .\homologacao.ps1 parar-backend-importacao -Confirmar
      .\homologacao.ps1 remover-instancia-importacao -Pasta C:\auneron -Confirmar   (delecao: aprovacao de Daniel)

    Nao existe downgrade neste script. Veja backend/docs/DEPLOYMENT.md.
    Autoteste offline: backend/scripts/test_homologacao.ps1
#>
[CmdletBinding(DefaultParameterSetName = 'Normal')]
param(
    [Parameter(ParameterSetName = 'Normal', Position = 0, Mandatory = $true)]
    [ValidateSet(
        'configurar', 'status', 'versao-banco', 'pendentes', 'backup', 'ensaiar', 'migrar',
        'registrar-acao-pagamento', 'registrar-acao-atraso', 'detectar-vencidos', 'consultar',
        'provisionar-deteccao-vencidos', 'vincular-deteccao-vencidos', 'desvincular-deteccao-vencidos',
        'provisionar-orcamento-shadow', 'provisionar-conta-vencida-sintetica', 'redefinir-senha',
        'subir-backend', 'parar-backend', 'subir-frontend', 'parar-frontend',
        'preparar-instancia-importacao', 'subir-backend-importacao', 'parar-backend-importacao',
        'subir-frontend-importacao', 'parar-frontend-importacao', 'remover-instancia-importacao'
    )]
    [string]$Comando,

    # Raiz do checkout a usar (precisa ter backend\alembic.ini e frontend\package.json).
    [Parameter(ParameterSetName = 'Normal')][string]$Pasta,
    # Caminho do cofre (padrao: <Base>\cofre.xml).
    [Parameter(ParameterSetName = 'Normal')][string]$Cofre,
    # Pasta base de estado/logs/backups (padrao: %LOCALAPPDATA%\Auneron\homologacao).
    [Parameter(ParameterSetName = 'Normal')][string]$Base,
    # Destino do dump (backup).
    [Parameter(ParameterSetName = 'Normal')][string]$Destino,
    # Dump existente, criado ha menos de 24 h (ensaiar, migrar).
    [Parameter(ParameterSetName = 'Normal')][string]$ConfirmarBackup,
    # Relatorio fixo de somente leitura (consultar).
    [Parameter(ParameterSetName = 'Normal')][ValidateSet('pagamento', 'catalogo', 'sistema', 'deteccao', 'shadow')][string]$Relatorio,
    # Id da conta (consultar -Relatorio pagamento); inteiro positivo, passado como variavel do psql.
    [Parameter(ParameterSetName = 'Normal')][ValidateRange(1, 2147483647)][int]$Conta,
    # Shadow do agente de vencidos (somente subir-backend): liga o shadow com o digest e o orcamento abaixo.
    [Parameter(ParameterSetName = 'Normal')][switch]$Shadow,
    [Parameter(ParameterSetName = 'Normal')][string]$DigestModelo,
    [Parameter(ParameterSetName = 'Normal')][ValidateRange(1, 2147483647)][int]$OrcamentoShadow,
    # E-mail do usuario (somente redefinir-senha); validado por regex antes de ir como argumento do filho.
    [Parameter(ParameterSetName = 'Normal')][string]$Email,
    # Permite redefinir o UNICO administrador ativo (somente redefinir-senha).
    [Parameter(ParameterSetName = 'Normal')][switch]$PermitirUltimoAdmin,
    [Parameter(ParameterSetName = 'Normal')][switch]$Confirmar,
    [Parameter(ParameterSetName = 'Normal')][switch]$Simular,
    # Nao consulta portas/HTTP (so com status ou -Simular; usado no autoteste).
    [Parameter(ParameterSetName = 'Normal')][switch]$SemSondagem,

    # Uso interno do autoteste: apenas define as funcoes (dot-source).
    [Parameter(ParameterSetName = 'Funcoes', Mandatory = $true)][switch]$CarregarFuncoes
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
$script:PortaBackend = 8000
$script:PortaFrontend = 5180
$script:UrlBackend = 'http://127.0.0.1:8000'
$script:UrlFrontend = 'http://127.0.0.1:5180'

# Instancia de teste da importacao (A6): segundo backend/frontend e banco proprio. Valores FIXOS.
# A trava do codigo (import_confirm.confirmation_test_is_available) exige APP_ENV=test, a chave ligada,
# banco chamado auneron_test e host local; nada disso e relaxado aqui.
$script:PortaBackendImportacao = 8001
$script:PortaFrontendImportacao = 5181
$script:UrlBackendImportacao = 'http://127.0.0.1:8001'
$script:UrlFrontendImportacao = 'http://127.0.0.1:5181'
$script:BancoImportacao = 'auneron_test'
$script:AmbienteImportacaoFixo = [ordered]@{
    'APP_ENV'                            = 'test'
    'IMPORT_CONFIRMATION_TEST_ENABLED'   = 'true'
    'MAINTENANCE_ENABLED'                = 'false'
    'INTELLIGENCE_MODE'                  = 'off'
    'CORS_ORIGINS'                       = 'http://127.0.0.1:5181,http://localhost:5181'
}

# Segredos: sempre no cofre, nunca em texto puro.
$script:NomesSegredos = @('DATABASE_URL', 'API_KEY')

# Configuracao NAO secreta, padrao de homologacao (fonte: backend/app/core/config.py
# e backend/.env.example). Nao ficam no cofre.
$script:ConfigPadrao = [ordered]@{
    'APP_ENV'            = 'development'
    'DEBUG'              = 'false'
    'LOG_LEVEL'          = 'INFO'
    'CORS_ORIGINS'       = 'http://127.0.0.1:5180,http://localhost:5180'
    'FORWARDED_ALLOW_IPS' = '127.0.0.1'
    'BUSINESS_TIMEZONE'  = 'America/Sao_Paulo'
    'INTELLIGENCE_MODE'  = 'off'
}

# Shadow do agente de vencidos (rodada S5). Valores FIXOS; so -DigestModelo e -OrcamentoShadow variam.
# Timeout de chamada: carga a frio (~33 s) + raciocinio de ate 2048 tokens a ~19 tok/s (~108 s) + leitura do
# prompt nao cabem em 120 s. 300 s e o teto de INTELLIGENCE_CALL_TIMEOUT_SECONDS (config.py), aceito acima de
# 120 SOMENTE com OVERDUE_SHADOW_ENABLED=true + INTELLIGENCE_MODE=shadow fora de production (este bloco).
# Tentativas: 1 (sem retry, para a chamada unica).
$script:ShadowFixo = [ordered]@{
    'INTELLIGENCE_MODE'                    = 'shadow'
    'OVERDUE_SHADOW_ENABLED'               = 'true'
    'OVERDUE_SHADOW_DATA_CLASSIFICATION'   = 'synthetic'
    'OVERDUE_SHADOW_MAX_ACCOUNTS_PER_RUN'  = '1'
    'OVERDUE_SHADOW_MODEL_ID'              = 'qwen3:4b'
    'INTELLIGENCE_CALL_TIMEOUT_SECONDS'    = '300'
    'INTELLIGENCE_MAX_ATTEMPTS'            = '1'
}
$script:ShadowModeloId = 'qwen3:4b'
$script:ShadowMemoriaMinimaKb = 14 * 1024 * 1024   # 14 GiB em KB (FreePhysicalMemory e em KB)
$script:OllamaUrlVersao = 'http://127.0.0.1:11434/api/version'
$script:OllamaUrlTags = 'http://127.0.0.1:11434/api/tags'
# Codigos de saida proprios do pre-voo do shadow.
$script:CodigoShadowMemoria = 6
$script:CodigoShadowOllama = 7
$script:CodigoShadowDigest = 8
$script:CodigoBackendAtivo = 9

$script:Ctx = @{
    Base = ''; Cofre = ''; Pasta = ''
    Simular = $false; Confirmar = $false; SemSondagem = $false
    Relatorio = ''; Conta = 0
    Shadow = $false; DigestModelo = ''; OrcamentoShadow = 0
    Email = ''; PermitirUltimoAdmin = $false
}
$script:Conhecidos = New-Object System.Collections.ArrayList

# SQL FIXO do comando consultar (somente leitura; nenhum parametro escolhe SQL).
# A conta entra como variavel do psql (-v conta=<int>, usada como :conta), nunca
# concatenada no texto. Cada relatorio abre BEGIN READ ONLY e termina em ROLLBACK.
$script:SqlConsultaPagamento = @'
BEGIN READ ONLY;
\echo == Conta ==
SELECT a.id, a.cliente, a.status, a.vencimento
FROM accounts a
WHERE a.id = :conta;
\echo == Pagamento (account_payments) ==
SELECT p.id, p.paid_on, p.approval_request_id, p.created_at
FROM account_payments p
WHERE p.account_id = :conta
ORDER BY p.id;
\echo == Data efetiva do pedido (approval_request_inputs) ==
SELECT i.approval_request_id, i.effective_date, i.created_at
FROM approval_request_inputs i
WHERE i.approval_request_id IN (SELECT r.id FROM approval_requests r WHERE r.target_account_id = :conta OR r.id IN (SELECT pp.approval_request_id FROM account_payments pp WHERE pp.account_id = :conta))
ORDER BY i.approval_request_id;
\echo == Pedido de aprovacao (approval_requests) ==
SELECT r.id, sk.skill_key, sv.version, r.status, r.requester_actor_type, r.requester_reference, r.risk_level, r.created_at, r.expires_at, r.resolved_at
FROM approval_requests r
JOIN skill_versions sv ON sv.id = r.skill_version_id
JOIN skills sk ON sk.id = sv.skill_id
WHERE r.target_account_id = :conta OR r.id IN (SELECT pp.approval_request_id FROM account_payments pp WHERE pp.account_id = :conta)
ORDER BY r.id;
\echo == Decisao (approval_decisions) ==
SELECT d.approval_request_id, d.decision, d.decided_by_reference, d.decided_by_role, d.created_at
FROM approval_decisions d
WHERE d.approval_request_id IN (SELECT r.id FROM approval_requests r WHERE r.target_account_id = :conta OR r.id IN (SELECT pp.approval_request_id FROM account_payments pp WHERE pp.account_id = :conta))
ORDER BY d.approval_request_id;
\echo == Consumo da aprovacao (approval_consumptions) ==
SELECT c.approval_request_id, c.status, c.consumer_reference, c.authority_reference, c.authority_role, c.error_code, c.skill_invocation_id, c.reserved_at, c.finalized_at
FROM approval_consumptions c
WHERE c.approval_request_id IN (SELECT r.id FROM approval_requests r WHERE r.target_account_id = :conta OR r.id IN (SELECT pp.approval_request_id FROM account_payments pp WHERE pp.account_id = :conta))
ORDER BY c.approval_request_id;
\echo == Eventos da conta (account_events) ==
SELECT e.id, e.event_type, e.previous_status, e.new_status, e.actor_type, e.actor_reference, e.idempotency_key, e.occurred_at
FROM account_events e
WHERE e.account_id = :conta
ORDER BY e.occurred_at, e.id;
\echo == Invocacao da skill (skill_invocations; so o campo paid_on do output) ==
SELECT c.approval_request_id, si.id AS invocation_id, si.status, si.output_payload->>'paid_on' AS paid_on_output
FROM approval_consumptions c
JOIN skill_invocations si ON si.id = c.skill_invocation_id
WHERE c.approval_request_id IN (SELECT r.id FROM approval_requests r WHERE r.target_account_id = :conta OR r.id IN (SELECT pp.approval_request_id FROM account_payments pp WHERE pp.account_id = :conta))
ORDER BY si.id;
ROLLBACK;
'@

$script:SqlConsultaCatalogo = @'
BEGIN READ ONLY;
\echo == Catalogo de skills e versoes ==
SELECT s.skill_key, s.status AS skill_status, v.version, v.status AS version_status, v.execution_mode, v.published_at
FROM skills s
LEFT JOIN skill_versions v ON v.skill_id = s.id
ORDER BY s.skill_key, v.created_at, v.id;
ROLLBACK;
'@

$script:SqlConsultaSistema = @'
BEGIN READ ONLY;
\echo == Banco e porta interna ==
SELECT current_database() AS banco, inet_server_port() AS porta_interna;
\echo == Usuario de sistema (vencimentos) ==
SELECT email, role, active FROM users WHERE email = 'sistema.vencimentos@auneron.core';
ROLLBACK;
'@

# Relatorio da deteccao de vencidos: skill, binding do agente, autoridade L3 e contas elegiveis.
# CURRENT_DATE e a data do BANCO; o backend usa BUSINESS_TIMEZONE (America/Sao_Paulo), entao a
# contagem pode diferir perto da meia-noite UTC.
$script:SqlConsultaDeteccao = @'
BEGIN READ ONLY;
\echo == Banco ==
SELECT current_database() AS banco;
\echo == Skill account.mark_overdue ==
SELECT s.id, s.skill_key, s.status FROM skills s WHERE s.skill_key = 'account.mark_overdue';
\echo == Versoes da skill ==
SELECT v.id, v.version, v.status, v.execution_mode, v.runtime_kind
FROM skill_versions v
JOIN skills s ON s.id = v.skill_id
WHERE s.skill_key = 'account.mark_overdue'
ORDER BY v.id;
\echo == Bindings do OverdueDetectionAgent ==
SELECT b.id, b.skill_version_id, b.enabled, b.priority
FROM agent_skill_bindings b
WHERE b.agent_name = 'OverdueDetectionAgent'
ORDER BY b.id;
\echo == Autoridade L3 (policy_authority_grants da skill) ==
SELECT g.id, g.policy_key, g.state, g.expires_at
FROM policy_authority_grants g
WHERE g.skill_key = 'account.mark_overdue'
ORDER BY g.id;
\echo == Autoridade L3 ativa e nao expirada (contagem) ==
SELECT count(*) AS grants_ativos_nao_expirados
FROM policy_authority_grants g
WHERE g.skill_key = 'account.mark_overdue' AND g.state = 'active' AND g.expires_at > now();
\echo == Contas abertas vencidas (data do banco; o backend usa BUSINESS_TIMEZONE) ==
SELECT CURRENT_DATE AS hoje_banco, count(*) AS contas_abertas_vencidas
FROM accounts a
WHERE a.status = 'aberto' AND a.vencimento < CURRENT_DATE;
\echo == Contas abertas vencidas (ate 20) ==
SELECT a.id, a.vencimento
FROM accounts a
WHERE a.status = 'aberto' AND a.vencimento < CURRENT_DATE
ORDER BY a.vencimento, a.id
LIMIT 20;
ROLLBACK;
'@

# Relatorio do shadow (S5): orcamentos, chamadas de modelo e dominio governado antes/depois da rodada.
# Nenhuma coluna sensivel: so ids, chaves, digests, estados, contagens e a conta SINTETICO.
$script:SqlConsultaShadow = @'
BEGIN READ ONLY;
\echo == Banco ==
SELECT current_database() AS banco;
\echo == Orcamentos de inteligencia (intelligence_budgets) ==
SELECT b.id, b.scope_kind, b.scope_ref, b.period_start, b.period_end, b.limit_micro, b.limit_tokens, b.reserved_micro, b.reserved_tokens, b.settled_micro, b.settled_tokens, b.unknown_micro, b.unknown_tokens, b.overrun_micro, b.overrun_tokens, b.version, b.updated_at
FROM intelligence_budgets b
ORDER BY b.id;
\echo == Chamadas de modelo por estado (intelligence_model_calls) ==
SELECT c.state, count(*) AS chamadas
FROM intelligence_model_calls c
GROUP BY c.state
ORDER BY c.state;
\echo == Chamadas de modelo (ate 20 mais recentes; criada_em = reserved_at) ==
SELECT c.id, c.call_key, c.provider, c.model_profile, c.model_id, c.model_digest, c.provider_reported_model, c.runtime_version, c.latency_ms, c.state, c.error_class, c.release_basis, c.evidence_ref, c.reserved_tokens, c.actual_tokens, c.reserved_at AS criada_em, c.sent_at, c.settled_at
FROM intelligence_model_calls c
ORDER BY c.id DESC
LIMIT 20;
\echo == Contagens do dominio governado ==
SELECT (SELECT count(*) FROM accounts) AS accounts, (SELECT count(*) FROM account_events) AS account_events, (SELECT count(*) FROM approval_requests) AS approval_requests, (SELECT count(*) FROM approval_decisions) AS approval_decisions, (SELECT count(*) FROM approval_consumptions) AS approval_consumptions, (SELECT count(*) FROM authenticated_advisory_proposals) AS advisory_proposals;
\echo == Contas SINTETICO (accounts) ==
SELECT a.id, a.cliente, a.status, a.vencimento
FROM accounts a
WHERE a.cliente LIKE 'SINTETICO%'
ORDER BY a.id;
\echo == Eventos de conta (account_events; ultimos 10) ==
SELECT e.id, e.account_id, e.event_type, e.previous_status, e.new_status, e.actor_type, e.occurred_at
FROM account_events e
ORDER BY e.id DESC
LIMIT 10;
\echo == Pedidos de aprovacao (approval_requests; ultimos 10) ==
SELECT r.id, r.status, r.target_account_id, r.requester_actor_type, r.created_at, r.resolved_at
FROM approval_requests r
ORDER BY r.id DESC
LIMIT 10;
\echo == Decisoes de aprovacao (approval_decisions; ultimas 10) ==
SELECT d.id, d.approval_request_id, d.decision, d.created_at
FROM approval_decisions d
ORDER BY d.id DESC
LIMIT 10;
\echo == Consumos de aprovacao (approval_consumptions; ultimos 10) ==
SELECT c.id, c.approval_request_id, c.status, c.error_code, c.reserved_at, c.finalized_at
FROM approval_consumptions c
ORDER BY c.id DESC
LIMIT 10;
\echo == Propostas consultivas (authenticated_advisory_proposals; ultimas 10) ==
SELECT p.id, p.idempotency_key, p.snapshot_digest, p.created_at
FROM authenticated_advisory_proposals p
ORDER BY p.id DESC
LIMIT 10;
ROLLBACK;
'@

# ---------------------------------------------------------------------------
# Utilidades basicas
# ---------------------------------------------------------------------------
function Falhar {
    param([string]$Mensagem)
    throw $Mensagem
}

function Info {
    param([string]$Texto)
    Write-Host $Texto
}

function Registrar-Segredo {
    # Guarda valor (e variante percent-encoded) apenas em memoria, para mascarar saidas.
    param([string]$Valor)
    if ([string]::IsNullOrEmpty($Valor)) { return }
    if ($Valor.Length -lt 4) { return }
    [void]$script:Conhecidos.Add($Valor)
    $enc = [Uri]::EscapeDataString($Valor)
    if ($enc -ne $Valor) { [void]$script:Conhecidos.Add($enc) }
}

function Mascarar-Texto {
    param([string]$Texto)
    if ($null -eq $Texto) { return '' }
    $t = $Texto
    $ordenados = @($script:Conhecidos | Sort-Object -Property Length -Descending)
    foreach ($v in $ordenados) {
        if ($v) { $t = $t.Replace([string]$v, '***') }
    }
    return $t
}

function Remover-ValoresDeLinha {
    # Para exibir linha de comando de terceiros: tira o que parece credencial.
    param([string]$Linha)
    if ([string]::IsNullOrEmpty($Linha)) { return '' }
    $t = Mascarar-Texto $Linha
    $t = [regex]::Replace($t, '://[^/\s@"'']*@', '://***@')
    $t = [regex]::Replace($t, '(?i)((?:key|password|passwd|pwd|token|secret)[A-Za-z0-9_-]*(?:=|\s+|:))\S+', '$1***')
    if ($t.Length -gt 220) { $t = $t.Substring(0, 220) + '...' }
    return $t
}

function ConvertTo-ArgWin {
    param([string]$A)
    if ($A -eq '') { return '""' }
    if ($A -notmatch '[\s"]') { return $A }
    $s = $A -replace '(\\*)"', '$1$1\"'
    $s = $s -replace '(\\+)$', '$1$1'
    return '"' + $s + '"'
}

function Get-BaseDir {
    if ($script:Ctx.Base) { return $script:Ctx.Base }
    if (-not $env:LOCALAPPDATA) { Falhar 'LOCALAPPDATA nao definida; informe -Base.' }
    return (Join-Path $env:LOCALAPPDATA 'Auneron\homologacao')
}

function Get-CofreCaminho {
    if ($script:Ctx.Cofre) { return $script:Ctx.Cofre }
    return (Join-Path (Get-BaseDir) 'cofre.xml')
}

# ---------------------------------------------------------------------------
# Cofre (DPAPI do usuario; valores cifrados, nomes em claro)
# ---------------------------------------------------------------------------
function Ler-Cofre {
    $caminho = Get-CofreCaminho
    $r = @{}
    if (-not (Test-Path -LiteralPath $caminho)) { return $r }
    try {
        $o = Import-Clixml -LiteralPath $caminho
    }
    catch {
        Falhar 'Cofre ilegivel (arquivo corrompido). Rode configurar novamente.'
    }
    if ($o -isnot [hashtable]) {
        Falhar 'Cofre em formato inesperado. Rode configurar novamente.'
    }
    foreach ($k in @($o.Keys)) {
        if ($script:NomesSegredos -contains [string]$k) {
            $v = [string]$o[$k]
            if ($v) { $r[[string]$k] = $v }
        }
    }
    return $r
}

function Salvar-Cofre {
    param([hashtable]$Cifrados)
    $caminho = Get-CofreCaminho
    $dir = Split-Path -Parent $caminho
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        [void](New-Item -ItemType Directory -Force -Path $dir)
    }
    $tmp = $caminho + '.novo'
    Export-Clixml -LiteralPath $tmp -InputObject $Cifrados
    Move-Item -LiteralPath $tmp -Destination $caminho -Force
}

function Decifrar-Valor {
    param([string]$Cifrado)
    $bstr = [IntPtr]::Zero
    try {
        $ss = ConvertTo-SecureString -String $Cifrado
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
        $v = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    catch {
        Falhar 'Nao foi possivel decifrar o cofre (criado por outro usuario do Windows ou outra maquina?). Rode configurar novamente.'
    }
    finally {
        if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    }
    return $v
}

function Secure-ParaTexto {
    param([System.Security.SecureString]$Ss)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Ss)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Exigir-Segredos {
    param([hashtable]$Cofre, [string[]]$Nomes)
    $faltam = @($Nomes | Where-Object { -not $Cofre.ContainsKey($_) })
    if ($faltam.Count -gt 0) {
        Falhar ('Segredos nao definidos no cofre: ' + ($faltam -join ', ') + '. O diretor deve rodar: homologacao.ps1 configurar')
    }
}

function Obter-Segredo {
    param([hashtable]$Cofre, [string]$Nome)
    $v = Decifrar-Valor $Cofre[$Nome]
    Registrar-Segredo $v
    return $v
}

# ---------------------------------------------------------------------------
# DATABASE_URL
# ---------------------------------------------------------------------------
function ConvertFrom-DatabaseUrl {
    param([string]$Url)
    $m = [regex]::Match($Url, '^(?<esq>postgresql(?:\+psycopg2?)?)://(?<auth>[^/?#]*)(?:/(?<banco>[^?#]*))?(?:\?(?<q>[^#]*))?(?:#.*)?$')
    if (-not $m.Success) {
        Falhar 'DATABASE_URL: formato nao suportado (use postgresql://, postgresql+psycopg:// ou postgresql+psycopg2://).'
    }
    $auth = $m.Groups['auth'].Value
    $at = $auth.LastIndexOf('@')
    $userinfo = ''
    $hostport = $auth
    if ($at -ge 0) {
        $userinfo = $auth.Substring(0, $at)
        $hostport = $auth.Substring($at + 1)
    }
    $usuario = ''
    $senha = ''
    if ($userinfo) {
        $c = $userinfo.IndexOf(':')
        if ($c -ge 0) {
            $usuario = [Uri]::UnescapeDataString($userinfo.Substring(0, $c))
            $senha = [Uri]::UnescapeDataString($userinfo.Substring($c + 1))
        }
        else {
            $usuario = [Uri]::UnescapeDataString($userinfo)
        }
    }
    $hm = $null
    if ($hostport.StartsWith('[')) {
        $hm = [regex]::Match($hostport, '^\[(?<h>[^\]]+)\](?::(?<p>\d+))?$')
    }
    else {
        $hm = [regex]::Match($hostport, '^(?<h>[^:]*)(?::(?<p>\d+))?$')
    }
    if (-not $hm.Success) { Falhar 'DATABASE_URL: host/porta invalidos.' }
    $hostName = [Uri]::UnescapeDataString($hm.Groups['h'].Value)
    if (-not $hostName) { $hostName = 'localhost' }
    $porta = 5432
    if ($hm.Groups['p'].Success -and $hm.Groups['p'].Value) {
        $porta = [int]$hm.Groups['p'].Value
        if ($porta -lt 1 -or $porta -gt 65535) { Falhar 'DATABASE_URL: porta fora do intervalo.' }
    }
    $banco = [Uri]::UnescapeDataString($m.Groups['banco'].Value)
    if (-not $banco) { Falhar 'DATABASE_URL: o banco nao foi informado.' }
    $ssl = ''
    $q = $m.Groups['q'].Value
    if ($q) {
        $sm = [regex]::Match($q, '(?:^|&)sslmode=(?<v>[A-Za-z-]+)(?:&|$)')
        if ($sm.Success) { $ssl = $sm.Groups['v'].Value }
    }
    foreach ($campo in @($hostName, $usuario, $banco)) {
        if ($campo.StartsWith('-')) { Falhar 'DATABASE_URL: componente comeca com hifen (recusado).' }
    }
    return [pscustomobject]@{
        Host = $hostName; Porta = $porta; Usuario = $usuario
        Senha = $senha; Banco = $banco; SslMode = $ssl
    }
}

# ---------------------------------------------------------------------------
# Processos e portas
# ---------------------------------------------------------------------------
function Test-ProcessoAlvo {
    # Funcao pura: o processo (nome + linha de comando) e o uvicorn/vite esperado?
    param([string]$Nome, [string]$LinhaComando, [string]$Alvo)
    if ([string]::IsNullOrEmpty($Nome) -or [string]::IsNullOrEmpty($LinhaComando)) { return $false }
    if ($Alvo -eq 'backend') {
        if ($Nome -notmatch '(?i)^pythonw?(\d+(\.\d+)*)?(\.exe)?$') { return $false }
        if ($LinhaComando -notmatch '(?i)uvicorn') { return $false }
        if ($LinhaComando -notmatch 'app\.main:app') { return $false }
        return $true
    }
    if ($Alvo -eq 'frontend') {
        if ($Nome -notmatch '(?i)^node(\.exe)?$') { return $false }
        if ($LinhaComando -notmatch '(?i)(^|[\\/\s"])vite([\\/\s".]|$)') { return $false }
        return $true
    }
    return $false
}

function Get-Escutando {
    param([int]$Porta)
    $r = @()
    $con = @(Get-NetTCPConnection -State Listen -LocalPort $Porta -ErrorAction SilentlyContinue)
    $ids = @($con | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($idp in $ids) {
        $nome = ''
        $linha = ''
        $exe = ''
        $cim = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + [int]$idp) -ErrorAction SilentlyContinue
        if ($cim) {
            $nome = [string]$cim.Name
            $linha = [string]$cim.CommandLine
            $exe = [string]$cim.ExecutablePath
        }
        $enderecos = @($con | Where-Object { $_.OwningProcess -eq $idp } | Select-Object -ExpandProperty LocalAddress -Unique)
        $r += [pscustomobject]@{
            Pid = [int]$idp; Nome = $nome; LinhaComando = $linha
            Executavel = $exe; Enderecos = ($enderecos -join ',')
        }
    }
    return $r
}

function Descrever-Escutando {
    param($Item)
    return ('PID {0} ({1}) em {2}; comando: {3}' -f $Item.Pid, $Item.Nome, $Item.Enderecos, (Remover-ValoresDeLinha $Item.LinhaComando))
}

function Get-Http {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return [int]$r.StatusCode
    }
    catch {
        $resp = $_.Exception.Response
        if ($resp) { return [int]$resp.StatusCode }
        return 0
    }
}

function Exigir-PortaLivre {
    param([int]$Porta)
    if ($script:Ctx.SemSondagem) { Info ('(porta {0} nao verificada: -SemSondagem)' -f $Porta); return }
    $esc = @(Get-Escutando $Porta)
    if ($esc.Count -gt 0) {
        Falhar ('Porta {0} ja esta ocupada por: {1}' -f $Porta, (Descrever-Escutando $esc[0]))
    }
}

# ---------------------------------------------------------------------------
# Estado (PIDs/pastas dos processos iniciados por este script)
# ---------------------------------------------------------------------------
function Get-EstadoCaminho { return (Join-Path (Get-BaseDir) 'estado.json') }

function Ler-Estado {
    $r = @{}
    $c = Get-EstadoCaminho
    if (-not (Test-Path -LiteralPath $c)) { return $r }
    try {
        $o = Get-Content -LiteralPath $c -Raw | ConvertFrom-Json
        foreach ($p in $o.PSObject.Properties) {
            $r[$p.Name] = @{ pid = [int]$p.Value.pid; pasta = [string]$p.Value.pasta; iniciado = [string]$p.Value.iniciado }
        }
    }
    catch { }
    return $r
}

function Gravar-Estado {
    param([string]$Servico, $Idp, [string]$PastaUsada)
    $e = Ler-Estado
    if ($null -eq $Idp) { $e.Remove($Servico) }
    else { $e[$Servico] = @{ pid = [int]$Idp; pasta = $PastaUsada; iniciado = (Get-Date).ToString('s') } }
    $dir = Get-BaseDir
    if (-not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Force -Path $dir) }
    ($e | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath (Get-EstadoCaminho) -Encoding ASCII
}

# ---------------------------------------------------------------------------
# Execucao de processos filhos (segredos so por ambiente, nunca por argumento)
# ---------------------------------------------------------------------------
function Invoke-Filho {
    param(
        [string]$Exe,
        [string[]]$Argumentos = @(),
        [string]$DiretorioTrabalho = '',
        [hashtable]$Ambiente = @{},
        [string]$Entrada = ''
    )
    $psi =New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = (($Argumentos | ForEach-Object { ConvertTo-ArgWin $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    if ($DiretorioTrabalho) { $psi.WorkingDirectory = $DiretorioTrabalho }
    foreach ($k in @($Ambiente.Keys)) {
        if ($null -eq $Ambiente[$k]) {
            if ($psi.EnvironmentVariables.ContainsKey($k)) { $psi.EnvironmentVariables.Remove($k) }
        }
        else { $psi.EnvironmentVariables[$k] = [string]$Ambiente[$k] }
    }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()
    $tarefa = $p.StandardOutput.ReadToEndAsync()
    if ($Entrada) {
        # Entrada fixa em ASCII (bytes diretos, sem BOM); a saida ja esta sendo lida.
        $bytes = [Text.Encoding]::ASCII.GetBytes($Entrada)
        $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $p.StandardInput.BaseStream.Flush()
    }
    $p.StandardInput.Close()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    $out = $tarefa.Result
    $saida = Mascarar-Texto (($out + $err).TrimEnd())
    return [pscustomobject]@{ Codigo = $p.ExitCode; Saida = $saida }
}

function Aplicar-Ambiente {
    param([hashtable]$Amb)
    $salvo = @{}
    foreach ($k in @($Amb.Keys)) {
        $salvo[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
        [Environment]::SetEnvironmentVariable($k, $Amb[$k], 'Process')
    }
    return $salvo
}

function Restaurar-Ambiente {
    param([hashtable]$Salvo)
    foreach ($k in @($Salvo.Keys)) {
        [Environment]::SetEnvironmentVariable($k, $Salvo[$k], 'Process')
    }
}

function Montar-Ambiente {
    # Valores em memoria; devolve hashtable nome -> valor ($null = remover do filho).
    param([string]$Alvo)
    $cofre = Ler-Cofre
    $amb = @{}
    if ($Alvo -eq 'backend') {
        Exigir-Segredos $cofre $script:NomesSegredos
        foreach ($k in $script:ConfigPadrao.Keys) { $amb[$k] = $script:ConfigPadrao[$k] }
        foreach ($n in $script:NomesSegredos) {
            $v = Obter-Segredo $cofre $n
            $amb[$n] = $v
            if ($n -eq 'DATABASE_URL') {
                $db = ConvertFrom-DatabaseUrl $v
                Registrar-Segredo $db.Senha
            }
        }
        $amb['PYTHONUTF8'] = '1'
    }
    elseif ($Alvo -eq 'varredura') {
        # So a chave vigente: a varredura nao precisa do banco.
        Exigir-Segredos $cofre @('API_KEY')
        $amb['API_KEY'] = Obter-Segredo $cofre 'API_KEY'
        $amb['PYTHONUTF8'] = '1'
    }
    elseif ($Alvo -eq 'frontend') {
        Exigir-Segredos $cofre @('API_KEY')
        $amb['AUNERON_BACKEND_URL'] = $script:UrlBackend
        $amb['AUNERON_API_KEY'] = Obter-Segredo $cofre 'API_KEY'
    }
    return $amb
}

function Nomes-Do-Ambiente {
    param([string]$Alvo)
    if ($Alvo -eq 'backend') {
        return (@($script:ConfigPadrao.Keys) + $script:NomesSegredos + @('PYTHONUTF8')) -join ', '
    }
    if ($Alvo -eq 'varredura') { return 'API_KEY, PYTHONUTF8' }
    return 'AUNERON_BACKEND_URL, AUNERON_API_KEY'
}

# ---------------------------------------------------------------------------
# Pasta / Python / ferramentas
# ---------------------------------------------------------------------------
function Resolver-Pasta {
    if (-not $script:Ctx.Pasta) { Falhar 'Este comando exige -Pasta <raiz do checkout>.' }
    if (-not (Test-Path -LiteralPath $script:Ctx.Pasta -PathType Container)) { Falhar 'A pasta informada em -Pasta nao existe.' }
    $raiz = (Resolve-Path -LiteralPath $script:Ctx.Pasta).ProviderPath.TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $raiz 'backend\alembic.ini') -PathType Leaf)) {
        Falhar 'Pasta invalida: nao existe backend\alembic.ini nela.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $raiz 'frontend\package.json') -PathType Leaf)) {
        Falhar 'Pasta invalida: nao existe frontend\package.json nela.'
    }
    return $raiz
}

function Resolver-Python {
    # Unicas fontes aceitas: .venv do checkout de -Pasta, senao python.exe do PATH.
    param([string]$Raiz)
    $venv = Join-Path $Raiz 'backend\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv -PathType Leaf) { return $venv }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) {
        if (Test-PythonStoreStub $cmd.Source) {
            Falhar 'O python.exe do PATH e o atalho da Microsoft Store (WindowsApps), nao um Python real. Instale o Python ou crie backend\.venv no checkout.'
        }
        return $cmd.Source
    }
    Falhar 'Python nao encontrado (nem backend\.venv\Scripts\python.exe no checkout, nem python.exe no PATH).'
}

function Test-PythonStoreStub {
    # Funcao pura: o caminho e o stub da Microsoft Store?
    param([string]$Caminho)
    if ([string]::IsNullOrEmpty($Caminho)) { return $false }
    return ($Caminho -match '(?i)[\\/]WindowsApps[\\/]')
}

function Buscar-FerramentaPgEmRaiz {
    # Enumera os subdiretorios de versao (nome numerico) da raiz e testa
    # <versao>\bin\<nome>.exe; devolve o caminho da MAIOR versao numerica, ou $null.
    param([string]$Raiz, [string]$Nome)
    if ([string]::IsNullOrEmpty($Raiz)) { return $null }
    if (-not (Test-Path -LiteralPath $Raiz -PathType Container)) { return $null }
    $cands = @()
    $dirs = @(Get-ChildItem -LiteralPath $Raiz -Directory -ErrorAction SilentlyContinue)
    foreach ($d in $dirs) {
        if ($d.Name -notmatch '^[0-9]+(\.[0-9]+)*$') { continue }
        $exe = Join-Path (Join-Path $d.FullName 'bin') ($Nome + '.exe')
        if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { continue }
        $partes = @($d.Name.Split('.'))
        if ($partes.Count -lt 2) { $partes += '0' }
        if ($partes.Count -gt 4) { continue }
        $ver = $null
        try { $ver = [version]($partes -join '.') } catch { continue }
        $cands += [pscustomobject]@{ Versao = $ver; Caminho = $exe }
    }
    if ($cands.Count -eq 0) { return $null }
    $melhor = @($cands | Sort-Object -Property Versao -Descending)[0]
    return $melhor.Caminho
}

function Resolver-FerramentaPg {
    param([string]$Nome)  # 'pg_dump' | 'pg_restore'
    $cmd = Get-Command ($Nome + '.exe') -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return (Buscar-FerramentaPgEmRaiz 'C:\Program Files\PostgreSQL' $Nome)
}

function Resolver-FerramentaPgIrma {
    # Ferramenta (ex.: psql) do MESMO diretorio de uma ferramenta de referencia ja resolvida.
    param([string]$CaminhoReferencia, [string]$Nome)
    if ([string]::IsNullOrEmpty($CaminhoReferencia)) { return $null }
    $c = Join-Path (Split-Path -Parent $CaminhoReferencia) ($Nome + '.exe')
    if (Test-Path -LiteralPath $c -PathType Leaf) { return $c }
    return $null
}

function Exigir-FerramentaPg {
    param([string]$Nome)
    $c = Resolver-FerramentaPg $Nome
    if (-not $c) {
        Falhar ("{0} nao encontrado no PATH nem em C:\Program Files\PostgreSQL\*\bin. Instale o PostgreSQL Client Tools ou adicione a pasta bin ao PATH." -f $Nome)
    }
    return $c
}

# ---------------------------------------------------------------------------
# Alembic
# ---------------------------------------------------------------------------
function Rodar-Alembic {
    param([string]$Raiz, [string[]]$Args2, [string]$UrlBanco = '', [hashtable]$AmbienteExtra = @{})
    $py = Resolver-Python $Raiz
    $amb = Montar-Ambiente 'backend'
    foreach ($k in @($AmbienteExtra.Keys)) { $amb[$k] = $AmbienteExtra[$k] }
    if ($UrlBanco) {
        # Ensaio: o filho enxerga SO o banco descartavel (URL montada em memoria).
        Registrar-Segredo $UrlBanco
        $amb['DATABASE_URL'] = $UrlBanco
    }
    $res = Invoke-Filho -Exe $py -Argumentos (@('-m', 'alembic') + $Args2) -DiretorioTrabalho (Join-Path $Raiz 'backend') -Ambiente $amb
    if ($res.Codigo -ne 0) {
        $linhas = @($res.Saida -split "`r?`n" | Select-Object -Last 12)
        Falhar ("alembic {0} falhou (codigo {1}):`n{2}" -f ($Args2 -join ' '), $res.Codigo, ($linhas -join "`n"))
    }
    return $res.Saida
}

function Extrair-Revisoes {
    param([string]$Saida)
    $r = @()
    foreach ($l in ($Saida -split "`r?`n")) {
        $m = [regex]::Match($l, '^\s*(?<id>[0-9A-Za-z_]+)(\s+\(.*\))?\s*$')
        if ($m.Success) { $r += $m.Groups['id'].Value }
    }
    return $r
}

function Mostrar-Pendentes {
    param([string]$Raiz)
    $atual = @(Extrair-Revisoes (Rodar-Alembic $Raiz @('current')))
    $heads = @(Extrair-Revisoes (Rodar-Alembic $Raiz @('heads')))
    Info ('Revisao atual : ' + $(if ($atual.Count -gt 0) { $atual -join ', ' } else { '(banco sem versao)' }))
    Info ('Head          : ' + ($heads -join ', '))
    if ($atual.Count -gt 1 -or $heads.Count -ne 1) {
        Falhar 'Mais de uma revisao atual ou mais de um head; resolva manualmente (nada foi aplicado).'
    }
    if ($atual.Count -eq 1 -and $atual[0] -eq $heads[0]) {
        Info 'Nenhuma migracao pendente.'
        return 0
    }
    $inicio = 'base'
    if ($atual.Count -eq 1) { $inicio = $atual[0] }
    $hist = Rodar-Alembic $Raiz @('history', '-r', ($inicio + ':head'))
    $linhas = @($hist -split "`r?`n" | Where-Object { $_ -match '->' })
    Info 'Revisoes no caminho ate o head (a primeira e a atual, se houver):'
    foreach ($l in $linhas) { Info ('  ' + $l) }
    return $linhas.Count
}

# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Ensaio da migracao: funcoes puras e auxiliares
# ---------------------------------------------------------------------------
function Test-NomeBancoEnsaio {
    # Funcao pura: o nome e do padrao auneron_ensaio_<14 digitos> e NAO e o banco real?
    param([string]$Nome, [string]$BancoReal)
    if ([string]::IsNullOrEmpty($Nome)) { return $false }
    if ($Nome -cnotmatch '^auneron_ensaio_[0-9]{14}\z') { return $false }
    if ($BancoReal -and ($Nome -ieq $BancoReal)) { return $false }
    return $true
}

function Montar-UrlBancoEnsaio {
    # Funcao pura: mesma URL (esquema, credenciais como estao, host, porta, query);
    # so o nome do banco muda. O nome precisa ser do padrao do ensaio.
    param([string]$Url, [string]$NomeBanco)
    if (-not (Test-NomeBancoEnsaio $NomeBanco '')) { Falhar 'Nome de banco de ensaio invalido.' }
    return (Trocar-BancoDaUrl $Url $NomeBanco)
}

function Trocar-BancoDaUrl {
    # Funcao pura (sem validar o nome; quem chama valida): troca so o nome do banco da URL.
    param([string]$Url, [string]$NomeBanco)
    $m = [regex]::Match($Url, '^(?<esq>postgresql(?:\+psycopg2?)?)://(?<auth>[^/?#]*)(?:/(?<banco>[^?#]*))?(?:\?(?<q>[^#]*))?(?:#.*)?$')
    if (-not $m.Success) { Falhar 'DATABASE_URL: formato nao suportado.' }
    $r = $m.Groups['esq'].Value + '://' + $m.Groups['auth'].Value + '/' + $NomeBanco
    $q = $m.Groups['q'].Value
    if ($q) { $r += '?' + $q }
    return $r
}

function Get-DataUtcRecibo {
    param($Valor)
    if ($Valor -is [datetime]) { return $Valor.ToUniversalTime() }
    $s = [string]$Valor
    $dt = [datetime]::MinValue
    $ok = [datetime]::TryParseExact($s, "yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture,
        ([Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal), [ref]$dt)
    if (-not $ok) { return $null }
    return $dt
}

function Avaliar-ReciboEnsaio {
    # Funcao pura: '' se o recibo serve; senao o motivo da recusa.
    param($Recibo, [string]$Sha, [string]$Commit, [string]$Head, [datetime]$AgoraUtc)
    if ($null -eq $Recibo) { return 'recibo ilegivel' }
    if ([string]$Recibo.sha256 -ine $Sha) { return 'o recibo e de outro dump (SHA-256 diferente)' }
    if ([string]$Recibo.commit -ine $Commit) { return 'o recibo e de outro commit do checkout' }
    if ([string]$Recibo.revisao_final -ne $Head -or [string]$Recibo.head -ne $Head) {
        return 'a revisao final do ensaio nao e o head atual do checkout'
    }
    if ($Recibo.arvore_limpa -ne $true) { return 'o ensaio foi feito com a arvore do checkout suja' }
    $criado = Get-DataUtcRecibo $Recibo.criado_em_utc
    if ($null -eq $criado) { return 'data do recibo ilegivel' }
    $idade = $AgoraUtc - $criado
    if ($idade.TotalHours -ge 24 -or $idade.TotalSeconds -lt -300) { return ('o recibo tem {0:N1} h (limite 24 h)' -f $idade.TotalHours) }
    return ''
}

function Selecionar-ReciboEnsaio {
    # Funcao pura sobre uma lista de recibos. Devolve '' se algum serve; senao a mensagem de recusa.
    param($Recibos, [string]$Sha, [string]$Commit, [string]$Head, [datetime]$AgoraUtc)
    $mesmos = @($Recibos | Where-Object { $null -ne $_ -and [string]$_.sha256 -ieq $Sha })
    if ($mesmos.Count -eq 0) {
        return 'Nao ha recibo de ensaio para este dump; rode ensaiar antes de migrar.'
    }
    $ultimo = ''
    foreach ($r in $mesmos) {
        $mot = Avaliar-ReciboEnsaio $r $Sha $Commit $Head $AgoraUtc
        if ($mot -eq '') { return '' }
        $ultimo = $mot
    }
    return ('Nenhum recibo de ensaio serve (' + $ultimo + '); rode ensaiar antes de migrar.')
}

function Get-EnsaiosDir { return (Join-Path (Get-BaseDir) 'ensaios') }

function Ler-RecibosEnsaio {
    $dir = Get-EnsaiosDir
    $r = @()
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return $r }
    foreach ($f in @(Get-ChildItem -LiteralPath $dir -Filter 'ensaio-*.json' -File | Sort-Object Name)) {
        try { $r += (Get-Content -LiteralPath $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json) }
        catch { }
    }
    return $r
}

function Obter-InfoGit {
    param([string]$Raiz)
    $git = Get-Command git.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $git) { Falhar 'git.exe nao encontrado no PATH (necessario para identificar o commit do checkout).' }
    $h = Invoke-Filho -Exe $git.Source -Argumentos @('-C', $Raiz, 'rev-parse', 'HEAD')
    if ($h.Codigo -ne 0 -or $h.Saida.Trim() -notmatch '^[0-9a-fA-F]{40}$') { Falhar 'Nao foi possivel obter o commit do checkout (git rev-parse HEAD).' }
    $s = Invoke-Filho -Exe $git.Source -Argumentos @('--no-optional-locks', '-C', $Raiz, 'status', '--porcelain')
    if ($s.Codigo -ne 0) { Falhar 'git status falhou no checkout.' }
    return [pscustomobject]@{ Commit = $h.Saida.Trim().ToLowerInvariant(); Limpa = ($s.Saida.Trim() -eq '') }
}

function Obter-HeadUnico {
    param([string]$Raiz, [string]$UrlBanco = '', [hashtable]$AmbienteExtra = @{})
    $heads = @(Extrair-Revisoes (Rodar-Alembic $Raiz @('heads') $UrlBanco $AmbienteExtra))
    if ($heads.Count -ne 1) { Falhar 'O checkout tem mais de um head (ou nenhum) de migracao; resolva antes.' }
    return $heads[0]
}

function Validar-DumpArquivo {
    # Existe, nao vazio, menos de 24 h. Devolve o FileInfo.
    param([string]$Caminho, [string]$Opcao)
    if (-not (Test-Path -LiteralPath $Caminho -PathType Leaf)) { Falhar ('O arquivo de ' + $Opcao + ' nao existe.') }
    $item = Get-Item -LiteralPath $Caminho
    if ($item.Length -le 0) { Falhar 'O arquivo de backup esta vazio.' }
    $idade = (Get-Date) - $item.LastWriteTime
    if ($idade.TotalHours -ge 24 -or $idade.TotalSeconds -lt -300) {
        Falhar ('O backup tem {0:N1} h; precisa ter sido criado ha menos de 24 h. Rode backup de novo.' -f $idade.TotalHours)
    }
    return $item
}

function Invoke-Psql {
    # Sempre no banco de manutencao 'postgres'; senha so por ambiente.
    param([string]$Psql, $Db, [string]$Sql)
    $amb = @{ 'PGPASSWORD' = $(if ($Db.Senha) { $Db.Senha } else { $null }); 'PGCLIENTENCODING' = 'UTF8'; 'PGCONNECT_TIMEOUT' = '10' }
    if ($Db.SslMode) { $amb['PGSSLMODE'] = $Db.SslMode }
    $a = @('-h', $Db.Host, '-p', [string]$Db.Porta)
    if ($Db.Usuario) { $a += @('-U', $Db.Usuario) }
    $a += @('-d', 'postgres', '-w', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', $Sql)
    return (Invoke-Filho -Exe $Psql -Argumentos $a -Ambiente $amb)
}

function Limpar-BancoEnsaio {
    # UNICO ponto do script que remove um banco: so o descartavel criado nesta execucao,
    # com o nome conferido pelo padrao. Devolve '' se removeu, senao a mensagem de erro.
    param([string]$Psql, $Db, [string]$Nome)
    if (-not (Test-NomeBancoEnsaio $Nome $Db.Banco)) {
        return ('Remocao recusada: nome fora do padrao de ensaio (' + $Nome + ').')
    }
    $res = Invoke-Psql $Psql $Db ('DROP DATABASE "' + $Nome + '"')
    if ($res.Codigo -ne 0) {
        return ("Falha ao remover o banco descartavel {0}; REMOVA-O MANUALMENTE. Saida:`n{1}" -f $Nome, $res.Saida)
    }
    return ''
}

function Criar-BancoERestaurar {
    # Passos comuns do ensaio e da instancia de importacao: permissao CREATEDB, inexistencia do nome,
    # CREATE DATABASE e pg_restore --no-owner. $Estado.Criado so recebe o nome DEPOIS do CREATE bem-sucedido
    # (e o que autoriza a limpeza em quem chama). O nome ja deve ter sido validado por quem chama.
    param([string]$Psql, [string]$PgRestore, $Db, [string]$Nome, [string]$Dump, [hashtable]$Estado, [string]$Rotulo, [string]$MensagemExiste)
    $perm = Invoke-Psql $Psql $Db 'SELECT (rolcreatedb OR rolsuper) FROM pg_roles WHERE rolname = current_user'
    if ($perm.Codigo -ne 0) { Falhar ("Nao foi possivel consultar o servidor com psql:`n" + $perm.Saida) }
    if ($perm.Saida.Trim() -ne 't') {
        Falhar ("O usuario '{0}' nao tem permissao para criar bancos (CREATEDB); este comando precisa criar o banco {1}. Peca a um administrador do PostgreSQL: ALTER ROLE <usuario> CREATEDB." -f $Db.Usuario, $Rotulo)
    }
    $ex = Invoke-Psql $Psql $Db ("SELECT 1 FROM pg_database WHERE datname = '" + $Nome + "'")
    if ($ex.Codigo -ne 0) { Falhar ("Falha ao verificar o banco {0}:`n{1}" -f $Rotulo, $ex.Saida) }
    if ($ex.Saida.Trim() -ne '') { Falhar $MensagemExiste }
    $cr = Invoke-Psql $Psql $Db ('CREATE DATABASE "' + $Nome + '"')
    if ($cr.Codigo -ne 0) { Falhar ("Falha ao criar o banco {0}:`n{1}" -f $Rotulo, $cr.Saida) }
    $Estado.Criado = $Nome
    Info ('Banco {0} criado. Restaurando o dump ...' -f $Rotulo)
    $amb = @{ 'PGPASSWORD' = $(if ($Db.Senha) { $Db.Senha } else { $null }); 'PGCLIENTENCODING' = 'UTF8' }
    if ($Db.SslMode) { $amb['PGSSLMODE'] = $Db.SslMode }
    $ar = @('-h', $Db.Host, '-p', [string]$Db.Porta)
    if ($Db.Usuario) { $ar += @('-U', $Db.Usuario) }
    $ar += @('-d', $Nome, '--no-owner', '--exit-on-error', '-w', $Dump)
    $rs = Invoke-Filho -Exe $PgRestore -Argumentos $ar -Ambiente $amb
    if ($rs.Codigo -ne 0) {
        $l = @($rs.Saida -split "`r?`n" | Select-Object -Last 12)
        Falhar ("pg_restore falhou no banco {0} (codigo {1}):`n{2}" -f $Rotulo, $rs.Codigo, ($l -join "`n"))
    }
}

function Cmd-Ensaiar {
    if (-not $script:Ctx.ConfirmarBackup) {
        Falhar 'ensaiar exige -ConfirmarBackup <caminho do .dump> criado ha menos de 24 h (rode backup antes).'
    }
    $raiz = Resolver-Pasta
    $item = Validar-DumpArquivo $script:Ctx.ConfirmarBackup '-ConfirmarBackup'
    $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('DATABASE_URL')
    $url = Obter-Segredo $cofre 'DATABASE_URL'
    $db = ConvertFrom-DatabaseUrl $url
    Registrar-Segredo $db.Senha
    $nome = 'auneron_ensaio_' + (Get-Date).ToString('yyyyMMddHHmmss')
    if (-not (Test-NomeBancoEnsaio $nome $db.Banco)) { Falhar 'Nome do banco descartavel recusado (igual ao banco real ou fora do padrao).' }
    $urlEnsaio = Montar-UrlBancoEnsaio $url $nome
    Registrar-Segredo $urlEnsaio
    $pgrestore = Resolver-FerramentaPg 'pg_restore'
    $psql = Resolver-FerramentaPgIrma $pgrestore 'psql'
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera criado.'
        Info ('Dump     : {0} ({1} bytes, SHA-256 {2})' -f $item.FullName, $item.Length, $hash)
        Info ('Servidor : host={0} porta={1} usuario={2} (banco real {3} NAO sera tocado)' -f $db.Host, $db.Porta, $db.Usuario, $db.Banco)
        Info ('Descartavel: ' + $nome)
        Info ('pg_restore: ' + $(if ($pgrestore) { $pgrestore } else { 'NAO ENCONTRADO' }))
        Info ('psql      : ' + $(if ($psql) { $psql } else { 'NAO ENCONTRADO' }))
        Info ('Interpretador: ' + (Resolver-Python $raiz))
        Info ('Faria: pg_restore --list; checar permissao de criar banco e inexistencia do nome; criar o banco; pg_restore --no-owner; em {0}\backend, com DATABASE_URL do filho apontando ao descartavel: alembic current, upgrade head, current (exige 1 head e revisao final == head); remover SEMPRE o descartavel; gravar recibo em {1}.' -f $raiz, (Get-EnsaiosDir))
        return
    }
    if (-not $pgrestore) { [void](Exigir-FerramentaPg 'pg_restore') }
    if (-not $psql) { Falhar 'psql.exe nao encontrado na mesma pasta do pg_restore.' }
    $lst = Invoke-Filho -Exe $pgrestore -Argumentos @('--list', $item.FullName)
    if ($lst.Codigo -ne 0) { Falhar 'pg_restore --list reprovou o arquivo informado em -ConfirmarBackup.' }
    $git0 = Obter-InfoGit $raiz
    if (-not $git0.Limpa) { Info 'AVISO: a arvore do checkout esta suja; o recibo sera gravado, mas migrar o recusara.' }
    Info ('Dump aceito: {0} (SHA-256 {1})' -f $item.FullName, $hash)
    Info ('Banco descartavel: {0} em {1}:{2} (banco real {3} nao sera tocado)' -f $nome, $db.Host, $db.Porta, $db.Banco)

    $criado = ''
    $erroCorpo = $null
    $revIni = @()
    $revFim = @()
    $head = ''
    try {
        $estadoBanco = @{ Criado = '' }
        try {
            Criar-BancoERestaurar -Psql $psql -PgRestore $pgrestore -Db $db -Nome $nome -Dump $item.FullName -Estado $estadoBanco `
                -Rotulo 'descartavel' -MensagemExiste ('O banco ' + $nome + ' ja existe; nada foi feito. Tente de novo.')
        }
        finally { $criado = [string]$estadoBanco.Criado }
        Info 'Restauracao concluida. Rodando alembic no descartavel ...'
        $revIni = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current') $urlEnsaio))
        Info ('Revisao inicial: ' + $(if ($revIni.Count -gt 0) { $revIni -join ', ' } else { '(sem versao)' }))
        $head = Obter-HeadUnico $raiz $urlEnsaio
        $up = Rodar-Alembic $raiz @('upgrade', 'head') $urlEnsaio
        foreach ($l in @($up -split "`r?`n" | Where-Object { $_ -match 'Running upgrade' })) { Info ('  ' + $l) }
        $revFim = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current') $urlEnsaio))
        Info ('Revisao final  : ' + ($revFim -join ', '))
        if ($revFim.Count -ne 1 -or $revFim[0] -ne $head) {
            Falhar ('Revisao final do ensaio ({0}) difere do head ({1}).' -f ($revFim -join ', '), $head)
        }
    }
    catch { $erroCorpo = Mascarar-Texto ([string]$_.Exception.Message) }

    $erroLimpeza = ''
    if ($criado -ne '') {
        $erroLimpeza = Limpar-BancoEnsaio $psql $db $criado
        if ($erroLimpeza -eq '') { Info ('Banco descartavel removido: ' + $criado) }
    }
    if ($erroCorpo -or $erroLimpeza) {
        $partes = @()
        if ($erroCorpo) { $partes += ('ENSAIO FALHOU: ' + $erroCorpo) }
        if ($erroLimpeza) { $partes += $erroLimpeza }
        Falhar ($partes -join "`n")
    }

    $git1 = Obter-InfoGit $raiz
    if ($git1.Commit -ne $git0.Commit -or $git1.Limpa -ne $git0.Limpa) {
        Falhar 'O checkout mudou durante o ensaio (commit ou arvore); nenhum recibo foi gravado.'
    }
    $dirE = Get-EnsaiosDir
    if (-not (Test-Path -LiteralPath $dirE)) { [void](New-Item -ItemType Directory -Force -Path $dirE) }
    $agora = Get-Date
    $arq = Join-Path $dirE ('ensaio-' + $agora.ToString('yyyyMMdd-HHmmss') + '.json')
    if (Test-Path -LiteralPath $arq) { Falhar 'O recibo de ensaio ja existe; o script nunca sobrescreve.' }
    $recibo = [ordered]@{
        sha256         = $hash
        dump           = $item.FullName
        revisao_inicial = ($revIni -join ',')
        revisao_final  = $revFim[0]
        head           = $head
        commit         = $git1.Commit
        arvore_limpa   = $git1.Limpa
        criado_em_utc  = $agora.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
    }
    [IO.File]::WriteAllText($arq, ($recibo | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Info ''
    Info 'ENSAIO CONCLUIDO: o backup restaura e as migracoes pendentes aplicam sem erro.'
    Info ('SHA-256 do dump : ' + $hash)
    Info ('Revisao inicial : ' + $(if ($revIni.Count -gt 0) { $revIni -join ', ' } else { '(sem versao)' }))
    Info ('Revisao final   : ' + $revFim[0] + ' (head)')
    Info ('Commit          : {0} (arvore limpa: {1})' -f $git1.Commit, $(if ($git1.Limpa) { 'sim' } else { 'NAO' }))
    Info ('Recibo          : ' + $arq)
}

# ---------------------------------------------------------------------------
# Instancia de teste da importacao (A6)
# ---------------------------------------------------------------------------
function Test-NomeBancoImportacao {
    # Funcao pura: o nome e EXATAMENTE auneron_test (sensivel a caixa) e NAO e o banco da homologacao?
    param([string]$Nome, [string]$BancoReal)
    if ([string]::IsNullOrEmpty($Nome)) { return $false }
    if ($Nome -cne $script:BancoImportacao) { return $false }
    if ($BancoReal -and ($Nome -ieq $BancoReal)) { return $false }
    return $true
}

function Test-HostLocal {
    # Funcao pura: mesma lista da trava do backend (import_confirm.confirmation_test_is_available).
    param([string]$HostBanco)
    if ([string]::IsNullOrEmpty($HostBanco)) { return $false }
    return (@('localhost', '127.0.0.1', '::1') -contains $HostBanco.ToLowerInvariant())
}

function Montar-UrlBancoImportacao {
    # Funcao pura: a mesma URL da homologacao, trocando SO o nome do banco por auneron_test.
    param([string]$Url)
    return (Trocar-BancoDaUrl $Url $script:BancoImportacao)
}

function Montar-AmbienteImportacao {
    # Funcao pura: ambiente do segundo backend (valores fixos nao secretos + a URL do banco de teste).
    param([string]$UrlBancoTeste)
    $amb = @{}
    foreach ($k in $script:AmbienteImportacaoFixo.Keys) { $amb[$k] = $script:AmbienteImportacaoFixo[$k] }
    $amb['DATABASE_URL'] = $UrlBancoTeste
    return $amb
}

function Obter-ContextoImportacao {
    # Le o cofre e devolve o servidor da homologacao e a URL do auneron_test (so em memoria).
    # Recusa se o banco da homologacao ja for auneron_test ou se o servidor nao for local.
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('DATABASE_URL')
    $url = Obter-Segredo $cofre 'DATABASE_URL'
    $db = ConvertFrom-DatabaseUrl $url
    Registrar-Segredo $db.Senha
    if (-not (Test-NomeBancoImportacao $script:BancoImportacao $db.Banco)) {
        Falhar ('O banco da homologacao ja se chama ' + $db.Banco + '; a instancia de importacao exige um banco separado chamado auneron_test.')
    }
    if (-not (Test-HostLocal $db.Host)) {
        Falhar ('O servidor da homologacao ({0}) nao e local. A trava da importacao so libera a confirmacao com banco auneron_test em host local (localhost, 127.0.0.1 ou ::1); nada foi feito.' -f $db.Host)
    }
    $urlTeste = Montar-UrlBancoImportacao $url
    Registrar-Segredo $urlTeste
    return [pscustomobject]@{ Db = $db; UrlTeste = $urlTeste }
}

function Remover-BancoImportacao {
    # Segundo (e unico outro) ponto do script que exclui um banco: SO o auneron_test, com o nome conferido
    # literalmente antes. Devolve '' se excluiu, senao a mensagem de erro.
    param([string]$Psql, $Db, [string]$Nome)
    if (-not (Test-NomeBancoImportacao $Nome $Db.Banco)) {
        return ('Remocao recusada: so o banco auneron_test pode ser removido por este comando (' + $Nome + ').')
    }
    $res = Invoke-Psql $Psql $Db ('DROP DATABASE "' + $Nome + '"')
    if ($res.Codigo -ne 0) {
        return ("Falha ao remover o banco {0}. Saida:`n{1}" -f $Nome, $res.Saida)
    }
    return ''
}

function Cmd-PrepararInstanciaImportacao {
    if (-not $script:Ctx.ConfirmarBackup) {
        Falhar 'preparar-instancia-importacao exige -ConfirmarBackup <caminho do .dump> criado ha menos de 24 h (rode backup antes).'
    }
    $raiz = Resolver-Pasta
    $item = Validar-DumpArquivo $script:Ctx.ConfirmarBackup '-ConfirmarBackup'
    $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $ctx = Obter-ContextoImportacao
    $db = $ctx.Db
    $nome = $script:BancoImportacao
    $pgrestore = Resolver-FerramentaPg 'pg_restore'
    $psql = Resolver-FerramentaPgIrma $pgrestore 'psql'
    if ($script:Ctx.Simular -or (-not $script:Ctx.Confirmar)) {
        if ($script:Ctx.Simular) { Info 'SIMULACAO: nada sera criado.' }
        else { Info 'Nada foi criado. Repita com -Confirmar para executar (ou -Simular).' }
        Info ('Dump     : {0} ({1} bytes, SHA-256 {2})' -f $item.FullName, $item.Length, $hash)
        Info ('Servidor : host={0} porta={1} usuario={2} (banco da homologacao {3} NAO sera tocado)' -f $db.Host, $db.Porta, $db.Usuario, $db.Banco)
        Info ('Banco novo: ' + $nome)
        Info ('pg_restore: ' + $(if ($pgrestore) { $pgrestore } else { 'NAO ENCONTRADO' }))
        Info ('psql      : ' + $(if ($psql) { $psql } else { 'NAO ENCONTRADO' }))
        Info ('Interpretador: ' + (Resolver-Python $raiz))
        Info ('Faria: pg_restore --list; checar CREATEDB e que {0} NAO existe (se existir, recusa: remova antes com remover-instancia-importacao); criar o banco; pg_restore --no-owner; em {1}\backend, com DATABASE_URL do filho apontando a {0} e APP_ENV=test: alembic upgrade head e conferir revisao == head. Se algo falhar depois de criar, o {0} parcial e removido.' -f $nome, $raiz)
        return
    }
    if (-not $pgrestore) { [void](Exigir-FerramentaPg 'pg_restore') }
    if (-not $psql) { Falhar 'psql.exe nao encontrado na mesma pasta do pg_restore.' }
    $lst = Invoke-Filho -Exe $pgrestore -Argumentos @('--list', $item.FullName)
    if ($lst.Codigo -ne 0) { Falhar 'pg_restore --list reprovou o arquivo informado em -ConfirmarBackup.' }
    Info ('Dump aceito: {0} (SHA-256 {1})' -f $item.FullName, $hash)
    Info ('Banco novo: {0} em {1}:{2} (banco da homologacao {3} nao sera tocado)' -f $nome, $db.Host, $db.Porta, $db.Banco)
    $ambTeste = @{ 'APP_ENV' = 'test' }
    $estadoBanco = @{ Criado = '' }
    $erroCorpo = $null
    $revFim = @()
    $head = ''
    try {
        Criar-BancoERestaurar -Psql $psql -PgRestore $pgrestore -Db $db -Nome $nome -Dump $item.FullName -Estado $estadoBanco `
            -Rotulo 'da instancia de importacao' `
            -MensagemExiste ('O banco ' + $nome + ' ja existe; nada foi feito. Se for mesmo para recriar a instancia, pare o backend da 8001 e rode remover-instancia-importacao -Confirmar (delecao: precisa de aprovacao de Daniel) antes.')
        Info 'Restauracao concluida. Rodando alembic no banco de teste ...'
        $head = Obter-HeadUnico $raiz $ctx.UrlTeste $ambTeste
        $up = Rodar-Alembic $raiz @('upgrade', 'head') $ctx.UrlTeste $ambTeste
        foreach ($l in @($up -split "`r?`n" | Where-Object { $_ -match 'Running upgrade' })) { Info ('  ' + $l) }
        $revFim = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current') $ctx.UrlTeste $ambTeste))
        Info ('Revisao final: ' + ($revFim -join ', '))
        if ($revFim.Count -ne 1 -or $revFim[0] -ne $head) {
            Falhar ('Revisao final ({0}) difere do head ({1}).' -f ($revFim -join ', '), $head)
        }
    }
    catch { $erroCorpo = Mascarar-Texto ([string]$_.Exception.Message) }
    if ($erroCorpo) {
        $partes = @('PREPARACAO FALHOU: ' + $erroCorpo)
        if ($estadoBanco.Criado -ne '') {
            # So o banco criado NESTA execucao (nome conferido literalmente) e removido.
            $erroLimpeza = Remover-BancoImportacao $psql $db $estadoBanco.Criado
            if ($erroLimpeza -eq '') { $partes += ('O banco parcial ' + $nome + ' foi removido.') }
            else { $partes += $erroLimpeza; $partes += 'REMOVA-O com remover-instancia-importacao -Confirmar antes de tentar de novo.' }
        }
        Falhar ($partes -join "`n")
    }
    Info ''
    Info ('INSTANCIA PREPARADA: banco {0} restaurado do dump e na revisao {1} (head).' -f $nome, $revFim[0])
    Info 'Proximo passo: subir-backend-importacao -Pasta <hml> -Confirmar'
}

function Cmd-SubirBackendImportacao {
    $raiz = Resolver-Pasta
    $py = Resolver-Python $raiz
    $ctx = Obter-ContextoImportacao
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre $script:NomesSegredos
    # Defesa em profundidade: o banco do ambiente do filho tem de ser exatamente auneron_test, e local.
    $dbAlvo = ConvertFrom-DatabaseUrl $ctx.UrlTeste
    if ($dbAlvo.Banco -cne $script:BancoImportacao) { Falhar 'O banco alvo da instancia de importacao nao e auneron_test; nada foi iniciado.' }
    if (-not (Test-HostLocal $dbAlvo.Host)) { Falhar 'O servidor do banco alvo nao e local; nada foi iniciado.' }
    Exigir-PortaLivre $script:PortaBackendImportacao
    $extra = Montar-AmbienteImportacao $ctx.UrlTeste
    $args3 = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$script:PortaBackendImportacao)
    Info '== Backend da instancia de importacao (A6; banco de teste; sem producao) =='
    Info ('Banco alvo           : {0} em {1}:{2} (a URL completa nunca e impressa)' -f $dbAlvo.Banco, $dbAlvo.Host, $dbAlvo.Porta)
    Info ('Variaveis fixas      : ' + ((@($script:AmbienteImportacaoFixo.Keys) | ForEach-Object { '{0}={1}' -f $_, $script:AmbienteImportacaoFixo[$_] }) -join '; '))
    Info ('Porta               : {0} (a homologacao segue na {1})' -f $script:PortaBackendImportacao, $script:PortaBackend)
    if ((-not $script:Ctx.Simular) -and (-not $script:Ctx.Confirmar)) {
        Info 'Nada foi iniciado. Repita com -Confirmar para subir (ou -Simular).'
        return
    }
    if (-not $script:Ctx.Simular) {
        # O banco precisa existir (criado por preparar-instancia-importacao); so leitura no servidor.
        $pgrestore = Resolver-FerramentaPg 'pg_restore'
        $psql = Resolver-FerramentaPgIrma $pgrestore 'psql'
        if ($psql -and (-not $script:Ctx.SemSondagem)) {
            $ex = Invoke-Psql $psql $ctx.Db ("SELECT 1 FROM pg_database WHERE datname = '" + $script:BancoImportacao + "'")
            if ($ex.Codigo -ne 0) { Falhar ("Falha ao verificar o banco auneron_test:`n" + $ex.Saida) }
            if ($ex.Saida.Trim() -eq '') { Falhar 'O banco auneron_test nao existe; rode preparar-instancia-importacao antes. Nada foi iniciado.' }
        }
    }
    Iniciar-Servico -Alvo 'backend' -Porta $script:PortaBackendImportacao -Exe $py -Args3 $args3 -Dir (Join-Path $raiz 'backend') -Raiz $raiz -AmbienteExtra $extra `
        -Chave 'backend-importacao' -UrlBase $script:UrlBackendImportacao
}

function Cmd-RemoverInstanciaImportacao {
    $raiz = Resolver-Pasta
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('DATABASE_URL')
    $url = Obter-Segredo $cofre 'DATABASE_URL'
    $db = ConvertFrom-DatabaseUrl $url
    Registrar-Segredo $db.Senha
    $nome = $script:BancoImportacao
    if (-not (Test-NomeBancoImportacao $nome $db.Banco)) {
        Falhar ('O banco da homologacao ja se chama ' + $db.Banco + '; recusado.')
    }
    $pgrestore = Resolver-FerramentaPg 'pg_restore'
    $psql = Resolver-FerramentaPgIrma $pgrestore 'psql'
    Info '== Remocao da instancia de importacao (DELECAO: exige aprovacao de Daniel) =='
    Info ('Servidor : host={0} porta={1} usuario={2}' -f $db.Host, $db.Porta, $db.Usuario)
    Info ('Sera excluido apenas o banco {0}; o banco da homologacao ({1}) NAO sera tocado.' -f $nome, $db.Banco)
    if ($script:Ctx.Simular -or (-not $script:Ctx.Confirmar)) {
        if ($script:Ctx.Simular) { Info 'SIMULACAO: nada sera removido.' }
        else { Info 'Nada foi removido. Repita com -Confirmar (apos aprovacao de Daniel e com a 8001 parada).' }
        Info ('psql: ' + $(if ($psql) { $psql } else { 'NAO ENCONTRADO' }))
        return
    }
    Exigir-PortaLivre $script:PortaBackendImportacao
    if (-not $psql) { Falhar 'psql.exe nao encontrado (procurado ao lado do pg_restore).' }
    $ex = Invoke-Psql $psql $db ("SELECT 1 FROM pg_database WHERE datname = '" + $nome + "'")
    if ($ex.Codigo -ne 0) { Falhar ("Falha ao verificar o banco {0}:`n{1}" -f $nome, $ex.Saida) }
    if ($ex.Saida.Trim() -eq '') { Info ('O banco ' + $nome + ' nao existe; nada a remover.'); return }
    $erro = Remover-BancoImportacao $psql $db $nome
    if ($erro -ne '') { Falhar $erro }
    Info ('Banco removido: ' + $nome)
}

function Cmd-RegistrarAcaoPagamento {
    Rodar-RegistroDeAcao -Modulo 'scripts.register_account_mark_paid_skill' -Arquivo 'register_account_mark_paid_skill.py'
}

function Cmd-RegistrarAcaoAtraso {
    Rodar-RegistroDeAcao -Modulo 'scripts.register_account_mark_overdue_skill' -Arquivo 'register_account_mark_overdue_skill.py'
}

function Rodar-RegistroDeAcao {
    # Interno: so e chamado com modulo/arquivo constantes pelos Cmd-RegistrarAcao*; nenhum parametro do usuario chega aqui.
    param([string]$Modulo, [string]$Arquivo)
    $raiz = Resolver-Pasta
    $modulo = $Modulo
    $arqPy = Join-Path $raiz ('backend\scripts\' + $Arquivo)
    if (-not (Test-Path -LiteralPath $arqPy -PathType Leaf)) {
        Falhar ('Nao existe backend\scripts\' + $Arquivo + ' no checkout informado.')
    }
    $py = Resolver-Python $raiz
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Faria, em {0}\backend: {1} -m {2}' -f $raiz, $py, $modulo)
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    $amb = Montar-Ambiente 'backend'
    $res = Invoke-Filho -Exe $py -Argumentos @('-m', $modulo) -DiretorioTrabalho (Join-Path $raiz 'backend') -Ambiente $amb
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) {
        $script:Codigo = $res.Codigo
        Falhar ('{0} terminou com codigo {1}.' -f $modulo, $res.Codigo)
    }
}

function Cmd-ProvisionarDeteccaoVencidos {
    # Cria a conta de sistema da deteccao de vencidos. Modulo e interpretador fixos (nenhum
    # parametro do usuario escolhe executavel ou arquivo). O proprio script Python recusa
    # (codigo 4) se o banco conectado nao for o esperado. Sem -Confirmar nao grava.
    $raiz = Resolver-Pasta
    $arqPy = Join-Path $raiz 'backend\scripts\create_system_principal.py'
    if (-not (Test-Path -LiteralPath $arqPy -PathType Leaf)) {
        Falhar 'Nao existe backend\scripts\create_system_principal.py no checkout informado.'
    }
    $py = Resolver-Python $raiz
    if ($script:Ctx.Simular -or (-not $script:Ctx.Confirmar)) {
        if ($script:Ctx.Simular) { Info 'SIMULACAO: nada sera feito.' }
        else { Info 'Nada foi gravado. Repita com -Confirmar para provisionar.' }
        Info ('Faria, em {0}\backend: {1} -m scripts.create_system_principal' -f $raiz, $py)
        Info 'Efeito: cria sistema.vencimentos@auneron.core (role=system, active) se ausente; recusa se o banco nao for auneron_neon_synthetic; nao altera conta existente.'
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    $amb = Montar-Ambiente 'backend'
    $res = Invoke-Filho -Exe $py -Argumentos @('-m', 'scripts.create_system_principal') -DiretorioTrabalho (Join-Path $raiz 'backend') -Ambiente $amb
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) {
        $script:Codigo = $res.Codigo
        Falhar ('create_system_principal terminou com codigo {0} (3 = conta existente divergente, 4 = banco diferente do esperado).' -f $res.Codigo)
    }
}

function Rodar-BindingDeteccao {
    # Vincula/desvincula o OverdueDetectionAgent. Modulo e interpretador fixos; o modo e um
    # literal interno (nenhum parametro do usuario escolhe modulo, modo ou executavel). O
    # script Python recusa banco diferente do esperado (4) e L3 com autoridade ativa (5).
    param([string]$Modo)
    if ($Modo -ne 'vincular' -and $Modo -ne 'desvincular') { Falhar 'Modo interno invalido.' }
    $raiz = Resolver-Pasta
    $arqPy = Join-Path $raiz 'backend\scripts\create_overdue_detection_binding.py'
    if (-not (Test-Path -LiteralPath $arqPy -PathType Leaf)) {
        Falhar 'Nao existe backend\scripts\create_overdue_detection_binding.py no checkout informado.'
    }
    $py = Resolver-Python $raiz
    if ($script:Ctx.Simular -or (-not $script:Ctx.Confirmar)) {
        if ($script:Ctx.Simular) { Info 'SIMULACAO: nada sera feito.' }
        else { Info 'Nada foi gravado. Repita com -Confirmar para executar.' }
        Info ('Faria, em {0}\backend: {1} -m scripts.create_overdue_detection_binding {2}' -f $raiz, $py, $Modo)
        if ($Modo -eq 'vincular') {
            Info 'Efeito: vincula OverdueDetectionAgent a versao publicada de account.mark_overdue (prioridade 100, enabled); recusa se o banco nao for auneron_neon_synthetic ou se houver autoridade L3 ativa; idempotente; nao reabilita binding desabilitado.'
        }
        else {
            Info 'Efeito: desabilita (enabled=false, nunca apaga) o binding do OverdueDetectionAgent para account.mark_overdue; recusa se o banco nao for auneron_neon_synthetic.'
        }
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    $amb = Montar-Ambiente 'backend'
    $res = Invoke-Filho -Exe $py -Argumentos @('-m', 'scripts.create_overdue_detection_binding', $Modo) -DiretorioTrabalho (Join-Path $raiz 'backend') -Ambiente $amb
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) {
        $script:Codigo = $res.Codigo
        Falhar ('create_overdue_detection_binding terminou com codigo {0} (3 = inelegivel/binding desabilitado, 4 = banco diferente do esperado, 5 = L3 com autoridade ativa).' -f $res.Codigo)
    }
}

function Cmd-VincularDeteccaoVencidos { Rodar-BindingDeteccao -Modo 'vincular' }

function Cmd-DesvincularDeteccaoVencidos { Rodar-BindingDeteccao -Modo 'desvincular' }

function Cmd-DetectarVencidos {
    # Varredura diaria de vencimento. A chave vigente (API_KEY) sai do cofre no momento da execucao
    # e vai ao filho SO por variavel de ambiente. O script e o interpretador sao fixos (nenhum
    # parametro do usuario escolhe executavel ou arquivo). O codigo de saida do script e repassado.
    $raiz = Resolver-Pasta
    $arqPy = Join-Path $raiz 'backend\scripts\scheduled_detect_overdue.py'
    if (-not (Test-Path -LiteralPath $arqPy -PathType Leaf)) {
        Falhar 'Nao existe backend\scripts\scheduled_detect_overdue.py no checkout informado.'
    }
    $py = Resolver-Python $raiz
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Faria, em {0}\backend: {1} {2}' -f $raiz, $py, $arqPy)
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'varredura'))
        return
    }
    $amb = Montar-Ambiente 'varredura'
    $res = Invoke-Filho -Exe $py -Argumentos @($arqPy) -DiretorioTrabalho (Join-Path $raiz 'backend') -Ambiente $amb
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) {
        $script:Codigo = $res.Codigo
        Falhar ('scheduled_detect_overdue.py terminou com codigo {0} (1 = erro, 2 = falhas parciais).' -f $res.Codigo)
    }
}

function Cmd-Consultar {
    # Somente leitura: SQL fixo por relatorio, transacao read-only forcada em duas camadas
    # (PGOPTIONS no ambiente do filho e BEGIN READ ONLY / ROLLBACK no proprio SQL).
    $rel = [string]$script:Ctx.Relatorio
    $conta = [int]$script:Ctx.Conta
    if (-not $rel) { Falhar 'consultar exige -Relatorio pagamento|catalogo|sistema|deteccao|shadow.' }
    if ($rel -eq 'pagamento') {
        if ($conta -lt 1) { Falhar 'consultar -Relatorio pagamento exige -Conta <id inteiro positivo>.' }
        $sql = $script:SqlConsultaPagamento
    }
    elseif ($rel -eq 'catalogo') {
        if ($conta -ne 0) { Falhar '-Conta so vale com -Relatorio pagamento.' }
        $sql = $script:SqlConsultaCatalogo
    }
    elseif ($rel -eq 'sistema') {
        if ($conta -ne 0) { Falhar '-Conta so vale com -Relatorio pagamento.' }
        $sql = $script:SqlConsultaSistema
    }
    elseif ($rel -eq 'deteccao') {
        if ($conta -ne 0) { Falhar '-Conta so vale com -Relatorio pagamento.' }
        $sql = $script:SqlConsultaDeteccao
    }
    elseif ($rel -eq 'shadow') {
        if ($conta -ne 0) { Falhar '-Conta so vale com -Relatorio pagamento.' }
        $sql = $script:SqlConsultaShadow
    }
    else { Falhar 'Relatorio desconhecido.' }
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('DATABASE_URL')
    $url = Obter-Segredo $cofre 'DATABASE_URL'
    $db = ConvertFrom-DatabaseUrl $url
    Registrar-Segredo $db.Senha
    $ref = Resolver-FerramentaPg 'pg_dump'
    if (-not $ref) { $ref = Resolver-FerramentaPg 'pg_restore' }
    $psql = Resolver-FerramentaPgIrma $ref 'psql'
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera conectado nem consultado.'
        Info ('Relatorio: ' + $rel + $(if ($rel -eq 'pagamento') { ' (conta ' + $conta + ')' } else { '' }))
        Info ('Banco    : host={0} porta={1} banco={2} (transacao somente leitura)' -f $db.Host, $db.Porta, $db.Banco)
        Info ('psql     : ' + $(if ($psql) { $psql } else { 'NAO ENCONTRADO' }))
        Info 'Opcoes   : -X -v ON_ERROR_STOP=1 -P pager=off; ambiente do filho: PGPASSWORD (valor oculto), PGOPTIONS=-c default_transaction_read_only=on'
        if ($rel -eq 'pagamento') { Info ('Variavel : -v conta=' + $conta) }
        Info 'SQL fixo (stdin do psql):'
        foreach ($l in @($sql -split "`r?`n")) { Info ('  ' + $l) }
        return
    }
    if (-not $psql) { Falhar 'psql.exe nao encontrado na mesma pasta de pg_dump/pg_restore (PATH ou C:\Program Files\PostgreSQL\*\bin).' }
    $amb = @{
        'PGPASSWORD' = $(if ($db.Senha) { $db.Senha } else { $null })
        'PGCLIENTENCODING' = 'UTF8'
        'PGCONNECT_TIMEOUT' = '10'
        'PGOPTIONS' = '-c default_transaction_read_only=on'
    }
    if ($db.SslMode) { $amb['PGSSLMODE'] = $db.SslMode }
    $a = @('-h', $db.Host, '-p', [string]$db.Porta)
    if ($db.Usuario) { $a += @('-U', $db.Usuario) }
    $a += @('-d', $db.Banco, '-w', '-X', '-v', 'ON_ERROR_STOP=1', '-P', 'pager=off')
    if ($rel -eq 'pagamento') { $a += @('-v', ('conta=' + $conta)) }
    $a += @('-f', '-')
    Info ('Consultando (somente leitura) {0}:{1}/{2}: relatorio {3} ...' -f $db.Host, $db.Porta, $db.Banco, $rel)
    $res = Invoke-Filho -Exe $psql -Argumentos $a -Ambiente $amb -Entrada $sql
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) { Falhar ('psql terminou com codigo {0}.' -f $res.Codigo) }
}

function Cmd-Configurar {
    # Recusa ANTES de ler qualquer coisa.
    $interativo = [Environment]::UserInteractive
    $redirecionado = $false
    try { $redirecionado = [Console]::IsInputRedirected } catch { $redirecionado = $true }
    if ((-not $interativo) -or $redirecionado) {
        Falhar 'configurar so roda em terminal interativo (o diretor, no terminal dele). Sessao nao interativa ou entrada redirecionada: nada foi lido.'
    }
    $cofre = Ler-Cofre
    $novo = @{}
    foreach ($k in $cofre.Keys) { $novo[$k] = $cofre[$k] }
    Info ('Cofre: ' + (Get-CofreCaminho))
    Info 'Digite o valor de cada segredo (nao aparece na tela). Enter mantem o valor ja gravado.'
    foreach ($n in $script:NomesSegredos) {
        $estado = 'nao definido'
        if ($novo.ContainsKey($n)) { $estado = 'definido' }
        $ss = Read-Host -Prompt ("{0} [{1}]" -f $n, $estado) -AsSecureString
        if ($ss.Length -eq 0) {
            if (-not $novo.ContainsKey($n)) { Info ('  ' + $n + ': continua nao definido') }
            else { Info ('  ' + $n + ': mantido') }
            continue
        }
        $ok = $true
        if ($n -eq 'API_KEY' -and $ss.Length -lt 32) {
            Info '  API_KEY: precisa ter pelo menos 32 caracteres; valor ignorado.'
            $ok = $false
        }
        if ($ok -and $n -eq 'DATABASE_URL') {
            $txt = Secure-ParaTexto $ss
            try { [void](ConvertFrom-DatabaseUrl $txt) }
            catch { Info ('  DATABASE_URL: ' + $_.Exception.Message + ' Valor ignorado.'); $ok = $false }
            $txt = $null
        }
        if ($ok) {
            $novo[$n] = ConvertFrom-SecureString -SecureString $ss
            Info ('  ' + $n + ': gravado')
        }
    }
    Salvar-Cofre $novo
    Info ''
    Info 'Situacao do cofre:'
    $cofre2 = Ler-Cofre
    foreach ($n in $script:NomesSegredos) {
        Info ('  {0}: {1}' -f $n, $(if ($cofre2.ContainsKey($n)) { 'definido' } else { 'nao definido' }))
    }
}

function Cmd-Status {
    $caminho = Get-CofreCaminho
    Info '== Cofre =='
    Info ('Caminho: ' + $caminho)
    $existe = Test-Path -LiteralPath $caminho
    Info ('Existe : ' + $(if ($existe) { 'sim' } else { 'nao' }))
    if ($existe) {
        $cofre = Ler-Cofre
        foreach ($n in $script:NomesSegredos) {
            Info ('  {0}: {1}' -f $n, $(if ($cofre.ContainsKey($n)) { 'definido' } else { 'nao definido' }))
        }
    }
    Info ''
    Info '== Configuracao nao secreta (padrao de homologacao) =='
    foreach ($k in $script:ConfigPadrao.Keys) { Info ('  {0}={1}' -f $k, $script:ConfigPadrao[$k]) }
    Info ''
    Info '== Estado gravado pelo script =='
    $e = Ler-Estado
    if ($e.Count -eq 0) { Info '  (vazio)' }
    foreach ($k in $e.Keys) { Info ('  {0}: PID {1}, pasta {2}, iniciado {3}' -f $k, $e[$k].pid, $e[$k].pasta, $e[$k].iniciado) }
    Info ''
    if ($script:Ctx.SemSondagem) {
        Info '== Portas e HTTP: nao consultados (-SemSondagem) =='
        return
    }
    Info '== Portas =='
    foreach ($par in @(@($script:PortaBackend, 'backend'), @($script:PortaFrontend, 'frontend'))) {
        $porta = [int]$par[0]
        $alvo = [string]$par[1]
        $esc = @(Get-Escutando $porta)
        if ($esc.Count -eq 0) { Info ('  {0}: livre' -f $porta); continue }
        foreach ($i in $esc) {
            $casa = Test-ProcessoAlvo -Nome $i.Nome -LinhaComando $i.LinhaComando -Alvo $alvo
            Info ('  {0}: {1}; reconhecido como {2}: {3}' -f $porta, (Descrever-Escutando $i), $alvo, $(if ($casa) { 'sim' } else { 'NAO' }))
            if ($i.Executavel) { Info ('        executavel: ' + $i.Executavel) }
        }
    }
    Info ''
    Info '== Backend HTTP =='
    Info ('  GET /health : ' + (Get-Http ($script:UrlBackend + '/health')) + ' (0 = sem resposta)')
    Info ('  GET /ready  : ' + (Get-Http ($script:UrlBackend + '/ready')) + ' (503 = banco offline)')
}

function Cmd-VersaoBanco {
    $raiz = Resolver-Pasta
    $atual = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current')))
    if ($atual.Count -eq 0) { Info 'Revisao atual: (banco sem versao)' }
    else { Info ('Revisao atual: ' + ($atual -join ', ')) }
}

function Cmd-Pendentes {
    $raiz = Resolver-Pasta
    [void](Mostrar-Pendentes $raiz)
}

function Cmd-Backup {
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('DATABASE_URL')
    $url = Obter-Segredo $cofre 'DATABASE_URL'
    $db = ConvertFrom-DatabaseUrl $url
    Registrar-Segredo $db.Senha
    if ($script:Ctx.Destino) { $dest = [IO.Path]::GetFullPath($script:Ctx.Destino) }
    else { $dest = Join-Path (Join-Path (Get-BaseDir) 'backups') ('hml-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '.dump') }
    if (Test-Path -LiteralPath $dest) { Falhar 'O destino do backup ja existe; o script nunca sobrescreve. Escolha outro -Destino.' }
    $pgdump = Resolver-FerramentaPg 'pg_dump'
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Banco   : host={0} porta={1} usuario={2} banco={3}' -f $db.Host, $db.Porta, $db.Usuario, $db.Banco)
        Info ('Destino : ' + $dest)
        if ($pgdump) { Info ('pg_dump : ' + $pgdump) }
        else { Info 'AVISO: pg_dump nao encontrado; instale o PostgreSQL Client Tools ou ponha a pasta bin no PATH.' }
        Info 'Faria: pg_dump -F c (senha via PGPASSWORD no ambiente), pg_restore --list, tamanho e SHA-256.'
        return
    }
    if (-not $pgdump) { [void](Exigir-FerramentaPg 'pg_dump') }
    $pgrestore = Exigir-FerramentaPg 'pg_restore'
    $dir = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Force -Path $dir) }
    $amb = @{ 'PGPASSWORD' = $(if ($db.Senha) { $db.Senha } else { $null }); 'PGCLIENTENCODING' = 'UTF8' }
    if ($db.SslMode) { $amb['PGSSLMODE'] = $db.SslMode }
    $args1 = @('-h', $db.Host, '-p', [string]$db.Porta)
    if ($db.Usuario) { $args1 += @('-U', $db.Usuario) }
    $args1 += @('-d', $db.Banco, '-F', 'c', '-w', '-f', $dest)
    Info ('Executando pg_dump em {0}:{1}/{2} ...' -f $db.Host, $db.Porta, $db.Banco)
    $res = Invoke-Filho -Exe $pgdump -Argumentos $args1 -Ambiente $amb
    if ($res.Codigo -ne 0) {
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
        Falhar ("pg_dump falhou (codigo {0}):`n{1}" -f $res.Codigo, $res.Saida)
    }
    $lst = Invoke-Filho -Exe $pgrestore -Argumentos @('--list', $dest)
    if ($lst.Codigo -ne 0) {
        Falhar ("pg_restore --list reprovou o arquivo; NAO USE este dump: {0}`n{1}" -f $dest, $lst.Saida)
    }
    $item = Get-Item -LiteralPath $dest
    if ($item.Length -le 0) { Falhar ('Dump vazio: ' + $dest) }
    $hash = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
    $entradas = @($lst.Saida -split "`r?`n" | Where-Object { $_ -and -not $_.StartsWith(';') }).Count
    Info 'Backup validado.'
    Info ('Arquivo : ' + $dest)
    Info ('Tamanho : {0} bytes' -f $item.Length)
    Info ('SHA-256 : ' + $hash)
    Info ('Entradas no catalogo (pg_restore --list): ' + $entradas)
}

function Cmd-Migrar {
    if (-not $script:Ctx.ConfirmarBackup) {
        Falhar 'migrar exige -ConfirmarBackup <caminho do .dump> criado ha menos de 24 h (rode backup antes).'
    }
    if ($script:Ctx.SemSondagem -and -not $script:Ctx.Simular) {
        Falhar '-SemSondagem so vale com -Simular ou status.'
    }
    $raiz = Resolver-Pasta
    $item = Validar-DumpArquivo $script:Ctx.ConfirmarBackup '-ConfirmarBackup'
    $idade = (Get-Date) - $item.LastWriteTime
    $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $recibos = @(Ler-RecibosEnsaio)
    if (@($recibos | Where-Object { [string]$_.sha256 -ieq $hash }).Count -eq 0) {
        Falhar ('Nao ha recibo de ensaio para este dump (SHA-256 {0}); rode ensaiar antes de migrar.' -f $hash)
    }
    $pgrestore = Exigir-FerramentaPg 'pg_restore'
    $lst = Invoke-Filho -Exe $pgrestore -Argumentos @('--list', $item.FullName)
    if ($lst.Codigo -ne 0) { Falhar 'pg_restore --list reprovou o arquivo informado em -ConfirmarBackup.' }
    $gitInfo = Obter-InfoGit $raiz
    if (-not $gitInfo.Limpa) { Falhar 'A arvore do checkout de -Pasta esta suja; migrar exige checkout limpo (e um novo ensaiar).' }
    $headAtual = Obter-HeadUnico $raiz
    $mot = Selecionar-ReciboEnsaio $recibos $hash $gitInfo.Commit $headAtual ((Get-Date).ToUniversalTime())
    if ($mot -ne '') { Falhar $mot }
    Info ('Recibo de ensaio aceito (commit {0}, head {1}).' -f $gitInfo.Commit, $headAtual)
    Exigir-PortaLivre $script:PortaBackend
    Info ('Backup aceito: {0} ({1:N1} h, {2} bytes)' -f $item.FullName, $idade.TotalHours, $item.Length)
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Faria, em {0}\backend: alembic current/heads/history (somente leitura), depois python -m alembic upgrade head, depois alembic current.' -f $raiz)
        Info ('Interpretador: ' + (Resolver-Python $raiz))
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    Info 'Pendentes antes de migrar:'
    [void](Mostrar-Pendentes $raiz)
    Info 'Aplicando: alembic upgrade head ...'
    $saida = Rodar-Alembic $raiz @('upgrade', 'head')
    $linhas = @($saida -split "`r?`n" | Where-Object { $_ -match 'Running upgrade' })
    foreach ($l in $linhas) { Info ('  ' + $l) }
    $atual = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current')))
    Info ('Revisao final: ' + ($atual -join ', '))
}

function Aguardar-Porta {
    param([int]$Porta, $Processo, [int]$TimeoutSeg)
    $limite = (Get-Date).AddSeconds($TimeoutSeg)
    while ((Get-Date) -lt $limite) {
        $esc = @(Get-Escutando $Porta)
        if ($esc.Count -gt 0) { return $esc }
        if ($Processo -and $Processo.HasExited) { return @() }
        Start-Sleep -Milliseconds 1000
    }
    return @()
}

function Mostrar-FimDoLog {
    param([string]$Arquivo)
    if (Test-Path -LiteralPath $Arquivo) {
        $l = @(Get-Content -LiteralPath $Arquivo -Tail 15 -ErrorAction SilentlyContinue)
        foreach ($x in $l) { Info ('   | ' + (Mascarar-Texto $x)) }
    }
}

function Iniciar-Servico {
    param([string]$Alvo, [int]$Porta, [string]$Exe, [string[]]$Args3, [string]$Dir, [string]$Raiz, [hashtable]$AmbienteExtra = @{}, [string]$Chave = '', [string]$UrlBase = '')
    # $Chave: nome de logs/estado (padrao = $Alvo); $UrlBase: URL do servico (padrao = a da homologacao).
    if (-not $Chave) { $Chave = $Alvo }
    if (-not $UrlBase) { $UrlBase = $(if ($Alvo -eq 'backend') { $script:UrlBackend } else { $script:UrlFrontend }) }
    $logs = Join-Path (Get-BaseDir) 'logs'
    $carimbo = (Get-Date).ToString('yyyyMMdd-HHmmss')
    $logOut = Join-Path $logs ($Chave + '-' + $carimbo + '.out.log')
    $logErr = Join-Path $logs ($Chave + '-' + $carimbo + '.err.log')
    $linhaArgs = (($Args3 | ForEach-Object { ConvertTo-ArgWin $_ }) -join ' ')
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Interpretador/executavel: ' + $Exe)
        Info ('Executaria : {0} {1}' -f $Exe, $linhaArgs)
        Info ('Em         : ' + $Dir)
        Info ('Ambiente   : ' + (Nomes-Do-Ambiente $Alvo) + ' (valores do cofre, so no ambiente do processo filho)')
        if ($AmbienteExtra.Count -gt 0) { Info ('Ambiente extra (nomes; valores so no filho): ' + ((@($AmbienteExtra.Keys) | Sort-Object) -join ', ')) }
        Info ('Logs       : ' + $logOut + ' e ' + $logErr)
        return
    }
    if (-not (Test-Path -LiteralPath $logs)) { [void](New-Item -ItemType Directory -Force -Path $logs) }
    $entrada = Join-Path (Get-BaseDir) 'entrada-vazia.txt'
    if (-not (Test-Path -LiteralPath $entrada)) { Set-Content -LiteralPath $entrada -Value '' -Encoding ASCII }
    $amb = Montar-Ambiente $Alvo
    # Extras (shadow): sobrescrevem o padrao so no ambiente do filho; nunca contem segredos.
    foreach ($k in @($AmbienteExtra.Keys)) { $amb[$k] = $AmbienteExtra[$k] }
    if ($AmbienteExtra.ContainsKey('DATABASE_URL')) { Registrar-Segredo ([string]$AmbienteExtra['DATABASE_URL']) }
    $salvo = Aplicar-Ambiente $amb
    $proc = $null
    try {
        $proc = Start-Process -FilePath $Exe -ArgumentList $linhaArgs -WorkingDirectory $Dir -WindowStyle Hidden -PassThru `
            -RedirectStandardInput $entrada -RedirectStandardOutput $logOut -RedirectStandardError $logErr
    }
    finally { Restaurar-Ambiente $salvo }
    Info ('Executavel usado: ' + $Exe)
    Info ('Processo iniciado (PID inicial {0}); aguardando a porta {1} ...' -f $proc.Id, $Porta)
    $esc = @(Aguardar-Porta -Porta $Porta -Processo $proc -TimeoutSeg 60)
    if ($esc.Count -eq 0) {
        Info 'A porta nao abriu. Final do log de erro:'
        Mostrar-FimDoLog $logErr
        Mostrar-FimDoLog $logOut
        Falhar ('{0} nao subiu na porta {1} (logs em {2}).' -f $Alvo, $Porta, $logs)
    }
    $dono = $esc[0]
    Gravar-Estado $Chave $dono.Pid $Raiz
    if ($Alvo -eq 'backend') {
        $codigo = Get-Http ($UrlBase + '/health')
        if ($codigo -ne 200) { Falhar ('Backend escutando, mas GET /health retornou ' + $codigo + '.') }
        $pronto = Get-Http ($UrlBase + '/ready')
        Info ('Backend no ar: PID {0}, /health 200, /ready {1}.' -f $dono.Pid, $pronto)
    }
    else {
        $codigo = Get-Http ($UrlBase + '/')
        if ($codigo -ne 200) { Falhar ('Frontend escutando, mas GET / retornou ' + $codigo + '.') }
        Info ('Frontend no ar: PID {0}, GET / 200.' -f $dono.Pid)
    }
    Info ('Logs: {0} | {1}' -f $logOut, $logErr)
}

# ---------------------------------------------------------------------------
# Shadow (S5): funcoes puras e pre-voo
# ---------------------------------------------------------------------------
function Normalizar-DigestModelo {
    # Funcao pura: 64 hex (aceita prefixo sha256:) em minusculas, ou '' se invalido.
    param([string]$Digest)
    if ([string]::IsNullOrWhiteSpace($Digest)) { return '' }
    $d = $Digest.Trim().ToLowerInvariant()
    if ($d.StartsWith('sha256:')) { $d = $d.Substring(7) }
    if ($d -cnotmatch '^[0-9a-f]{64}\z') { return '' }
    return $d
}

function Test-MemoriaLivreSuficiente {
    # Funcao pura: memoria fisica livre (KB, como em FreePhysicalMemory) >= minimo (padrao 14 GiB)?
    param([double]$LivreKb, [double]$MinimoKb = $script:ShadowMemoriaMinimaKb)
    return ($LivreKb -ge $MinimoKb)
}

function Format-GiB {
    param([double]$Kb)
    return ('{0:N2} GiB' -f ($Kb / 1048576))
}

function Avaliar-TagsOllama {
    # Funcao pura sobre o corpo JSON de /api/tags. Devolve @{ Codigo; Motivo; DigestEncontrado }.
    # Codigo: 0 ok, 7 modelo ausente/resposta ilegivel, 8 digest diferente do aprovado.
    param([string]$CorpoJson, [string]$Modelo, [string]$DigestEsperado)
    $r = @{ Codigo = 0; Motivo = ''; DigestEncontrado = '' }
    $o = $null
    try { $o = $CorpoJson | ConvertFrom-Json } catch { }
    if ($null -eq $o -or $null -eq $o.models) {
        $r.Codigo = $script:CodigoShadowOllama
        $r.Motivo = 'resposta de /api/tags ilegivel.'
        return $r
    }
    $m = @($o.models | Where-Object { [string]$_.name -eq $Modelo })
    if ($m.Count -eq 0) {
        $r.Codigo = $script:CodigoShadowOllama
        $r.Motivo = ('o modelo {0} nao esta presente no Ollama.' -f $Modelo)
        return $r
    }
    $achado = Normalizar-DigestModelo ([string]$m[0].digest)
    $r.DigestEncontrado = $achado
    if ($achado -eq '' -or $achado -ne $DigestEsperado) {
        $r.Codigo = $script:CodigoShadowDigest
        $r.Motivo = ('o digest de {0} no Ollama ({1}) difere do aprovado em -DigestModelo ({2}).' -f $Modelo, $(if ($achado) { $achado } else { '(ilegivel)' }), $DigestEsperado)
        return $r
    }
    return $r
}

function Obter-MemoriaLivreKb {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    return [double]$os.FreePhysicalMemory
}

function Get-JsonOllama {
    # GET somente leitura em loopback (URL fixa; nenhuma inferencia). Devolve o corpo ou $null.
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return [string]$r.Content
    }
    catch { return $null }
}

function Montar-AmbienteShadow {
    # Funcao pura: variaveis do shadow para o filho (nenhum segredo). Valida o digest e o orcamento.
    param([string]$Digest, [int]$OrcamentoId)
    $d = Normalizar-DigestModelo $Digest
    if ($d -eq '') { Falhar '-DigestModelo deve ter 64 caracteres hexadecimais (digest aprovado de qwen3:4b).' }
    if ($OrcamentoId -lt 1) { Falhar '-OrcamentoShadow deve ser o id (inteiro positivo) do orcamento provisionado.' }
    if ([string]$script:ConfigPadrao['APP_ENV'] -eq 'production') { Falhar 'O shadow e recusado com APP_ENV=production.' }
    $amb = @{}
    foreach ($k in $script:ShadowFixo.Keys) { $amb[$k] = $script:ShadowFixo[$k] }
    $amb['OVERDUE_SHADOW_DEPLOYMENT_BUDGET_ID'] = [string]$OrcamentoId
    $amb['OVERDUE_SHADOW_MODEL_DIGEST'] = $d
    return $amb
}

function Verificar-PreVooShadow {
    # Memoria e Ollama (so leitura). Em falha, define o codigo de saida proprio e lanca.
    param([string]$Digest)
    if ($script:Ctx.SemSondagem) {
        Info '(pre-voo do shadow nao verificado: -SemSondagem)'
        return
    }
    $livre = Obter-MemoriaLivreKb
    Info ('Memoria fisica livre: {0} (minimo {1})' -f (Format-GiB $livre), (Format-GiB $script:ShadowMemoriaMinimaKb))
    if (-not (Test-MemoriaLivreSuficiente $livre)) {
        $script:Codigo = $script:CodigoShadowMemoria
        Falhar ('Memoria fisica livre insuficiente ({0}); o shadow exige pelo menos {1}. Nada foi iniciado.' -f (Format-GiB $livre), (Format-GiB $script:ShadowMemoriaMinimaKb))
    }
    $v = Get-JsonOllama $script:OllamaUrlVersao
    $t = Get-JsonOllama $script:OllamaUrlTags
    if ($null -eq $v -or $null -eq $t) {
        $script:Codigo = $script:CodigoShadowOllama
        Falhar 'O Ollama nao respondeu em 127.0.0.1:11434 (/api/version e /api/tags). Nada foi iniciado.'
    }
    $versao = ''
    try { $versao = [string](($v | ConvertFrom-Json).version) } catch { }
    $av = Avaliar-TagsOllama $t $script:ShadowModeloId $Digest
    if ($av.Codigo -ne 0) {
        $script:Codigo = $av.Codigo
        Falhar ('Pre-voo do shadow recusado: ' + $av.Motivo + ' Nada foi iniciado.')
    }
    Info ('Ollama versao        : ' + $(if ($versao) { $versao } else { '(nao informada)' }))
    Info ('Modelo {0} digest : {1} (igual ao aprovado)' -f $script:ShadowModeloId, $av.DigestEncontrado)
}

function Cmd-SubirBackend {
    $raiz = Resolver-Pasta
    $py = Resolver-Python $raiz
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre $script:NomesSegredos
    Exigir-PortaLivre $script:PortaBackend
    $args3 = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$script:PortaBackend)
    $extra = @{}
    if ($script:Ctx.Shadow) {
        $extra = Montar-AmbienteShadow $script:Ctx.DigestModelo $script:Ctx.OrcamentoShadow
        Info '== Shadow do agente de vencidos (rodada S5; dados sinteticos; sem producao) =='
        Info ('APP_ENV             : ' + $script:ConfigPadrao['APP_ENV'])
        Info ('Variaveis do shadow : ' + ((@($extra.Keys | Sort-Object) | ForEach-Object { '{0}={1}' -f $_, $extra[$_] }) -join '; '))
        Verificar-PreVooShadow (Normalizar-DigestModelo $script:Ctx.DigestModelo)
        if ((-not $script:Ctx.Simular) -and (-not $script:Ctx.Confirmar)) {
            Info 'Nada foi iniciado. Repita com -Confirmar para subir o backend com o shadow (ou -Simular).'
            return
        }
    }
    Iniciar-Servico -Alvo 'backend' -Porta $script:PortaBackend -Exe $py -Args3 $args3 -Dir (Join-Path $raiz 'backend') -Raiz $raiz -AmbienteExtra $extra
}

function Rodar-ProvisionamentoShadow {
    # Provisiona (orcamento ou conta sintetica). Modulo e arquivo sao literais internos; nenhum parametro
    # do usuario escolhe executavel, modulo ou SQL. O script Python recusa banco diferente do esperado (4).
    param([string]$Modulo, [string]$Arquivo, [string]$Efeito, [switch]$ExigirBackendParado)
    $raiz = Resolver-Pasta
    $arqPy = Join-Path $raiz ('backend\scripts\' + $Arquivo)
    if (-not (Test-Path -LiteralPath $arqPy -PathType Leaf)) {
        Falhar ('Nao existe backend\scripts\' + $Arquivo + ' no checkout informado.')
    }
    $py = Resolver-Python $raiz
    if ($script:Ctx.Simular -or (-not $script:Ctx.Confirmar)) {
        if ($script:Ctx.Simular) { Info 'SIMULACAO: nada sera feito.' }
        else { Info 'Nada foi gravado. Repita com -Confirmar para executar.' }
        Info ('Faria, em {0}\backend: {1} -m {2}' -f $raiz, $py, $Modulo)
        Info ('Efeito: ' + $Efeito)
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    if ($ExigirBackendParado -and (-not $script:Ctx.SemSondagem) -and (@(Get-Escutando $script:PortaBackend).Count -gt 0)) {
        $script:Codigo = $script:CodigoBackendAtivo
        Falhar ('A porta {0} esta ocupada: o backend esta no ar. A conta sintetica so pode ser criada com o backend PARADO; com o backend ligado sem -Shadow, a deteccao (a cada ~60 s) criaria o pedido de aprovacao sem o shadow observar. Rode parar-backend -Confirmar e repita. Nada foi gravado.' -f $script:PortaBackend)
    }
    $amb = Montar-Ambiente 'backend'
    $res = Invoke-Filho -Exe $py -Argumentos @('-m', $Modulo) -DiretorioTrabalho (Join-Path $raiz 'backend') -Ambiente $amb
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) {
        $script:Codigo = $res.Codigo
        Falhar ('{0} terminou com codigo {1} (3 = existente divergente, 4 = banco diferente do esperado).' -f $Modulo, $res.Codigo)
    }
}

function Cmd-ProvisionarOrcamentoShadow {
    Rodar-ProvisionamentoShadow -Modulo 'scripts.create_shadow_budget' -Arquivo 'create_shadow_budget.py' -Efeito 'cria UM orcamento deployment_period (7 dias, scope_ref s5-shadow-overdue-synthetic-think; o orcamento antigo s5-shadow-overdue-synthetic, de prompt v1 sem raciocinio, NAO e alterado) com limit_tokens = reserva de pior caso de UMA chamada local-qwen3-4b com raciocinio (2048 tokens de saida) (preco zero; o limite de tokens e o que garante uma chamada) e limit_micro = 1; idempotente; recusa se o banco nao for auneron_neon_synthetic.'
}

function Cmd-ProvisionarContaVencidaSintetica {
    Rodar-ProvisionamentoShadow -Modulo 'scripts.create_shadow_synthetic_account' -Arquivo 'create_shadow_synthetic_account.py' -Efeito 'cria UMA conta (cliente SINTETICO S5 SHADOW, s5.shadow@example.com, valor 10.00, aberto, vencimento = hoje no BUSINESS_TIMEZONE - 10 dias); idempotente pelo email; nada mais e criado; recusa se o banco nao for auneron_neon_synthetic e (codigo 9) se a porta 8000 estiver ocupada: o backend deve estar PARADO.' -ExigirBackendParado
}

function Cmd-RedefinirSenha {
    # Recuperacao manual de senha. Modulo e arquivo sao literais fixos; o e-mail e validado por regex
    # (nunca comeca com '-') e vai como argumento. Nenhum segredo do cofre vai por argumento; o filho
    # recebe o ambiente do backend. A senha temporaria sai UMA vez na saida do filho.
    $email = ([string]$script:Ctx.Email).Trim()
    if (-not $email) { Falhar 'redefinir-senha exige -Email <e-mail do usuario>.' }
    if ($email -notmatch '^[A-Za-z0-9._%+][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' -or $email.Length -gt 254) {
        $script:Codigo = 2
        Falhar '-Email invalido.'
    }
    $raiz = Resolver-Pasta
    $arqPy = Join-Path $raiz 'backend\scripts\reset_user_password.py'
    if (-not (Test-Path -LiteralPath $arqPy -PathType Leaf)) {
        Falhar 'Nao existe backend\scripts\reset_user_password.py no checkout informado.'
    }
    $py = Resolver-Python $raiz
    $argsFilho = @('-m', 'scripts.reset_user_password', $email)
    if ($script:Ctx.PermitirUltimoAdmin) { $argsFilho += '--permitir-ultimo-admin' }
    if ($script:Ctx.Simular -or (-not $script:Ctx.Confirmar)) {
        if ($script:Ctx.Simular) { Info 'SIMULACAO: nada sera feito.' }
        else { Info 'Nada foi gravado. Repita com -Confirmar para executar.' }
        Info ('Faria, em {0}\backend: {1} {2}' -f $raiz, $py, ($argsFilho -join ' '))
        Info ('Efeito: gera senha temporaria (30 min), exige troca no primeiro acesso e revoga TODAS as sessoes de ' + $email + '; recusa banco diferente de auneron_neon_synthetic, usuario inexistente, conta system e o unico administrador ativo (sem -PermitirUltimoAdmin).')
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    $amb = Montar-Ambiente 'backend'
    $res = Invoke-Filho -Exe $py -Argumentos $argsFilho -DiretorioTrabalho (Join-Path $raiz 'backend') -Ambiente $amb
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) {
        $script:Codigo = $res.Codigo
        Falhar ('scripts.reset_user_password terminou com codigo {0} (2 = e-mail invalido, 3 = usuario inexistente, 4 = banco diferente do esperado, 5 = conta system, 6 = unico administrador ativo). Nada foi gravado.' -f $res.Codigo)
    }
}

function Cmd-SubirFrontendImportacao {
    Subir-Frontend -Porta $script:PortaFrontendImportacao -UrlBase $script:UrlFrontendImportacao -Chave 'frontend-importacao' `
        -PortaBackendAlvo $script:PortaBackendImportacao -AmbienteExtra @{ 'AUNERON_BACKEND_URL' = $script:UrlBackendImportacao }
}

function Cmd-SubirFrontend {
    Subir-Frontend -Porta $script:PortaFrontend -UrlBase $script:UrlFrontend -Chave 'frontend' -PortaBackendAlvo $script:PortaBackend
}

function Subir-Frontend {
    param([int]$Porta, [string]$UrlBase, [string]$Chave, [int]$PortaBackendAlvo, [hashtable]$AmbienteExtra = @{})
    $raiz = Resolver-Pasta
    $vite = Join-Path $raiz 'frontend\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite -PathType Leaf)) {
        Falhar ("node_modules ausente em {0}\frontend. Rode la: npm ci (o script nao instala nada)." -f $raiz)
    }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $node) { Falhar 'node.exe nao encontrado no PATH.' }
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('API_KEY')
    Exigir-PortaLivre $Porta
    if (-not $script:Ctx.SemSondagem) {
        if (@(Get-Escutando $PortaBackendAlvo).Count -eq 0) { Info ('AVISO: nada escuta na porta {0}; o frontend subira, mas /api falhara ate o backend subir.' -f $PortaBackendAlvo) }
    }
    $args3 = @($vite, '--host', '127.0.0.1', '--port', [string]$Porta, '--strictPort')
    Iniciar-Servico -Alvo 'frontend' -Porta $Porta -Exe $node.Source -Args3 $args3 -Dir (Join-Path $raiz 'frontend') -Raiz $raiz `
        -AmbienteExtra $AmbienteExtra -Chave $Chave -UrlBase $UrlBase
}

function Cmd-Parar {
    param([string]$Alvo, [int]$Porta, [string]$Chave = '')
    if (-not $Chave) { $Chave = $Alvo }
    if ($script:Ctx.SemSondagem) { Falhar '-SemSondagem nao se aplica a parar-*.' }
    $esc = @(Get-Escutando $Porta)
    if ($esc.Count -eq 0) { Info ('Nada escutando na porta {0}; nada a parar.' -f $Porta); return }
    $alvos = @($esc | Where-Object { Test-ProcessoAlvo -Nome $_.Nome -LinhaComando $_.LinhaComando -Alvo $Alvo })
    if ($alvos.Count -ne $esc.Count) {
        foreach ($i in $esc) { Info ('  escutando: ' + (Descrever-Escutando $i)) }
        Falhar ('A porta {0} esta ocupada por processo que NAO e o {1} esperado; nada foi parado.' -f $Porta, $Alvo)
    }
    foreach ($i in $alvos) { Info ('Sera parado: ' + (Descrever-Escutando $i)) }
    if ($script:Ctx.Simular) { Info 'SIMULACAO: nada foi parado.'; return }
    if (-not $script:Ctx.Confirmar) { Info 'Nada foi parado. Repita com -Confirmar para parar.'; return }
    foreach ($i in $alvos) {
        $atual = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + $i.Pid) -ErrorAction SilentlyContinue
        if (-not $atual -or -not (Test-ProcessoAlvo -Nome $atual.Name -LinhaComando $atual.CommandLine -Alvo $Alvo)) {
            Falhar ('O PID {0} mudou desde a verificacao; nada mais foi parado.' -f $i.Pid)
        }
        Stop-Process -Id $i.Pid -Force
    }
    $limite = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $limite -and @(Get-Escutando $Porta).Count -gt 0) { Start-Sleep -Milliseconds 500 }
    if (@(Get-Escutando $Porta).Count -gt 0) { Falhar ('A porta {0} continua ocupada apos parar.' -f $Porta) }
    Gravar-Estado $Chave $null ''
    Info ('{0} parado; porta {1} livre.' -f $Alvo, $Porta)
}

# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
function Principal {
    $ErrorActionPreference = 'Stop'
    $script:Codigo = 0
    try {
        $script:Ctx.Base = $Base
        $script:Ctx.Cofre = $Cofre
        $script:Ctx.Pasta = $Pasta
        $script:Ctx.Simular = [bool]$Simular
        $script:Ctx.Confirmar = [bool]$Confirmar
        $script:Ctx.SemSondagem = [bool]$SemSondagem
        $script:Ctx.Destino = $Destino
        $script:Ctx.ConfirmarBackup = $ConfirmarBackup
        $script:Ctx.Relatorio = $Relatorio
        $script:Ctx.Conta = $Conta
        $script:Ctx.Shadow = [bool]$Shadow
        $script:Ctx.DigestModelo = $DigestModelo
        $script:Ctx.OrcamentoShadow = $OrcamentoShadow
        $script:Ctx.Email = $Email
        $script:Ctx.PermitirUltimoAdmin = [bool]$PermitirUltimoAdmin
        if (($Relatorio -or $Conta) -and ($Comando -ne 'consultar')) {
            Falhar '-Relatorio e -Conta so valem com consultar.'
        }
        if (($Shadow -or $DigestModelo -or $OrcamentoShadow) -and ($Comando -ne 'subir-backend')) {
            Falhar '-Shadow, -DigestModelo e -OrcamentoShadow so valem com subir-backend.'
        }
        if (($DigestModelo -or $OrcamentoShadow) -and (-not $Shadow)) {
            Falhar '-DigestModelo e -OrcamentoShadow so valem com -Shadow.'
        }
        if ($Shadow -and ((-not $DigestModelo) -or (-not $OrcamentoShadow))) {
            Falhar '-Shadow exige -DigestModelo <64 hex> e -OrcamentoShadow <id>.'
        }
        if (($Email -or $PermitirUltimoAdmin) -and ($Comando -ne 'redefinir-senha')) {
            Falhar '-Email e -PermitirUltimoAdmin so valem com redefinir-senha.'
        }
        if ($SemSondagem -and ($Comando -ne 'status') -and (-not $Simular)) {
            Falhar '-SemSondagem so vale com status ou -Simular.'
        }
        switch ($Comando) {
            'configurar'     { Cmd-Configurar }
            'status'         { Cmd-Status }
            'versao-banco'   { Cmd-VersaoBanco }
            'pendentes'      { Cmd-Pendentes }
            'backup'         { Cmd-Backup }
            'ensaiar'        { Cmd-Ensaiar }
            'migrar'         { Cmd-Migrar }
            'registrar-acao-pagamento' { Cmd-RegistrarAcaoPagamento }
            'registrar-acao-atraso'    { Cmd-RegistrarAcaoAtraso }
            'detectar-vencidos' { Cmd-DetectarVencidos }
            'consultar'      { Cmd-Consultar }
            'provisionar-deteccao-vencidos' { Cmd-ProvisionarDeteccaoVencidos }
            'vincular-deteccao-vencidos' { Cmd-VincularDeteccaoVencidos }
            'desvincular-deteccao-vencidos' { Cmd-DesvincularDeteccaoVencidos }
            'provisionar-orcamento-shadow' { Cmd-ProvisionarOrcamentoShadow }
            'provisionar-conta-vencida-sintetica' { Cmd-ProvisionarContaVencidaSintetica }
            'redefinir-senha' { Cmd-RedefinirSenha }
            'subir-backend'  { Cmd-SubirBackend }
            'parar-backend'  { Cmd-Parar 'backend' $script:PortaBackend }
            'subir-frontend' { Cmd-SubirFrontend }
            'parar-frontend' { Cmd-Parar 'frontend' $script:PortaFrontend }
            'preparar-instancia-importacao' { Cmd-PrepararInstanciaImportacao }
            'subir-backend-importacao'  { Cmd-SubirBackendImportacao }
            'parar-backend-importacao'  { Cmd-Parar 'backend' $script:PortaBackendImportacao 'backend-importacao' }
            'subir-frontend-importacao' { Cmd-SubirFrontendImportacao }
            'parar-frontend-importacao' { Cmd-Parar 'frontend' $script:PortaFrontendImportacao 'frontend-importacao' }
            'remover-instancia-importacao' { Cmd-RemoverInstanciaImportacao }
        }
    }
    catch {
        if ($script:Codigo -eq 0) { $script:Codigo = 1 }
        $msg = Mascarar-Texto ([string]$_.Exception.Message)
        [Console]::Error.WriteLine('ERRO: ' + $msg)
    }
}

if ($PSCmdlet.ParameterSetName -eq 'Funcoes') { return }

Principal
exit $script:Codigo
