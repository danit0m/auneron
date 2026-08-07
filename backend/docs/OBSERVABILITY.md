# Observabilidade do backend

## Objetivo

O backend do Auneron registra eventos estruturados em JSON usando
somente a biblioteca padrão `logging` do Python.

O foco deste estágio é permitir rastreamento de requisições sem
registrar credenciais ou conteúdo sensível.

## Request ID

Toda requisição HTTP recebe o cabeçalho:

`X-Request-ID`

Quando o cliente envia um identificador válido, ele é preservado.
Quando não envia, ou envia um valor inválido, o backend gera um
UUID.

O valor aceito possui no máximo 128 caracteres e somente letras,
números, ponto, sublinhado, dois-pontos e hífen.

O mesmo `request_id` aparece nos logs da requisição e nos logs de
autenticação produzidos durante seu processamento.

## Log HTTP

Cada requisição concluída registra:

- `event`
- `request_id`
- `method`
- `path`
- `status_code`
- `duration_ms`
- `environment`

A query string, corpo da requisição e cabeçalhos não são
registrados.

Respostas com status 400 ou superior são registradas em nível
`WARNING`. Exceções não tratadas são registradas em nível `ERROR`,
com o tipo da exceção, sem serializar sua mensagem.

## Segurança dos logs

O formatador JSON mascara automaticamente campos cujo nome indique
dados sensíveis, incluindo:

- API keys
- Authorization
- cookies
- URLs de banco
- senhas
- secrets
- tokens

O valor desses campos é substituído por `[REDACTED]`.

A autenticação registra somente o resultado da tentativa
(`missing`, `invalid` ou `not_configured`) e o `request_id`.
A chave recebida nunca é registrada.

## Banco

Falhas do health check do banco registram apenas o tipo da exceção.
A `DATABASE_URL` e a mensagem bruta da exceção não são colocadas
no log.

## Configuração

O nível mínimo é controlado por:

`LOG_LEVEL=INFO`

Valores aceitos:

- `DEBUG`
- `INFO`
- `WARNING`
- `ERROR`
- `CRITICAL`

## Escopo deste commit

A auditoria encontrou vários `print()` nos agentes e no
orquestrador. Eles são saída legada de diagnóstico e não foram
convertidos neste commit para evitar mudança simultânea no
comportamento dos agentes.

A migração dessas saídas para o logging estruturado deve ser feita
em uma etapa específica, com testes próprios.
