# Runbook unico da homologacao

Operacao da homologacao local (Windows PowerShell 5.1), conferida contra `backend\scripts\homologacao.ps1`
na `main` em 6991f00. Este documento consolida e aponta para os runbooks detalhados; nao os substitui:

- Shadow do agente de vencidos: `backend\docs\operations\S5_SHADOW_RUNBOOK.md`
- Instancia de teste da importacao: `backend\docs\operations\A6_IMPORTACAO_RUNBOOK.md`
- Arquitetura do modelo local: `backend\docs\intelligence\MODELO_LOCAL_ARQUITETURA.md`
- Ajuda completa e codigos de saida: cabecalho de `backend\scripts\homologacao.ps1`

## 1. Convencoes

- Todos os comandos: `backend\scripts\homologacao.ps1 <comando> -Pasta <checkout>`. A homologacao e
  `auneron_neon_synthetic` (backend na 8000, frontend na 5180). Dados sinteticos apenas.
- Segredos (`DATABASE_URL`, `API_KEY`) ficam no cofre cifrado (DPAPI) fora do repositorio. Quem
  configura e Daniel (`configurar`). Nenhum comando imprime valores do cofre.
- Comandos que mudam algo so agem com `-Confirmar`. `-Simular` (ou omitir `-Confirmar`) mostra o que
  seria feito. Use `-Simular` primeiro.
- Nao existe downgrade de banco neste script (`backend\docs\DEPLOYMENT.md`).
- Captura de saida (PR 78): a saida normal vai para o pipeline de sucesso. Use
  `homologacao.ps1 status | Out-File <pasta-nova>\status.txt -Encoding utf8`, ou `> arquivo`, ou
  `$s = homologacao.ps1 status`. Erros (`ERRO: ...`) vao para o stderr; para te-los no arquivo use `2>&1`.
  O codigo de saida fica em `$LASTEXITCODE`. Cada rodada de evidencia usa pasta NOVA.

## 2. Lista de comandos (ValidateSet real do script)

`configurar`, `status`, `versao-banco`, `pendentes`, `backup`, `ensaiar`, `migrar`,
`registrar-acao-pagamento`, `registrar-acao-atraso`, `consultar`, `provisionar-deteccao-vencidos`,
`vincular-deteccao-vencidos`, `desvincular-deteccao-vencidos`, `provisionar-orcamento-shadow`,
`provisionar-conta-vencida-sintetica`, `redefinir-senha`, `subir-backend`, `parar-backend`,
`subir-frontend`, `parar-frontend`, `preparar-instancia-importacao`, `subir-backend-importacao`,
`parar-backend-importacao`, `subir-frontend-importacao`, `parar-frontend-importacao`,
`remover-instancia-importacao`.

Relatorios de `consultar -Relatorio`: `pagamento` (exige `-Conta <id>`), `catalogo`, `sistema`,
`deteccao`, `shadow`. Todos somente leitura (`BEGIN READ ONLY` ... `ROLLBACK`).

## 3. Subir e parar

| Acao | Comando |
|---|---|
| Subir backend (padrao, `INTELLIGENCE_MODE=off`) | `subir-backend -Pasta <p> -Confirmar` |
| Subir frontend | `subir-frontend -Pasta <p> -Confirmar` (exige `npm ci` ja feito; o script nao instala nada) |
| Parar backend / frontend | `parar-backend -Confirmar` / `parar-frontend -Confirmar` |
| Situacao | `status` (cofre, portas 8000/5180, `/health`, `/ready`, estado gravado) |

