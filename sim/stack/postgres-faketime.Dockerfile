# SIM-1.4 -- PostgreSQL do lab: postgres:17-alpine (mesma base do produto)
# + libfaketime (musl validado no SIM-1.1). Tambem serve ao container do
# relogio (sh), evitando imagem extra fora do prefixo auneron-sim.
FROM postgres:17-alpine
RUN apk add --no-cache libfaketime
