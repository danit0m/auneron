# Imagem do EVIDENCE COLLECTOR (SIM-1.5, D-1.5.2-2): python minimo + SOMENTE
# sim/collector. Recebe um plano de coleta SEM valores; nunca o Oracle.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --system simcol \
    && useradd --system --gid simcol --create-home simcol \
    && mkdir -p /app/sim /state /inputs \
    && : > /app/sim/__init__.py \
    && chown -R simcol:simcol /state /inputs

WORKDIR /app
COPY sim/collector ./sim/collector

USER simcol
CMD ["sleep", "infinity"]