`parar-*` so para o processo se ele for o uvicorn/vite esperado; se a porta estiver ocupada por outra
coisa, recusa e nada e parado. Logs: `%LOCALAPPDATA%\Auneron\homologacao\logs\` (`backend-*.out.log`,
`backend-*.err.log`, `frontend-*`).

## 4. Banco: backup, ensaiar, migrar

Ordem obrigatoria antes de qualquer migracao: backup, ensaiar, migrar.

1. `versao-banco -Pasta <p>` e `pendentes -Pasta <p>`: revisao atual e migracoes pendentes.
2. `backup` (dump em `%LOCALAPPDATA%\Auneron\homologacao\backups\hml-<data>.dump`; nunca sobrescreve).
3. `ensaiar -Pasta <p> -ConfirmarBackup <dump>`: valida o dump, restaura num banco descartavel, roda
   `alembic upgrade head` e remove o descartavel. Nao toca a homologacao.
4. `migrar -Pasta <p> -ConfirmarBackup <dump>`: o dump precisa ter menos de 24 h.

Este script NAO tem comando de restauracao da homologacao. Restaurar o banco da homologacao e feito
fora dele e exige aprovacao de Daniel (secao 10).

## 5. Consultar (relatorios)

```
consultar -Relatorio sistema            # banco/porta e usuario de sistema
consultar -Relatorio catalogo           # skills e versoes
consultar -Relatorio deteccao           # skill, binding, autoridade L3, contas vencidas
consultar -Relatorio pagamento -Conta 123
consultar -Relatorio shadow             # orcamentos, chamadas de modelo, contagens do dominio
```

Use `consultar -Relatorio shadow` ANTES e DEPOIS de uma rodada do shadow e guarde as duas saidas.

## 6. Deteccao de vencidos

A deteccao roda DENTRO do backend, sozinha, a cada ~60 s (`overdue_detection_maintenance_loop`).
Nao existe comando para dispara-la: `detectar-vencidos` e `scheduled_detect_overdue.py` foram removidos.
Pre-requisitos (idempotentes), cada um com `-Pasta <p> -Confirmar`:

1. `registrar-acao-atraso` (registra a skill `account.mark_overdue`; `registrar-acao-pagamento` faz o
   mesmo para `account.mark_paid`).
2. `provisionar-deteccao-vencidos` (conta de sistema `sistema.vencimentos@auneron.core`).
3. `vincular-deteccao-vencidos` (vincula o `OverdueDetectionAgent`; recusa se houver autoridade L3
   ativa). `desvincular-deteccao-vencidos` desabilita, nunca apaga.

Confira com `consultar -Relatorio deteccao`.

## 7. Shadow do agente de vencidos (detalhes no S5_SHADOW_RUNBOOK)

Estado: a rodada real COM raciocinio (think=true, 4096) ainda nao foi feita; o orcamento novo
(`scope_ref` `s5-shadow-overdue-synthetic-think-4096`) ainda precisa ser provisionado. Os orcamentos
anteriores nao servem e nao se alteram nem se apagam.

Ordem (a conta so e criada com o backend PARADO, senao a deteccao embutida cria o pedido sem o shadow
observar):

1. `parar-backend -Confirmar` e `status` (8000 livre).
2. `provisionar-orcamento-shadow -Pasta <p> -Confirmar` (anote o id). Exige banco `auneron_neon_synthetic`.
3. `provisionar-conta-vencida-sintetica -Pasta <p> -Confirmar` (recusa com codigo 9 se a 8000 estiver
   ocupada). `consultar -Relatorio shadow` -> guardar como "antes".
4. Pre-voo e ensaio, sem subir nada:
   `subir-backend -Pasta <p> -Shadow -DigestModelo <64 hex> -OrcamentoShadow <id> -Simular`
   O pre-voo exige memoria fisica livre >= 14 GiB (codigo 6), Ollama em loopback respondendo
   (`/api/version`, `/api/tags`; codigo 7) e `qwen3:4b` com digest igual ao informado (codigo 8).
   Digest aprovado: `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`.
5. Ligar: `subir-backend -Pasta <p> -Shadow -DigestModelo <64 hex> -OrcamentoShadow <id> -Confirmar`.
   Variaveis so no ambiente do filho: `INTELLIGENCE_MODE=shadow`, `OVERDUE_SHADOW_ENABLED=true`,
   `OVERDUE_SHADOW_DATA_CLASSIFICATION=synthetic`, `OVERDUE_SHADOW_MAX_ACCOUNTS_PER_RUN=1`,
   `OVERDUE_SHADOW_DEPLOYMENT_BUDGET_ID`, `OVERDUE_SHADOW_MODEL_ID=qwen3:4b`,
   `OVERDUE_SHADOW_MODEL_DIGEST`, `INTELLIGENCE_CALL_TIMEOUT_SECONDS=300`, `INTELLIGENCE_MAX_ATTEMPTS=1`.
   Recusa `APP_ENV=production`. Esta e uma inferencia real: exige aprovacao de Daniel (secao 10).
6. Aguardar ate 5 min por `overdue.shadow.observed` em `backend-*.out.log`. Nada em 5 min: parar,
   registrar e reportar; nao repetir.
7. `consultar -Relatorio shadow` ("depois"), conferir criterios de aceite do S5_SHADOW_RUNBOOK.
8. Desligar: `parar-backend -Confirmar`, depois `subir-backend -Pasta <p> -Confirmar` (sem `-Shadow`;
   `INTELLIGENCE_MODE=off` e o padrao).

Se a chamada ficar `unknown`: NAO repetir; parar o backend, guardar o relatorio e reportar ao PO.

## 8. Instancia de importacao A6 (detalhes no A6_IMPORTACAO_RUNBOOK)

Copia separada da homologacao, banco `auneron_test`, portas 8001/5181; nunca toca 8000/5180 nem o banco
da homologacao.

1. `backup` (dump com menos de 24 h).
2. `preparar-instancia-importacao -Pasta <p> -ConfirmarBackup <dump> -Simular`, depois `-Confirmar`.
3. `subir-backend-importacao -Pasta <p> -Confirmar` (`APP_ENV=test`, `IMPORT_CONFIRMATION_TEST_ENABLED=true`,
   `MAINTENANCE_ENABLED=false`, `INTELLIGENCE_MODE=off`).
4. `subir-frontend-importacao -Pasta <p> -Confirmar`; testar em `http://127.0.0.1:5181` com
   `backend\docs\operations\a6_exemplo.csv`.
