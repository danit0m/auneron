# RELATORIO G2 revisao 2 (AI Gateway V1) - 24/11/2026

Pasta: `scratchpad\ai-gateway-r2` (a r1 em `ai-gateway` ficou intacta). Sem commit, sem alterar repositorio, sem Docker, sem Avast/excecoes.
Este relatorio e da r2; `suite_run1..3.txt` e `mutation_results.json` aqui sao da r2 (432 testes; 70 mutantes, incluindo os novos da r2).

## Resultado: PARCIAL (itens 1-4 entregues; mTLS por socket real continua NAO provado neste PC)
O Avast intercepta TLS em loopback. `tools/windows_mtls_check.py` (executada so na parte de deteccao) confirmou e RELATOU, sem contornar:
emissor visto "Avast Web/Mail Shield Untrusted Root", certificado do cliente nao chegou ao servidor, exit 3.
Substituto real: TLS em memoria (ssl.MemoryBIO, OpenSSL de verdade) + protocolo real do uvicorn sobre ele.

## O que mudou
- Servidor proprio removido (`httpwire.py` nao existe). Agora uvicorn 0.54.0 + h11 0.16.0; `GatewayProtocol(H11Protocol)`: `connection_made` confere
  `ssl_object.getpeercert(binary_form=True)` contra a lista fixa; recusado = `abort()` sem `super()`; `data_received/eof_received/pause/resume` ignoram se nao autenticado.
  Accept proprio em socket exclusivo (limite de 4 ANTES do handshake), contexto SSL proprio (CERT_REQUIRED, X509_STRICT, 1.3). Prazos de cabecalho e ocioso no protocolo; corpo em stream com limite real.
- App ASGI puro (sem FastAPI/Starlette; justificativa e comparacao com hypercorn, nginx/stunnel em `ALTERNATIVAS.md`). Rota por `raw_path` exato.
- Ollama: `http.client` em thread, sem proxy/redirect, peer loopback, leitura limitada a max+1, cancelamento real (fecha o socket; o Ollama falso ve a desconexao).
- Partida: permissao da chave (POSIX 0600; Windows DACL por API/SID, falha fechada), validade do certificado do servidor e da CA (leitor DER proprio), par chave/cert, exposicao do Ollama
  por tabela de escuta IPv4+IPv6 (GetExtendedTcpTable; /proc/net/tcp*) com fallback de sondagem (IPv6 e link-local com escopo) e recusa se nao verificavel (`[startup].exposure_unverifiable`), porta exclusiva (SO_EXCLUSIVEADDRUSE, sem reuso; 2o bind falha, provado).
- Acoplamento a API interna do uvicorn: versao fixada + `tests/test_uvicorn_interface.py` (falha se versao/assinaturas/atributos mudarem ou se `connection_made` chamar `super()` antes da identidade).
- Preparados e NAO executados: `docker/` (Dockerfile.test, ROTEIRO.md, prepare_context.ps1: imagem por digest a preencher, wheelhouse offline, --network none, nao root, somente leitura, sem montagens).

## Testes (suite final, 3 rodadas, mesmo codigo)
432 passed, 28 skipped em 166,0 s / 168,0 s / 169,2 s; exit 0; sem instabilidade. Os 28 pulados sao `needs_tls` (mTLS por socket real, interceptado aqui).
Novos: 14 casos de handshake em memoria + 7 do protocolo real (aborta nao listado sem parsear: transporte abortado, 0 bytes escritos, h11 IDLE, cycle/scope None, EOF -> False); fuzz deterministico 2400 entradas
(sem travar, sem 500, sem vazar vaga, sem excecao no loop) + matriz de smuggling CL/TE (17 formas ambiguas nunca executam a requisicao escondida; 3 legitimas pipelinadas exatamente 1x); ACL real via icacls por SID (Todos/Usuarios/Autenticados recusados);
validade do certificado; IPv6; porta exclusiva; cancelamento real do Ollama; resposta infinita limitada em bytes.
Comportamentos do h11 aceitos e documentados (testes ajustados, nao e smuggling): aceita HTTP/1.0, LF puro, funde Content-Length duplicado IDENTICO; erros de protocolo do h11 voltam como 400 text/plain (nao JSON).

## Mutacoes: 70 semeadas, 70 mortas, 0 sobreviventes finais
Incluem: pular a porta de identidade, chamar super() antes, recusa sem return, data_received sem guarda, comparacao sempre verdadeira, aceitar 0.0.0.0, bind em todas as interfaces, reuso de porta,
pular ACL da chave, SID estranho aceito, pular validade, pular IPv6 (candidatos, escopo link-local, tabela), curinga como loopback, exposicao desligada, sem prazo de cabecalho, limite de 1000 conexoes, etc.
Na 1a passada completa sobreviveram 2: `eof_unauth_kept` (faltava chamar eof_received direto) e `ollama_response_unbounded` (o teste so via Content-Length); adicionei testes e ambos morreram na reexecucao
(o segundo exigiu resposta chunked infinita). Os 67 restantes foram rodados antes desses testes extras (a suite so cresceu); `port_reuse` e morto pelo teste de fonte e `port_reuse_behavior` pelo comportamento (2o bind).
Ressalva: `cl_te_both_accepted` morre, mas h11 tambem recusa CL+TE parte das vezes (defesa em profundidade).

