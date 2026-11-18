# A6 - Importacao com conferencia: instancia de teste separada

Objetivo: provar, numa instancia SEPARADA, que a importacao mostra uma previa sem gravar, que a
confirmacao grava, que os totais conferem e que reenviar o mesmo arquivo nao duplica.

Decisao de Daniel: a prova roda em uma COPIA da homologacao, num banco novo `auneron_test`. O banco da
homologacao (`auneron_neon_synthetic`), o backend da 8000 e o frontend da 5180 NAO sao tocados.
A trava de seguranca da importacao nao muda: a confirmacao so libera com `APP_ENV=test`,
`IMPORT_CONFIRMATION_TEST_ENABLED=true`, banco chamado `auneron_test` e host do banco local
(`localhost`, `127.0.0.1` ou `::1`). Se o servidor de banco da homologacao nao for local, o
`preparar-instancia-importacao` recusa (nada e criado).

Todos os comandos: `backend\scripts\homologacao.ps1 <comando> -Pasta <checkout da homologacao>`.
O cofre (DATABASE_URL, API_KEY) ja deve estar configurado. Nenhum comando imprime a URL do banco.

## Sequencia

1. Backup recente (menos de 24 h) da homologacao:
   `homologacao.ps1 backup` (anote o caminho do `.dump`).
2. Criar e restaurar a instancia (cria `auneron_test` no mesmo servidor, restaura a copia, roda
   `alembic upgrade head`; recusa se `auneron_test` ja existir):
   `homologacao.ps1 preparar-instancia-importacao -Pasta <hml> -ConfirmarBackup <dump> -Simular`
   `homologacao.ps1 preparar-instancia-importacao -Pasta <hml> -ConfirmarBackup <dump> -Confirmar`
3. Subir o segundo backend (porta 8001; `APP_ENV=test`, chave de confirmacao ligada,
   `MAINTENANCE_ENABLED=false`: nenhum loop de manutencao nem deteccao roda nessa instancia):
   `homologacao.ps1 subir-backend-importacao -Pasta <hml> -Confirmar`
4. Subir o segundo frontend (porta 5181, apontando para a 8001):
   `homologacao.ps1 subir-frontend-importacao -Pasta <hml> -Confirmar`
5. Teste na tela: abrir `http://127.0.0.1:5181`, entrar com um usuario que exista na copia
   (a copia traz os usuarios da homologacao) e abrir a tela de Importacao (Upload). Usar o arquivo
   `backend\docs\operations\a6_exemplo.csv` (sintetico: 5 linhas, total 4.460,65). Ver criterios abaixo.
   Dica: a sessao e por cookie do host `127.0.0.1` (sem separar por porta); se a homologacao estiver
   aberta no mesmo navegador, use outra janela anonima ou outro perfil para nao misturar sessoes.
6. Parar:
   `homologacao.ps1 parar-frontend-importacao -Confirmar`
   `homologacao.ps1 parar-backend-importacao -Confirmar`
7. Remover a instancia (EXCLUI o banco `auneron_test`; e uma delecao: so com aprovacao de Daniel;
   exige a 8001 parada; nunca toca outro banco):
   `homologacao.ps1 remover-instancia-importacao -Pasta <hml> -Confirmar`

Para repetir a prova do zero: execute 6 e 7 e depois recomece no passo 1 (ou 2, com dump recente).

## Como o frontend aponta para a 8001

O `frontend\vite.config.ts` ja le a variavel `AUNERON_BACKEND_URL` (padrao `http://127.0.0.1:8000`) para
o proxy de `/api`, e `AUNERON_API_KEY` para o cabecalho. O comando `subir-frontend-importacao` apenas
define `AUNERON_BACKEND_URL=http://127.0.0.1:8001` no ambiente do processo do Vite (porta 5181,
`--strictPort`); a chave vem do cofre. Nenhum arquivo do frontend foi alterado.

## Criterios de aceite da A6

Use `a6_exemplo.csv` (5 linhas; total 4.460,65). Registre o resultado de cada item.

1. Previa sem gravacao: ao escolher o arquivo e pedir a previa, a tela mostra 5 linhas validas, 0
   invalidas e total 4.460,65; a lista de contas e o contador de contas do banco `auneron_test`
   continuam como antes (nenhuma conta `A6 TESTE ...` existe). Repetir a previa varias vezes nao grava nada.
2. Confirmacao grava: com a confirmacao disponivel na tela (capabilities = true), marcar a conferencia e
   confirmar; a resposta e o recibo (HTTP 201, `replayed=false`) com `record_count=5`. As 5 contas
   `A6 TESTE ...` aparecem na lista de contas, com status `aberto`.
3. Totais conferem: o total do recibo (4.460,65), o numero de contas (5) e o total mostrado na previa
   sao iguais, e a soma dos valores das 5 contas na lista bate com o total do recibo.
4. Sem duplicar: (a) repetir a confirmacao com o mesmo arquivo e a mesma intencao (mesma chave) devolve o
   mesmo recibo como repeticao (HTTP 200, `replayed=true`) e a contagem continua 5; (b) reenviar o mesmo
   arquivo como nova tentativa (nova chave) e recusado com `content_already_imported` (409) e nada novo e
   gravado; o total de contas `A6 TESTE ...` continua 5.
5. Contraprova da trava: com a homologacao normal (porta 8000/5180), a tela de Importacao NAO oferece
   a confirmacao (capabilities = false); so a instancia de teste oferece.

Se algum item falhar, pare, guarde a saida (e os logs em `%LOCALAPPDATA%\Auneron\homologacao\logs\`,
arquivos `backend-importacao-*` e `frontend-importacao-*`), pare a instancia e reporte ao PO sem remover o
banco ate haver orientacao.

## Limites

- Uma instancia por vez (`auneron_test` e fixo). Nao use dado real: o arquivo de exemplo e sintetico e a
  copia vem da homologacao sintetica.
- Os comandos desta instancia usam portas 8001/5181 e nunca `parar-backend`/`parar-frontend` (8000/5180).
- A remocao do banco e irreversivel; a copia pode ser refeita a partir de um backup.