5. `parar-frontend-importacao -Confirmar`, `parar-backend-importacao -Confirmar`.
6. `remover-instancia-importacao -Pasta <p> -Confirmar` EXCLUI o banco `auneron_test`: delecao,
   so com aprovacao de Daniel.

## 9. Redefinir senha (B1)

```
redefinir-senha -Pasta <p> -Email pessoa@empresa.com -ArquivoSenha C:\protegido\senha.txt -Simular
redefinir-senha -Pasta <p> -Email pessoa@empresa.com -ArquivoSenha C:\protegido\senha.txt -Confirmar
```

- `-ArquivoSenha` e obrigatorio: arquivo NOVO, em pasta existente. A senha temporaria nunca vai ao
  console nem a saida capturavel; e gravada so nesse arquivo, com acesso restrito ao usuario atual.
- A senha vence em 30 min, exige troca no primeiro acesso e revoga todas as sessoes do usuario.
- Entregue o arquivo por canal seguro e apague-o. O backend pode estar no ar.
- Recusas: 2 uso/e-mail invalido, 3 usuario inexistente, 4 banco errado, 5 conta `system`,
  6 unico administrador ativo (so com `-PermitirUltimoAdmin`).

## 10. O que exige aprovacao de Daniel

- Qualquer delecao: `remover-instancia-importacao` (exclui `auneron_test`) e apagar arquivos de evidencia,
  dumps ou orcamentos.
- Restauracao do banco da homologacao (nao existe comando no script; fora dele).
- Inferencia real: ligar o shadow (`subir-backend -Shadow ... -Confirmar`) chama o modelo local.
- L3 autonomo: `POLICY_MARK_OVERDUE_TRIGGER_ENABLED` e `false` por padrao; ligar exige decisao e
  autorizacao especificas dele. Deploy sozinho nao autoriza autonomia.
- Config do cofre (`configurar`) e ato do diretor.
- Fora de escopo deste runbook: o ambiente real na Vultr (publicacao, DNS, servidor).

## 11. Depois de reiniciar o PC

1. Abrir o Docker Desktop e esperar ficar pronto.
2. Subir o banco da homologacao: `docker start auneron-neon-synthetic-db`.
3. Conferir: `homologacao.ps1 status` e `versao-banco -Pasta <p>` (banco responde; `/ready` 200 depois
   do backend).
4. `subir-backend -Pasta <p> -Confirmar` e `subir-frontend -Pasta <p> -Confirmar`.
5. Ollama (so se for rodar o shadow): iniciar o Ollama e conferir `http://127.0.0.1:11434/api/version`
   (esperado 0.40.16) e que `qwen3:4b` aparece em `/api/tags` com o digest aprovado. O pre-voo do
   `subir-backend -Shadow -Simular` confere isso.
6. Instancia A6, se estava em uso, nao volta sozinha: repita a secao 8 a partir do passo 3 (o banco
   `auneron_test` persiste no mesmo servidor, a menos que tenha sido removido).
