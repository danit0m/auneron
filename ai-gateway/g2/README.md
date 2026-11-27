# AUNERON AI Gateway V1 (G2, revisão 2: uvicorn + h11)

Ponte mínima, só para texto **sintético**, entre um cliente autenticado por mTLS e o Ollama local. Não importa nada de `backend/`,
não tem banco, não guarda segredo do AUNERON. Nada aqui foi instalado como serviço, publicado ou ligado à produção.
Esta revisão **substitui o servidor HTTP próprio** por componentes consolidados (ver `ALTERNATIVAS.md`).

## O que faz
- Escuta **só em 127.0.0.1** (recusa outro host na partida), socket exclusivo (`SO_EXCLUSIVEADDRUSE`; sem reuso de porta). TLS 1.3 por padrão.
- **mTLS obrigatório** (CA própria, `CERT_REQUIRED`, `X509_STRICT`) + lista fixa de impressões SHA-256, conferida **por conexão, antes de qualquer parsing HTTP**
  (`GatewayProtocol` deriva de `uvicorn...H11Protocol`; recusa = `abort()` sem `super()`). Limite de 4 conexões **antes** do handshake.
- Duas rotas: `POST /v1/generate`, `GET /v1/health`; o resto é 404/405 (caminho bruto exato; sem docs, sem redirecionamento, sem `Server`/`Date`).
- Contrato estrito (pydantic), corpo ≤ 32 KiB lido em stream com limite real (também chunked), JSON estrito (sem chaves duplicadas/NaN/UTF-8 inválido).
- Ollama só em `127.0.0.1` e só `/api/version`, `/api/tags`, `/api/chat` (`http.client` em thread, sem proxy/redirect, cancelamento real); digest conferido antes e depois;
  perfis `qwen3-4b-instruct-2507` (padrão) e `qwen3-4b-think` (definido, **desligado**); `message.thinking` nunca devolvido/validado/logado.
- Limites: 1 geração, fila 2, 10/min, 120 s totais, 4 conexões; cancela ao desconectar/prazo. Logs JSON com campos fechados, rotação 30 dias.
- **Partida fail-closed**: host loopback; arquivos legíveis; **permissão da chave privada** (POSIX 0600 do dono; Windows: DACL lida por API, só dono/usuário/SYSTEM/Administradores);
  **certificado do servidor e da CA válidos agora** e par chave/certificado; **Ollama não exposto** (tabela de escuta do sistema IPv4+IPv6; senão sondagem em todos os endereços
  locais não-loopback incl. link-local com escopo; se nada for verificável: **recusa**, salvo `[startup].exposure_unverifiable="allow"`).
- Cliente de referência (`client`, `client_cli`): mTLS, hostname `ai.auneron.internal`, conexão 5 s, disjuntor 3/60 s, nunca repete após enviar.

## O que NÃO faz
Não é proxy do Ollama; sem streaming/ferramentas/arquivos/imagens; sem dado real; não instala serviço, firewall nem túnel; sem HTTP/2, WebSocket ou upgrade; não garante disponibilidade.

## Rodar
```
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m auneron_ai_gateway --config config\gateway.toml     # ver config\gateway.example.toml
```
## Testes
```
.venv\Scripts\python -m pytest                       # suíte completa (~2,5 min)
.venv\Scripts\python tools\mutate.py [nome ...]      # mutações (mutation_results.json)
.venv\Scripts\python tools\windows_mtls_check.py     # detecta interceptação de TLS e relata (não contorna); se limpo, roda o mTLS fim a fim
```
- **TLS em memória** (`tests/test_mem_mtls.py`): 14 casos de handshake real do OpenSSL via `ssl.MemoryBIO` + o protocolo REAL do uvicorn acionado sobre esse TLS. Imune a antivírus.
- **Fuzz determinístico** (`tests/test_fuzz.py`): 2400 entradas + matriz de smuggling CL/TE contra o servidor real.
- Os demais testes sobem o Gateway em processo separado e um Ollama **falso** (socket real, 127.0.0.1, porta temporária). Se o TLS em loopback for interceptado, os testes
  `needs_tls` (mTLS por socket real) são **pulados** e o resto roda sem a camada TLS (`tests/plain_launcher.py`, só teste).
- `docker/` traz um roteiro **preparado e não executado** para rodar a suíte inteira em container descartável (sem rede, sem root, somente leitura).

## Limitações
mTLS por socket real não provado neste PC (interceptação). Acoplamento à API interna do uvicorn (versão fixa + `test_uvicorn_interface.py`). h11 aceita HTTP/1.0 e LF puro (parser único, coerente).
Inspeção da tabela de escuta fora de Windows/Linux não existe (cai para sondagem). Ollama real, Cloudflare/cloudflared e suspensão do PC não testados.
