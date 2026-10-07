# Imagem do DRIVER (SIM-1.5, D-1.5.1-3): python minimo + SOMENTE sim/driver.
# Contexto = diretorio extraido por `git archive` com apenas sim/driver
# (o harness nunca usa a arvore de trabalho). Sem pip, sem Oracle, sem mundo.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --system simdrv \
    && useradd --system --gid simdrv --create-home simdrv \
    && mkdir -p /app/sim /state /inputs \
    && : > /app/sim/__init__.py \
    && chown -R simdrv:simdrv /state /inputs

WORKDIR /app
COPY sim/driver ./sim/driver

USER simdrv
CMD ["sleep", "infinity"]
