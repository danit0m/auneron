# Roteiro de teste descartável em container (PREPARADO, NÃO EXECUTADO)

Objetivo: provar o mTLS por socket real onde o antivírus do PC não intercepta TLS. Nada aqui foi rodado; não há Docker nesta etapa.
Não montar volumes do repositório. Não publicar portas. Sem rede durante a execução.

1. **Contexto de build (cópia, não o repositório)** — `docker/prepare_context.ps1 -Out C:\temp\aigw-ctx`
   copia `src/ tests/ tools/ config/ requirements*.txt pytest.ini` e baixa o **wheelhouse** (Linux, cp311) com
   `pip download --require-hashes --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --abi cp311 -d wheelhouse -r requirements.txt -r requirements-dev.txt`.
   (Esse passo usa rede só no host, só para os pacotes fixados com hash.)
2. **Preencher o digest** da imagem base no `ARG PYTHON_IMAGE` do `docker/Dockerfile.test` (ou `--build-arg`). Sem digest preenchido, não construir.
3. **Construir sem rede de dependências** (todas vêm do wheelhouse):
   `docker build --network none -f docker/Dockerfile.test -t aigw-test:descartavel C:\temp\aigw-ctx`
4. **Rodar** (sem rede, raiz somente leitura, sem capabilities, usuário não root, sem montagens):
   ```
   docker run --rm --network none --read-only --tmpfs /tmp:rw,size=256m --cap-drop ALL \
     --security-opt no-new-privileges --pids-limit 256 --memory 1g --user 10001:10001 \
     aigw-test:descartavel
   ```
   A suíte usa só 127.0.0.1 dentro do container (a interface `lo` existe com `--network none`).
   Esperado: os testes `needs_tls` **executam** (sem Avast) e passam; os testes `icacls` ficam pulados (Linux).
5. **Descartar**: `docker image rm aigw-test:descartavel`; apagar `C:\temp\aigw-ctx`.

Limites conhecidos: não foi executado; os testes de ACL do Windows não rodam em Linux (a verificação POSIX é testada por lógica pura);
a tabela de escuta usa `/proc/net/tcp*` (testada só por amostra de texto aqui).
