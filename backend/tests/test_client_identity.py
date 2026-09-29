import asyncio

import pytest
import uvicorn
from pydantic import ValidationError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import BACKEND_DIR
from app.core.config import PACKAGED_PROXY_IPS
from app.core.config import Settings
from app.main import app


# Endereços fixados pelo desenho (SEC-AUTH-1.1/1.1A/1.1B/1.1C). Os dois
# primeiros são literalmente os valores de PACKAGED_PROXY_IPS -- se algum
# dia divergirem, os testes que usam a constante diretamente (não os
# literais abaixo) pegam a divergência.
NGINX_IP = "172.28.1.10"
TRAEFIK_IP = "172.28.0.10"
LB_IP = "203.0.113.5"
CLIENT_A = "198.51.100.20"
CLIENT_B = "198.51.100.21"
ATTACKER_IP = "203.0.113.99"


async def _resolve_client_ip(
    *,
    trusted_hosts: str,
    peer_host: str,
    forwarded_for: str | None,
) -> str | None:
    """Roda o ProxyHeadersMiddleware real (uvicorn==0.51.0) contra um
    scope ASGI sintético e devolve o client resolvido. Não usa Docker,
    Traefik ou Nginx reais -- exercita exatamente a classe que o Uvicorn
    carrega em runtime quando --proxy-headers está ativo."""

    captured: dict[str, tuple[str, int] | None] = {}

    async def inner_app(scope, receive, send) -> None:
        captured["client"] = scope.get("client")
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
            }
        )

    middleware = ProxyHeadersMiddleware(
        inner_app,
        trusted_hosts=trusted_hosts,
    )

    headers = []
    if forwarded_for is not None:
        headers.append(
            (b"x-forwarded-for", forwarded_for.encode("latin1"))
        )

    scope = {
        "type": "http",
        "client": (peer_host, 12345),
        "headers": headers,
    }

    async def receive():
        return {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }

    async def send(message) -> None:
        return None

    await middleware(scope, receive, send)

    client = captured["client"]
    return client[0] if client else None


def resolve_client_ip(
    *,
    trusted_hosts: str,
    peer_host: str,
    forwarded_for: str | None,
) -> str | None:
    return asyncio.run(
        _resolve_client_ip(
            trusted_hosts=trusted_hosts,
            peer_host=peer_host,
            forwarded_for=forwarded_for,
        )
    )


PACKAGED_TRUSTED_HOSTS = ",".join(sorted(PACKAGED_PROXY_IPS))


# ---------------------------------------------------------------------
# 1-8: mecanismo de resolução (ProxyHeadersMiddleware real, scope sintético)
# ---------------------------------------------------------------------


def test_trusted_peer_with_full_chain_resolves_real_client() -> None:
    resolved = resolve_client_ip(
        trusted_hosts=f"{NGINX_IP}/32,{TRAEFIK_IP}/32",
        peer_host=NGINX_IP,
        forwarded_for=f"{CLIENT_A}, {TRAEFIK_IP}",
    )

    assert resolved == CLIENT_A


def test_untrusted_peer_forged_xff_is_ignored() -> None:
    resolved = resolve_client_ip(
        trusted_hosts=f"{NGINX_IP}/32,{TRAEFIK_IP}/32",
        peer_host=ATTACKER_IP,
        forwarded_for=f"{CLIENT_A}, {TRAEFIK_IP}",
    )

    assert resolved == ATTACKER_IP


def test_untrusted_peer_varying_forged_xff_always_resolves_to_peer() -> None:
    for fake_chain in (
        "1.2.3.4",
        "1.2.3.4, 5.6.7.8",
        f"{CLIENT_A}, {TRAEFIK_IP}, {NGINX_IP}",
        "not-an-ip, also-not-an-ip",
    ):
        resolved = resolve_client_ip(
            trusted_hosts=f"{NGINX_IP}/32,{TRAEFIK_IP}/32",
            peer_host=ATTACKER_IP,
            forwarded_for=fake_chain,
        )

        assert resolved == ATTACKER_IP, (
            f"cadeia forjada {fake_chain!r} não deveria "
            "influenciar a identidade resolvida"
        )


def test_missing_traefik_in_allowlist_collapses_clients_into_traefik_ip() -> None:
    # Regressão real corrigida no SEC-AUTH-1.1B: confiar só em Nginx
    # (peer imediato) sem o hop do Traefik faz dois clientes distintos
    # colapsarem na mesma identidade -- a do Traefik, não a deles.
    resolved_a = resolve_client_ip(
        trusted_hosts=f"{NGINX_IP}/32",
        peer_host=NGINX_IP,
        forwarded_for=f"{CLIENT_A}, {TRAEFIK_IP}",
    )
    resolved_b = resolve_client_ip(
        trusted_hosts=f"{NGINX_IP}/32",
        peer_host=NGINX_IP,
        forwarded_for=f"{CLIENT_B}, {TRAEFIK_IP}",
    )

    assert resolved_a == TRAEFIK_IP
    assert resolved_b == TRAEFIK_IP
    assert resolved_a == resolved_b


