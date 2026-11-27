# Alternativas avaliadas para o servidor HTTP/mTLS (revisão 2)

Contexto: o parecer do consultor **não aprovou** o servidor HTTP próprio (revisão 1). Requisito central mantido:
**mTLS (CA própria + impressão SHA-256 fixa) decidido por conexão, antes de qualquer parsing HTTP**, escutando só em 127.0.0.1.

## Escolhida: uvicorn 0.54.0 + h11 0.16.0, com protocolo derivado
- `GatewayProtocol` estende `uvicorn.protocols.http.h11_impl.H11Protocol`. `connection_made` (o asyncio só o chama **depois do handshake TLS**)
  confere `ssl_object.getpeercert(binary_form=True)` contra a lista fixa. Recusado: `transport.abort()` **sem `super()`**, sem estado HTTP, sem ler byte.
  `data_received`/`eof_received`/`pause_writing`/`resume_writing` ignoram tudo se não autenticado.
- Laço de `accept` próprio sobre socket exclusivo (`SO_EXCLUSIVEADDRUSE`, sem reuso): o limite de **4 conexões vale antes do handshake**;
  `loop.connect_accepted_socket(ssl=ctx)` com prazo de handshake. Contexto SSL próprio (`CERT_REQUIRED`, `X509_STRICT`, TLS 1.3).
- Parser HTTP: **h11** (consolidado, sans-IO, amplamente usado). O parser próprio da revisão 1 foi removido por completo (`httpwire.py` não existe mais).
- Aplicação: ASGI puro com 2 rotas, **sem FastAPI/Starlette**. Justificativa: (a) o roteamento do Starlette decodifica o caminho e, por padrão,
  responde 307 a `/rota/` (`redirect_slashes`) — o requisito é "sem redirecionar" e "caminhos codificados = 404"; (b) FastAPI traria OpenAPI/docs/injeção
  de dependências que seriam desligados; a validação já é feita por pydantic estrito; (c) menos dependências com hash (sem fastapi, starlette, anyio, idna, sniffio).
  A rota é casada contra `scope["raw_path"]` exato, sem query.
- Desligados: `server_header`, `date_header`, `access_log`, `proxy_headers`, `lifespan`, WebSocket/upgrade, docs. Ligados: `limit_concurrency=16`,
  `h11_max_incomplete_event_size=16 KiB`, `timeout_keep_alive`. Prazo de **cabeçalho** (o uvicorn não tem) e **ocioso inicial** implementados no protocolo; prazo de corpo no app
  (corpo lido em stream, limite de 32 KiB aplicado durante a leitura).
- Cliente do Ollama: `http.client` em thread dedicada (sem proxy, sem redirect), peer loopback conferido, leitura limitada a `max_bytes+1`, cancelamento real
  (fechar o socket interrompe a leitura bloqueada e o Ollama vê a desconexão).

### Acoplamento à API interna do uvicorn (risco aceito e controlado)
Dependemos de: construtor `H11Protocol(config, server_state, app_state, _loop)`, métodos `connection_made/lost`, `data_received`, `eof_received`, `pause/resume_writing`,
atributos `transport/cycle/scope/conn/flow/loop`, `ServerState`, e `Config.load()`. Mitigação: versões **fixadas com hash** (`uvicorn==0.54.0`, `h11==0.16.0`) e
`tests/test_uvicorn_interface.py`, que **falha** se a versão, as assinaturas ou os atributos mudarem (e se `connection_made` deixar de conferir identidade antes de `super()`).
Atualizar o uvicorn exige reexecutar esse teste e a suíte de TLS em memória.

## Descartadas / não escolhidas
| Opção | Por quê não |
|---|---|
| **hypercorn** | Suporta h11/h2/h3; mais superfície (HTTP/2, QUIC) sem necessidade. A identidade chegaria ao app só via extensão ASGI **depois** do parsing HTTP; para impedir isso teríamos de derivar o mesmo ponto interno (também acoplado a API interna, menos usada). Não testado aqui: afirmação sobre a extensão TLS vem da documentação, não de execução. |
| **nginx na frente** (`ssl_verify_client on`) | Exige serviço extra no Windows. `$ssl_client_fingerprint` do nginx é **SHA-1**; SHA-256 exigiria módulo/njs. O backend ficaria em HTTP simples numa porta loopback **alcançável por qualquer processo local** (contorna o mTLS) e a identidade viajaria em cabeçalho (spoofável se o backend for alcançado direto). |
| **stunnel na frente** | Mesmo problema do salto em texto claro em loopback; sem fixação por SHA-256 do certificado do cliente (só cadeia/CA), então a lista fixa teria de ser reimplementada atrás — sem a identidade. |
| **uvicorn "puro"** (`ssl_cert_reqs`) | O uvicorn não expõe o certificado do cliente ao ASGI; só valida a cadeia, não a impressão. Insuficiente para a lista fixa. |
| **Servidor próprio (revisão 1)** | Não aprovado: parser HTTP próprio. |

## O que ainda não foi provado
- mTLS por socket real neste PC (Avast intercepta TLS em loopback). Substituído por **TLS em memória (`ssl.MemoryBIO`)** com handshake real do OpenSSL
  (14 casos) e pelo **protocolo real do uvicorn** acionado sobre esse TLS em memória. O teste em socket real existe (`needs_tls`) e roda onde não há interceptação
  (roteiro Docker e verificação Windows preparados, não executados).
- Comportamento de h11 0.16.0 com formas exóticas de HTTP é confiado ao h11; cobrimos com fuzz determinístico (2400 entradas) e matriz de smuggling CL/TE.
