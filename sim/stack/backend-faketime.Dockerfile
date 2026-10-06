# SIM-1.4 -- imagem DERIVADA do lab: base do commit exato + libfaketime.
# A camada extra so instala o pacote do sistema; nao toca app/, migrations/
# nem requirements.txt (o source_digest sd1 da base e preservado -- C1-C5
# verificados de forma independente nesta imagem).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfaketime \
    && rm -rf /var/lib/apt/lists/*
USER auneron