def test_trusted_vs_untrusted_peer_same_xff_only_trusted_is_processed() -> None:
    forwarded_for = f"{CLIENT_A}, {TRAEFIK_IP}"

    trusted_result = resolve_client_ip(
        trusted_hosts=f"{NGINX_IP}/32,{TRAEFIK_IP}/32",
        peer_host=NGINX_IP,
        forwarded_for=forwarded_for,
    )
    untrusted_result = resolve_client_ip(
        trusted_hosts=f"{NGINX_IP}/32,{TRAEFIK_IP}/32",
        peer_host=ATTACKER_IP,
        forwarded_for=forwarded_for,
    )

    assert trusted_result == CLIENT_A
    assert untrusted_result == ATTACKER_IP


def test_packaged_proxy_ips_constant_resolves_real_client() -> None:
    # Ancora o comportamento do middleware à config congelada de produção
    # (PACKAGED_PROXY_IPS), não só a nomes arbitrários de teste.
    resolved = resolve_client_ip(
        trusted_hosts=PACKAGED_TRUSTED_HOSTS,
        peer_host=NGINX_IP,
        forwarded_for=f"{CLIENT_A}, {TRAEFIK_IP}",
    )

    assert resolved == CLIENT_A


def test_lb_chain_intact_vs_degraded() -> None:
    trusted_hosts = f"{NGINX_IP}/32,{TRAEFIK_IP}/32,{LB_IP}/32"

    intact = resolve_client_ip(
        trusted_hosts=trusted_hosts,
        peer_host=NGINX_IP,
        forwarded_for=f"{CLIENT_A}, {LB_IP}, {TRAEFIK_IP}",
    )
    degraded = resolve_client_ip(
        trusted_hosts=trusted_hosts,
        peer_host=NGINX_IP,
        forwarded_for=f"{LB_IP}, {TRAEFIK_IP}",
    )

    assert intact == CLIENT_A
    assert degraded == LB_IP


def test_external_lb_omitted_from_allowlist_resolves_to_lb_not_client() -> None:
    # Cadeia íntegra (cliente presente), mas o backend só confia em
    # Nginx+Traefik -- sem o LB na allowlist, o walker para no LB.
    resolved = resolve_client_ip(
        trusted_hosts=f"{NGINX_IP}/32,{TRAEFIK_IP}/32",
        peer_host=NGINX_IP,
        forwarded_for=f"{CLIENT_A}, {LB_IP}, {TRAEFIK_IP}",
    )

    assert resolved == LB_IP
    assert resolved != CLIENT_A


def test_default_vs_configured_forwarded_allow_ips_regression() -> None:
    forwarded_for = f"{CLIENT_A}, {TRAEFIK_IP}"

    default_result = resolve_client_ip(
        trusted_hosts="127.0.0.1",  # default verificado do uvicorn==0.51.0
        peer_host=NGINX_IP,
        forwarded_for=forwarded_for,
    )
    configured_result = resolve_client_ip(
        trusted_hosts=PACKAGED_TRUSTED_HOSTS,
        peer_host=NGINX_IP,
        forwarded_for=forwarded_for,
    )

    assert default_result == NGINX_IP
    assert configured_result == CLIENT_A


# ---------------------------------------------------------------------
# 9: log de startup com a configuração efetiva
# ---------------------------------------------------------------------


def test_startup_log_reports_effective_forwarded_allow_ips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import settings

    with caplog.at_level("INFO", logger="auneron.application"):
        with TestClient(app):
            pass

    started_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "application_lifecycle"
        and getattr(record, "state", None) == "started"
    ]

    assert started_records, (
        "evento application_started não foi emitido"
    )
    assert (
        started_records[-1].forwarded_allow_ips
        == settings.forwarded_allow_ips
    )


# ---------------------------------------------------------------------
# 10-11: biblioteca real do uvicorn.Config + sentinela comportamental
# ---------------------------------------------------------------------


def test_uvicorn_config_reads_forwarded_allow_ips_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FORWARDED_ALLOW_IPS",
        PACKAGED_TRUSTED_HOSTS,
    )

    config = uvicorn.Config(
        app,
        proxy_headers=True,
        forwarded_allow_ips=None,
    )

    assert config.forwarded_allow_ips == PACKAGED_TRUSTED_HOSTS


