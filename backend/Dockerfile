FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system auneron \
    && useradd \
        --system \
        --gid auneron \
        --create-home \
        auneron

COPY backend/requirements.txt ./requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=auneron:auneron backend/ ./

RUN mkdir -p /app/uploads && chown auneron:auneron /app/uploads

# Identidade de build (VALUE-3.4D-2a): ALEGACAO fornecida pelo processo de
# build (CI / `scripts/verify_build_identity.py claims`). O default
# `unknown` NUNCA vira identidade limpa; a prova e o source_digest,
# verificado fora da imagem contra os objetos do commit.
ARG GIT_SHA=unknown
ARG GIT_DIRTY=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.auneron.build.dirty="${GIT_DIRTY}"
ENV AUNERON_GIT_SHA="${GIT_SHA}" \
    AUNERON_GIT_DIRTY="${GIT_DIRTY}"

USER auneron

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