## Dependencias (todas com hash, --require-hashes)
Runtime: uvicorn 0.54.0 (BSD-3), h11 0.16.0 (MIT), click 8.5.1 (BSD-3), pydantic 2.14.4 (MIT), pydantic-core 2.50.5 (MIT), annotated-types 0.8.0 (MIT), typing-extensions 4.16.0 (PSF-2.0), typing-inspection 0.4.5 (MIT), colorama 0.4.6 (BSD, Windows).
Teste: cryptography 50.0.2 (Apache-2.0 OR BSD-3), cffi 2.1.1 (MIT-0), pycparser 3.12 (BSD-3), pytest 9.2.0, pluggy 1.7.0, iniconfig 2.3.1 (MIT), packaging 26.4, pygments 2.21.0 (BSD-2). Sem fastapi/starlette/httpx. Licencas de click/uvicorn/h11 conforme metadados conhecidos; nao reverificadas no PyPI nesta passada.

## Limites e riscos nao provados
- mTLS por socket real (28 testes pulados); Docker/Linux nao executado (roteiro nao testado; testes de ACL so em Windows).
- Dependencia de API interna do uvicorn (controlada por versao fixa e teste, nao eliminada). h11 e confiado para o parsing; fuzz nao e prova.
- Exposicao do Ollama: sem listener no momento da partida = ok ("ok_not_listening"), sem reverificacao periodica. Tabela de escuta so Windows/Linux. Nao verificado: ACL do arquivo da CA/certificado (so da chave).
- `tools/somas.py` foi reescrito (gravar LF) depois das 3 rodadas finais; nao afeta a suite. Ollama real, Cloudflare, suspensao do PC: nao testados.
- Processos de outras sessoes (`mutate.py --no-baseline`, scripts `emai*`) estavam rodando na maquina; nao sao meus e nao foram tocados.

## Limpeza
Nenhum processo do Gateway/pytest/mutacao meu ativo; porta 8443 livre; pastas `aigw-*` temporarias apagadas; sem `__pycache__`; certificados de teste so em pasta temporaria apagada. SOMAS.txt (LF, 52 arquivos) lista os SHA-256; RELATORIO.md fica fora do SOMAS.

## G2 r3: itens A e B
Base: `origin/trabalho/ai-gateway` (cef351c). Escopo so estes dois itens.

**A. Permissao de ESCRITA do certificado do servidor e da CA de clientes, na partida (falha fechada).**
`fsperm.check_public_file_not_writable` reaproveita a mesma leitura de ACL/modo da chave (`_check`, `_read_windows_acl`, `windows_violations(write_only=True)`); a leitura de ACL agora tambem devolve a mascara de acesso.
Leitura por outros e aceita (arquivos publicos). Windows: recusa ACE de permitir com qualquer bit de escrita (dados, anexar, EA, atributos, DELETE, WRITE_DAC, WRITE_OWNER, GENERIC_WRITE/ALL) para SID que nao seja dono, usuario do processo, SYSTEM, Administradores ou CREATOR OWNER; ACE de objeto, DACL nula e ACE sem mascara = recusa.
POSIX: sem escrita para grupo/outros e dono = usuario do processo ou root (decisao minha, alem do pedido: dono de outro usuario tambem recusa). Verificacao impossivel (arquivo ausente, erro de API) = recusa. `preflight` ganhou `public_check` (injetavel) e recusa com `permissao de escrita do certificado do servidor` / `... da CA de clientes`.
Apoio de teste: `tools/make_test_certs.py` passou a restringir a ACL do certificado e da CA de teste (a pasta temporaria herda escrita de outros SIDs e a partida agora corretamente recusaria).

**B. Reverificacao periodica da exposicao do Ollama.** `[startup] exposure_recheck_seconds` (padrao 60, minimo 10, maximo 3600; fora da faixa ou tipo errado = configuracao recusada). `exposure_watch.ExposureWatch` repete `check_ollama_not_exposed` (a mesma funcao da partida) numa unica tarefa asyncio (conferencia bloqueante no executor do laco), iniciada em `serve_forever` e cancelada e aguardada no `finally` (sem threads proprias).
Exposto, nao verificavel ou excecao = modo recusa: toda rota valida (inclusive `/v1/health`) responde 503 `{"error":{"code":"unavailable"}}` com `Connection: close` e `Retry-After`, sem ler o corpo e sem chamar o Ollama; 404/405 seguem iguais. Motivo no log (`ollama_exposed`, `exposure_unverifiable`) e a volta (`exposure_recovered`); volta ao normal sozinho quando a conferencia passa. Ligado em `__main__`.

**Testes:** `tests/test_r3.py` (20 novos; 1 so POSIX, pulado aqui). Suite completa 2x: 452 passed, 29 skipped por rodada (28 `needs_tls` + 1 POSIX), 0 falhas.
**Mutacao:** 27 mutantes novos `r3_*`, 27 mortos (97/97 em `mutation_results.json`); 1 sobreviveu na 1a passada (`r3_main_unwired`: o teste lia a fonte real e nao a copia mutada) e morreu apos corrigir o teste.
**Nao provado:** ACL real em POSIX (so logica pura e teste de modos que roda fora do Windows); reverificacao contra um Ollama real escutando em 0.0.0.0 (so verificador injetado); mTLS por socket real segue pulado (Avast).