def test_uvicorn_config_default_forwarded_allow_ips_is_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sentinela comportamental (não só uvicorn.__version__): se uma
    # atualização futura do uvicorn mudar esse default, este teste quebra
    # e força reabrir o desenho -- ver SEC-AUTH-1.1C item 2.
    monkeypatch.delenv(
        "FORWARDED_ALLOW_IPS",
        raising=False,
    )

    config = uvicorn.Config(
        app,
        proxy_headers=True,
        forwarded_allow_ips=None,
    )

    assert config.forwarded_allow_ips == "127.0.0.1"


# ---------------------------------------------------------------------
# 12: regressão do Dockerfile
# ---------------------------------------------------------------------


def test_dockerfile_never_passes_forwarded_allow_ips_flag() -> None:
    dockerfile_path = BACKEND_DIR / "Dockerfile"
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "--forwarded-allow-ips" not in content, (
        "o Dockerfile não deve passar --forwarded-allow-ips "
        "explicitamente -- isso teria prioridade sobre a variável "
        "de ambiente FORWARDED_ALLOW_IPS e silenciaria a validação "
        "de Settings."
    )
    assert "--proxy-headers" in content


# ---------------------------------------------------------------------
# 13-17: validação de Settings (produção)
# ---------------------------------------------------------------------


VALID_PRODUCTION_DATABASE_URL = (
    "postgresql+psycopg://"
    "auneron:strong-password@postgres:5432/auneron"
)

VALID_PRODUCTION_API_KEY = (
    "Q7d9R2m8L4x1P6v3N5k0T8z2B9c4W7y1"
)


def _production_settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": VALID_PRODUCTION_DATABASE_URL,
        "API_KEY": VALID_PRODUCTION_API_KEY,
        "CORS_ORIGINS": "",
        "DEBUG": False,
        "DATABASE_ECHO": False,
        "EXPECTED_DATABASE_NAME": "auneron",
        "EXPECTED_DATABASE_HOST": "postgres",
        "FORWARDED_ALLOW_IPS": PACKAGED_TRUSTED_HOSTS,
    }
    values.update(overrides)

    return Settings(_env_file=None, **values)


def test_production_accepts_packaged_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FORWARDED_ALLOW_IPS",
        PACKAGED_TRUSTED_HOSTS,
    )

    settings = _production_settings()

    assert settings.forwarded_allow_ips == PACKAGED_TRUSTED_HOSTS


def test_production_accepts_packaged_proxies_plus_external_lb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = PACKAGED_TRUSTED_HOSTS + ",203.0.113.0/24"
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)

    settings = _production_settings(
        FORWARDED_ALLOW_IPS=value
    )

    assert settings.forwarded_allow_ips == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "*",
        "0.0.0.0/0",
        "::/0",
        "not-an-ip",
    ],
)
def test_production_rejects_invalid_forwarded_allow_ips(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)

    with pytest.raises(ValidationError):
        _production_settings(FORWARDED_ALLOW_IPS=value)


def test_production_rejects_packaged_proxy_written_as_broader_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "172.28.1.0/24" cobre o IP certo do Nginx, mas não é o literal /32
    # exato congelado em PACKAGED_PROXY_IPS -- não pode ser aceito como
    # substituto, mesmo contendo o endereço. Só CIDRs de LB/CDN externo
    # (não os dois proxies empacotados) podem ser mais amplos que /32.
    value = f"172.28.1.0/24,{TRAEFIK_IP}/32"
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)

    with pytest.raises(
        ValidationError,
        match="proxies empacotados",
    ):
        _production_settings(FORWARDED_ALLOW_IPS=value)


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "127.0.0.1/32",
        "::1",
        "::1/128",
        "127.5.5.5/32",
    ],
)
def test_production_rejects_loopback_forwarded_allow_ips(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full_value = f"{PACKAGED_TRUSTED_HOSTS},{value}"
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", full_value)

    with pytest.raises(
        ValidationError,
        match="loopback",
    ):
        _production_settings(
            FORWARDED_ALLOW_IPS=full_value
        )


def test_production_requires_packaged_proxies_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "203.0.113.0/24"
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)

    with pytest.raises(
        ValidationError,
        match="proxies empacotados",
    ):
        _production_settings(FORWARDED_ALLOW_IPS=value)


def test_production_requires_os_environ_to_match_settings_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simula Settings carregando de uma fonte diferente do ambiente real
    # do processo (ex.: .env fora de sincronia) -- deve falhar fechado.
    monkeypatch.setenv(
        "FORWARDED_ALLOW_IPS",
        PACKAGED_TRUSTED_HOSTS,
    )

    diverging_value = PACKAGED_TRUSTED_HOSTS + ",203.0.113.0/24"

    with pytest.raises(
        ValidationError,
        match="diverge",
    ):
        _production_settings(
            FORWARDED_ALLOW_IPS=diverging_value
        )
