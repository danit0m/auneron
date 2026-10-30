"""
Testes de integração HTTP REAIS da pré-visualização de importação
(MVP-FIN-IMPORT-01 / TASK-019).

Usam o app real (``app.main``) e as fixtures existentes do conftest
(``client``, ``service_client``, ``unauthenticated_client``,
``db_session``), com ``require_api_key`` + sessão + RBAC reais. Nenhuma
dependência de auth/permissão é sobrescrita: ``dependency_overrides`` não
é usado. O único monkeypatch de configuração é ``settings.api_key=None``
para provar o 503 fail-closed, e o único monkeypatch de código é uma
função interna do parser para provar o 500 genérico.

PRÉ-REQUISITOS (não executar fora deles):
- só via launcher HTTP com GlobalDatabaseTestGuard, contra
  ``auneron_test`` provado exclusivo/descartável (R1-R6);
- a fixture autouse ``clean_database`` do conftest faz TRUNCATE ...
  RESTART IDENTITY CASCADE em 24 tabelas antes e depois de CADA teste;
- ``MAINTENANCE_ENABLED=false`` no processo (o lifespan do TestClient
  dispara loops de manutenção com escrita quando ligado);
- o patch de integração de ``app/main.py`` aplicado. Sem ele estes
  testes FALHAM (404/ausência de rota); não há skip.

Todos os dados são sintéticos. Nenhum segredo real está neste arquivo: a
API key vem do ambiente via conftest e as senhas abaixo são de usuários
sintéticos criados no banco de teste truncado.

TASK-022: a persistência zero compara, além do COUNT(*) de todas as
tabelas, um digest SHA-256 canônico do conteúdo de ``accounts`` (única
tabela gravada pelo import legado) e de ``users``/``auth_sessions`` (a
auth da rota só lê: ``require_user_session`` não grava nada), e um digest
de nome+tamanho+conteúdo dos arquivos de ``uploads/``. Falhas mostram só
digests e contagens, nunca linhas. A captura de logs força nível DEBUG
(inclui INFO) em toda a subárvore ``auneron`` e restaura tudo no fim.
"""

import ast
import contextlib
import hashlib
import io
import json
import logging
import uuid
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from fastapi.routing import APIRoute
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update
from sqlalchemy.orm import Session

import app.services.import_preview_parser as preview_parser
from app.api.routes.upload import UPLOAD_DIR
from app.core.authentication import hash_password
from app.core.config import settings
from app.core.import_preview_http import ImportPreviewHTTPMiddleware
from app.core.observability import RequestObservabilityMiddleware
from app.core.security import API_KEY_HEADER_NAME
from app.core.security import require_api_key
from app.main import app
from app.models.account import Account
from app.models.auth_session import AuthSession
from app.models.user import User


BACKEND_DIR = Path(__file__).resolve().parents[1]
MAIN_PATH = BACKEND_DIR / "app" / "main.py"

PATH = "/imports/preview"
CSV_URL = f"{PATH}?format=csv"
XLSX_URL = f"{PATH}?format=xlsx"
OCTET = {"Content-Type": "application/octet-stream"}

DEVELOPER_EMAIL = "developer.test@example.com"
SYNTHETIC_PASSWORD = "Senha-Preview-Auneron-123!"

PII_SENTINEL = "Fulana Sigilosa da Silva"
PII_EMAIL = "fulana.sigilosa@example.com"
PII_PHONE = "+55 (11) 98888-7777"
CANARY = "PII-CANARY-Fulana-Preview"
HEADER = "cliente;valor;vencimento;status;email;whatsapp"

INVALID_REQUEST = {
    "detail": {
        "code": "invalid_preview_request",
        "message": "Parâmetros da pré-visualização inválidos.",
    }
}
UNSUPPORTED_MEDIA = {
    "detail": {
        "code": "unsupported_media_type",
        "message": "Envie o arquivo como application/octet-stream.",
    }
}
FILE_TOO_LARGE = {
    "detail": {
        "code": "file_too_large",
        "message": "O arquivo excede o tamanho máximo permitido.",
    }
}
INVALID_CONTENT_LENGTH = {
    "detail": {
        "code": "invalid_content_length",
        "message": "Cabeçalho Content-Length inválido.",
    }
}
GENERIC_500 = {
    "detail": {
        "code": "import_preview_internal_error",
        "message": "Erro interno na pré-visualização de importação.",
    }
}

API_KEY_DETAIL = "Credencial de API inválida ou ausente."
API_KEY_UNAVAILABLE_DETAIL = "Autenticação da API não configurada."
SESSION_MISSING_DETAIL = "Sessão de usuário ausente."
SESSION_INVALID_DETAIL = "Sessão de usuário inválida ou expirada."
USER_UNAVAILABLE_DETAIL = "Usuário inativo ou indisponível."
FORBIDDEN_DETAIL = "Seu perfil não possui permissão para este recurso."


# ------------------------------------------------------------ utilitários


def _csv(*lines):
    return "\r\n".join(lines).encode("utf-8")


def _mixed_csv():
    return _csv(
        HEADER,
        f"{PII_SENTINEL};R$ 1.234,56;05/01/2026;Aberto;{PII_EMAIL};"
        f"{PII_PHONE}",
        f"{PII_SENTINEL} B;-5;2026-12-31;aberto;;",
        f"{PII_SENTINEL} C;10;2026-02-30;pago;;",
        "Beto Lima;10,00;2026-03-01;;;",
    )


def _many_rows_csv(count):
    lines = [HEADER]
    for index in range(count):
        if index % 10 == 9:
            lines.append(f"Cliente {index:04d};-1;2026-01-01;;;")
        else:
            lines.append(f"Cliente {index:04d};{index + 1},25;2026-01-01;;;")
    return _csv(*lines)


def _xlsx(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _post(test_client, data=b"", *, url=CSV_URL, headers=None):
    return test_client.post(
        url,
        content=data,
        headers=OCTET if headers is None else headers,
    )


def _assert_no_store(response):
    # httpx junta valores repetidos com ", "; igualdade exata prova um
    # único Cache-Control.
    assert response.headers.get("cache-control") == "no-store"


def _assert_no_pii(response):
    for secret in (PII_SENTINEL, PII_EMAIL, PII_PHONE, CANARY):
        assert secret not in response.text


def _create_user_and_login(
    test_client: TestClient,
    db_session: Session,
    *,
    role: str,
    email: str,
) -> User:
    # Mesmo padrão de tests/test_authorization.py: usuário sintético +
    # login real em /auth/login (API key do conftest), sem atalho de auth.
    user = User(
        name=f"Usuário Preview {role}",
        email=email,
        password_hash=hash_password(SYNTHETIC_PASSWORD),
        role=role,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = test_client.post(
        "/auth/login",
        json={"email": email, "password": SYNTHETIC_PASSWORD},
    )
    assert response.status_code == 200
    return user


def _table_counts(db_session: Session) -> dict[str, int]:
    names = db_session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
    ).scalars().all()

    counts = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = db_session.execute(
            text(f"SELECT COUNT(*) FROM {quoted}")
        ).scalar_one()

    # Encerra a transação de leitura para não segurar locks até o
    # TRUNCATE do teardown.
    db_session.rollback()
    return counts


# accounts: única tabela que import_service.import_clients grava (add +
# commit; duplicata = mesma tripla cliente/valor/vencimento). users e
# auth_sessions: require_user_session/require_permission só leem, então
# nenhuma coluna de sessão (nem last_login_at) é excluída da igualdade.
# As colunas vêm do modelo; colunas só no banco ficam fora do digest e são
# cobertas apenas pelo COUNT(*) de _table_counts.
DOMAIN_SNAPSHOT_TABLES = (
    Account.__table__,
    User.__table__,
    AuthSession.__table__,
)

SENTINEL_FILE_BYTES = b"sentinela;sintetica\r\npreview;nao-persistir\r\n"


def _canonical(value):
    # Canonicalização explícita: tipo desconhecido falha sem expor o
    # valor, em vez de cair em str()/default do json.
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return ["decimal", format(value, "f")]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    raise TypeError(f"tipo sem forma canônica: {type(value).__name__}")


def _table_digest(db_session: Session, table) -> dict:
    statement = select(table).order_by(*table.primary_key.columns)
    rows = db_session.execute(statement).all()

    hasher = hashlib.sha256()
    hasher.update(
        json.dumps([column.name for column in table.columns]).encode("utf-8")
    )
    for row in rows:
        hasher.update(
            json.dumps(
                [_canonical(value) for value in row],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        hasher.update(b"\n")

    return {"rows": len(rows), "sha256": hasher.hexdigest()}


def _domain_snapshot(db_session: Session) -> dict[str, dict]:
    try:
        snapshot = {
            table.name: _table_digest(db_session, table)
            for table in DOMAIN_SNAPSHOT_TABLES
        }
    finally:
        # Leitura nunca segura transação aberta durante as requisições.
        db_session.rollback()
    assert not db_session.in_transaction()
    return snapshot


def _seed_sentinel_accounts(db_session: Session) -> None:
    # Mesma tripla (cliente, valor, vencimento) de linhas válidas dos
    # payloads, mas com status/contato diferentes do que um import
    # gravaria: upsert, UPDATE ou substituição mudam o digest.
    db_session.add_all([
        Account(
            cliente="Beto Lima",
            email="sentinela.beto@example.com",
            whatsapp="+55 11 90000-0001",
            valor=Decimal("10.00"),
            vencimento=date(2026, 3, 1),
            status="pago",
        ),
        Account(
            cliente=PII_SENTINEL,
            email=None,
            whatsapp=None,
            valor=Decimal("1234.56"),
            vencimento=date(2026, 1, 5),
            status="atrasado",
        ),
        Account(
            cliente=PII_SENTINEL,
            email="sentinela.xlsx@example.com",
            whatsapp=None,
            valor=Decimal("10.00"),
            vencimento=date(2026, 12, 31),
            status="pago",
        ),
    ])
    db_session.commit()


def _directory_digest(directory: Path):
    if not directory.exists():
        return None

    hasher = hashlib.sha256()
    entries = 0
    total_bytes = 0
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if path.is_dir():
            hasher.update(f"D {relative}\n".encode("utf-8"))
        else:
            content = path.read_bytes()
            total_bytes += len(content)
            hasher.update(
                f"F {relative} {len(content)} "
                f"{hashlib.sha256(content).hexdigest()}\n".encode("utf-8")
            )
        entries += 1

    return {
        "entries": entries,
        "bytes": total_bytes,
        "sha256": hasher.hexdigest(),
    }


@contextlib.contextmanager
def _upload_sentinel_file():
    # Arquivo sintético com nome único: sobrescrita ou remoção por um
    # preview mudaria o digest. Removido no finally.
    assert UPLOAD_DIR.is_dir()
    path = UPLOAD_DIR / f"preview_sentinel_{uuid.uuid4().hex}.csv"
    path.write_bytes(SENTINEL_FILE_BYTES)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@contextlib.contextmanager
def _auneron_log_capture(caplog, level=logging.DEBUG):
    # O logger "auneron" tem propagate=False e nível settings.log_level
    # (observability.configure_logging). Se esse nível for WARNING, os
    # registros INFO nem chegam a handlers. Durante a captura:
    # - o handler do caplog e toda a subárvore "auneron" existente
    #   descem para DEBUG (inclui INFO);
    # - loggers desabilitados são reabilitados;
    # - o handler é anexado a "auneron" e a todo descendente com
    #   propagate=False (sem duplicar os que propagam).
    # Tudo é restaurado no finally. O handler stdout de produção não é
    # tocado e mantém o próprio nível.
    handler = caplog.handler
    root = logging.getLogger("auneron")
    subtree = [root] + [
        logger
        for name, logger in sorted(logging.Logger.manager.loggerDict.items())
        if name.startswith("auneron.") and isinstance(logger, logging.Logger)
    ]
    attached = [root] + [logger for logger in subtree[1:] if not logger.propagate]

    saved_levels = [(logger, logger.level, logger.disabled) for logger in subtree]
    saved_handler_level = handler.level

    try:
        handler.setLevel(level)
        root.setLevel(level)
        for logger in subtree:
            if logger.level > level:
                logger.setLevel(level)
            logger.disabled = False
        for logger in attached:
            logger.addHandler(handler)
        yield caplog
    finally:
        for logger in attached:
            logger.removeHandler(handler)
        for logger, logger_level, disabled in saved_levels:
            logger.setLevel(logger_level)
            logger.disabled = disabled
        handler.setLevel(saved_handler_level)


def _record_text(record):
    return record.getMessage() + " " + json.dumps(
        record.__dict__,
        default=str,
        ensure_ascii=False,
    )


def _assert_records_free_of(records, secrets):
    # Exige registros capturados: lista vazia não prova ausência de PII.
    assert records
    for record in records:
        record_text = _record_text(record)
        for secret in secrets:
            # Mensagem limitada: só nome do logger e índice do segredo.
            assert secret not in record_text, (
                f"segredo #{secrets.index(secret)} em {record.name}"
            )


def _completed_info_records(records, status_code):
    return [
        record for record in records
        if record.name == "auneron.http"
        and record.levelno == logging.INFO
        and record.getMessage() == "http_request_completed"
        and getattr(record, "status_code", None) == status_code
    ]


# --------------------------------------------- registro e contrato legado


def test_preview_route_registered_once_with_business_dependencies():
    routes = [
        route for route in iter_route_contexts(app.routes)
        if isinstance(route.original_route, APIRoute) and route.path == PATH
    ]

    assert len(routes) == 1
    route = routes[0]
    assert route.methods == {"POST"}

    calls = [dependency.dependency for dependency in route.dependencies]
    assert calls.count(require_api_key) == 1
    permission_calls = [call for call in calls if call is not require_api_key]
    assert len(permission_calls) == 1
    closure = [
        cell.cell_contents
        for cell in (permission_calls[0].__closure__ or ())
    ]
    assert "imports.execute" in closure


def test_preview_middleware_registered_once_as_outermost_user_layer():
    # [INFERIDO] Starlette insere em user_middleware[0] a cada
    # add_middleware; o índice 0 é a camada de usuário mais externa.
    classes = [middleware.cls for middleware in app.user_middleware]

    assert classes.count(ImportPreviewHTTPMiddleware) == 1
    assert classes[0] is ImportPreviewHTTPMiddleware
    assert classes[1] is RequestObservabilityMiddleware


def _include_router_calls(tree):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "app"
        ):
            yield node


def _business_permission(call):
    for keyword in call.keywords:
        if keyword.arg != "dependencies":
            continue
        value = keyword.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "business_dependencies"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Constant)
        ):
            return value.args[0].value
    return None


def test_legacy_upload_contract_preserved_statically():
    # Contrato estático: o upload legado (que PERSISTE) não é chamado
    # aqui. A regressão comportamental continua em tests/test_upload.py,
    # que exige autorização própria por gravar no banco e em uploads/.
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    calls = list(_include_router_calls(tree))

    upload_calls = [
        call for call in calls
        if call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "upload_router"
    ]
    assert len(upload_calls) == 1
    assert _business_permission(upload_calls[0]) == "imports.execute"

    preview_calls = [
        call for call in calls
        if call.args
        and isinstance(call.args[0], ast.Call)
        and isinstance(call.args[0].func, ast.Name)
        and call.args[0].func.id == "create_import_preview_router"
    ]
    assert len(preview_calls) == 1
    assert _business_permission(preview_calls[0]) == "imports.execute"

    upload_routes = [
        route for route in iter_route_contexts(app.routes)
        if isinstance(route.original_route, APIRoute) and route.path == "/upload/"
    ]
    assert len(upload_routes) == 1
    assert upload_routes[0].methods == {"POST"}
    assert require_api_key in [
        dependency.dependency for dependency in upload_routes[0].dependencies
    ]


# -------------------------------------------------- auth real: 401/403/503


def test_missing_api_key_returns_401_no_store(
    unauthenticated_client: TestClient,
):
    response = _post(unauthenticated_client, _mixed_csv())

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApiKey"
    assert response.json() == {"detail": API_KEY_DETAIL}
    _assert_no_store(response)
    _assert_no_pii(response)


def test_invalid_api_key_returns_401_no_store(
    unauthenticated_client: TestClient,
):
    response = _post(
        unauthenticated_client,
        _mixed_csv(),
        headers={
            **OCTET,
            API_KEY_HEADER_NAME: "invalid-api-key-00000000000000000000",
        },
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApiKey"
    _assert_no_store(response)
    _assert_no_pii(response)


def test_unconfigured_api_key_fails_closed_503_no_store(
    client: TestClient,
    monkeypatch,
):
    # Única origem de 503 desta rota: require_api_key sem chave
    # configurada. Não há gate HTTP de manutenção em /imports/preview.
    monkeypatch.setattr(settings, "api_key", None)

    response = _post(client, _mixed_csv())

    assert response.status_code == 503
    assert response.json() == {"detail": API_KEY_UNAVAILABLE_DETAIL}
    _assert_no_store(response)
    _assert_no_pii(response)


def test_api_key_without_session_returns_401_no_store(
    service_client: TestClient,
):
    response = _post(service_client, _mixed_csv())

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Session"
    assert response.json() == {"detail": SESSION_MISSING_DETAIL}
    _assert_no_store(response)


def test_invalid_session_cookie_returns_401_no_store(
    service_client: TestClient,
):
    service_client.cookies.set(
        settings.auth_cookie_name,
        "sessao-sintetica-inexistente",
    )

    response = _post(service_client, _mixed_csv())

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Session"
    assert response.json() == {"detail": SESSION_INVALID_DETAIL}
    _assert_no_store(response)


def test_inactive_user_returns_401_no_store(
    service_client: TestClient,
    db_session: Session,
):
    user = _create_user_and_login(
        service_client,
        db_session,
        role="analyst",
        email="analyst.inactive.preview@example.com",
    )
    user.active = False
    db_session.commit()

    response = _post(service_client, _mixed_csv())

    assert response.status_code == 401
    assert response.json() == {"detail": USER_UNAVAILABLE_DETAIL}
    _assert_no_store(response)


def test_viewer_without_imports_execute_gets_403_no_store(
    service_client: TestClient,
    db_session: Session,
):
    _create_user_and_login(
        service_client,
        db_session,
        role="viewer",
        email="viewer.preview@example.com",
    )

    response = _post(service_client, _mixed_csv())

    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN_DETAIL}
    _assert_no_store(response)
    _assert_no_pii(response)


def test_auth_precedes_query_validation(
    unauthenticated_client: TestClient,
):
    response = _post(
        unauthenticated_client,
        _mixed_csv(),
        url=f"{PATH}?format=xlsm",
    )

    assert response.status_code == 401
    _assert_no_store(response)


def test_permission_precedes_query_validation(
    service_client: TestClient,
    db_session: Session,
):
    _create_user_and_login(
        service_client,
        db_session,
        role="viewer",
        email="viewer.query.preview@example.com",
    )

    response = _post(service_client, b"", url=f"{PATH}?format=xlsm")

    assert response.status_code == 403
    _assert_no_store(response)


# ----------------------------------------------------- 200: sucesso real


def test_developer_csv_preview_default_omits_values(
    client: TestClient,
    caplog,
):
    with _auneron_log_capture(caplog):
        response = _post(client, _mixed_csv())

    assert response.status_code == 200
    _assert_no_store(response)
    assert response.headers["content-type"] == "application/json"
    assert response.headers.get("x-request-id")

    body = response.json()
    assert body["file_kind"] == "csv"
    assert (body["total_rows"], body["valid_rows"], body["invalid_rows"]) == (
        4, 2, 2,
    )
    assert body["valid_total"] == "1244.56"
    assert body["returned_rows"] == 4
    assert body["rows_truncated"] is False
    assert all("values" not in row for row in body["rows"])
    for secret in (PII_SENTINEL, PII_EMAIL, PII_PHONE, "preview.csv", "cliente;"):
        assert secret not in response.text

    # Prova que a captura inclui INFO: o 200 só gera INFO em auneron.http.
    assert len(_completed_info_records(caplog.records, 200)) == 1
    _assert_records_free_of(
        caplog.records,
        (PII_SENTINEL, PII_EMAIL, PII_PHONE),
    )


def test_analyst_role_is_allowed(
    service_client: TestClient,
    db_session: Session,
):
    _create_user_and_login(
        service_client,
        db_session,
        role="analyst",
        email="analyst.preview@example.com",
    )

    response = _post(service_client, _mixed_csv())

    assert response.status_code == 200
    _assert_no_store(response)
    assert all("values" not in row for row in response.json()["rows"])


def test_xlsx_preview_values_only_with_explicit_opt_in(
    client: TestClient,
    caplog,
):
    data = _xlsx([
        ["cliente", "valor", "vencimento", "status", "email"],
        [PII_SENTINEL, "10,00", "2026-12-31", "aberto", PII_EMAIL],
        ["X", "10,00", "2026-12-31", "aberto", None],
    ])

    with _auneron_log_capture(caplog):
        default = _post(client, data, url=XLSX_URL)
        explicit_false = _post(
            client, data, url=f"{XLSX_URL}&include_values=false"
        )
        opted_in = _post(
            client, data, url=f"{XLSX_URL}&include_values=true"
        )

    # O opt-in devolve valores no corpo, nunca nos logs.
    assert len(_completed_info_records(caplog.records, 200)) == 3
    _assert_records_free_of(caplog.records, (PII_SENTINEL, PII_EMAIL))

    for response in (default, explicit_false, opted_in):
        assert response.status_code == 200
        _assert_no_store(response)

    assert default.json() == explicit_false.json()
    assert all("values" not in row for row in default.json()["rows"])
    assert PII_SENTINEL not in default.text

    rows = opted_in.json()["rows"]
    assert opted_in.json()["file_kind"] == "xlsx"
    assert rows[0]["valid"] is True
    assert rows[0]["values"] == {
        "cliente": PII_SENTINEL,
        "valor": "10.00",
        "vencimento": "2026-12-31",
        "status": "aberto",
        "email": PII_EMAIL,
        "whatsapp": None,
    }
    assert rows[1]["valid"] is False
    assert rows[1]["values"] is None


def test_preview_truncates_returned_rows_but_keeps_totals(client: TestClient):
    response = _post(client, _many_rows_csv(250))

    assert response.status_code == 200
    _assert_no_store(response)
    body = response.json()
    assert (body["total_rows"], body["valid_rows"], body["invalid_rows"]) == (
        250, 225, 25,
    )
    assert body["returned_rows"] == 100
    assert len(body["rows"]) == 100
    assert body["rows_truncated"] is True


# ------------------------------------------------- 4xx do adaptador real


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?format=",
        "?format=xlsm",
        "?format=CSV",
        "?format=csv&format=xlsx",
        "?format=csv&include_values=1",
        "?format=csv&include_values=True",
        "?format=csv&include_values=true&include_values=true",
    ],
)
def test_invalid_query_returns_fixed_422_no_store(client: TestClient, query):
    response = _post(client, _mixed_csv(), url=f"{PATH}{query}")

    assert response.status_code == 422
    assert response.json() == INVALID_REQUEST
    _assert_no_store(response)
    _assert_no_pii(response)


def test_multipart_upload_is_rejected_415(client: TestClient):
    response = client.post(
        CSV_URL,
        files={"file": ("clientes.csv", _mixed_csv(), "text/csv")},
    )

    assert response.status_code == 415
    assert response.json() == UNSUPPORTED_MEDIA
    _assert_no_store(response)
    _assert_no_pii(response)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Type": "text/csv"},
        {"Content-Type": "application/json"},
    ],
)
def test_non_octet_content_type_is_rejected_415(client: TestClient, headers):
    response = _post(client, _mixed_csv(), headers=headers)

    assert response.status_code == 415
    assert response.json() == UNSUPPORTED_MEDIA
    _assert_no_store(response)


@pytest.mark.parametrize(
    ("data", "url", "code"),
    [
        (b"", CSV_URL, "empty_file"),
        (b"PK\x03\x04" + CANARY.encode("utf-8"), CSV_URL, "content_mismatch"),
        (b"PK\x03\x04" + CANARY.encode("utf-8"), XLSX_URL, "malformed_zip"),
        (
            _csv("cliente;valor", f"{PII_SENTINEL};10,00"),
            CSV_URL,
            "missing_required_fields",
        ),
    ],
)
def test_parser_rejections_return_fixed_422_without_echo(
    client: TestClient,
    data,
    url,
    code,
):
    response = _post(client, data, url=url)

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": code,
            "message": preview_parser.FILE_ERROR_MESSAGES[code],
        }
    }
    _assert_no_store(response)
    _assert_no_pii(response)


def test_declared_oversize_returns_413_no_store(client: TestClient):
    response = _post(client, b"a" * (preview_parser.MAX_FILE_BYTES + 1))

    assert response.status_code == 413
    assert response.json() == FILE_TOO_LARGE
    _assert_no_store(response)


def test_streamed_oversize_without_content_length_returns_413(
    client: TestClient,
):
    # [INFERIDO] httpx envia corpo de gerador sem Content-Length
    # (chunked). O limite é aplicado na contagem real dos chunks.
    chunk = b"a" * (1024 * 1024)
    total_chunks = preview_parser.MAX_FILE_BYTES // len(chunk) + 1

    def body():
        for _ in range(total_chunks):
            yield chunk

    response = client.post(CSV_URL, content=body(), headers=OCTET)

    assert response.status_code == 413
    assert response.json() == FILE_TOO_LARGE
    _assert_no_store(response)


def test_invalid_content_length_returns_400_no_store(client: TestClient):
    # [INFERIDO] o cliente de teste repassa o Content-Length explícito ao
    # escopo ASGI. Se não repassar, investigar o harness, não relaxar.
    response = _post(
        client,
        b"x",
        headers={**OCTET, "Content-Length": "abc"},
    )

    assert response.status_code == 400
    assert response.json() == INVALID_CONTENT_LENGTH
    _assert_no_store(response)


def test_wrong_method_returns_405_no_store(client: TestClient):
    response = client.get(CSV_URL)

    assert response.status_code == 405
    _assert_no_store(response)


# --------------------------------------------- 500 genérico e logs seguros


def test_unexpected_parser_error_returns_generic_500_without_leak(
    client: TestClient,
    monkeypatch,
    caplog,
):
    def explode(*args, **kwargs):
        raise RuntimeError(CANARY)

    # Função interna resolvida em tempo de chamada dentro do parser real.
    monkeypatch.setattr(preview_parser, "_read_csv_table", explode)

    with _auneron_log_capture(caplog):
        response = _post(client, _mixed_csv())

    assert response.status_code == 500
    assert response.json() == GENERIC_500
    assert response.headers["content-type"] == "application/json"
    _assert_no_store(response)
    _assert_no_pii(response)

    preview_records = [
        record for record in caplog.records
        if record.name == "auneron.imports.http"
    ]
    assert len(preview_records) == 1
    assert preview_records[0].error_type == "RuntimeError"
    assert preview_records[0].exc_info is None

    # Observability fica por dentro do middleware do preview: vê a
    # exceção e registra só o tipo.
    http_failures = [
        record for record in caplog.records
        if record.name == "auneron.http"
        and getattr(record, "error_type", None) == "RuntimeError"
    ]
    assert len(http_failures) == 1
    # A exceção sobe até o middleware do preview: não há "completed".
    assert _completed_info_records(caplog.records, 500) == []

    _assert_records_free_of(
        caplog.records,
        (CANARY, PII_SENTINEL, PII_EMAIL, PII_PHONE),
    )


# ------------------------------------------------- persistência zero


def test_snapshot_detects_same_count_update_and_file_overwrite(
    db_session: Session,
    tmp_path: Path,
):
    # Poder discriminante dos snapshots: UPDATE com mesma contagem e
    # sobrescrita de arquivo com mesmo tamanho escapariam de COUNT(*) e
    # da listagem de nomes, mas não do digest. Mutação feita só pelo
    # próprio teste no banco truncado, sem passar por rota HTTP.
    _seed_sentinel_accounts(db_session)
    counts_before = _table_counts(db_session)
    snapshot_before = _domain_snapshot(db_session)
    assert snapshot_before["accounts"]["rows"] == 3

    first_id = db_session.execute(
        select(Account.__table__.c.id).order_by(Account.__table__.c.id)
    ).scalars().first()
    db_session.execute(
        update(Account.__table__)
        .where(Account.__table__.c.id == first_id)
        .values(status="aberto")
    )
    db_session.commit()

    snapshot_after = _domain_snapshot(db_session)
    assert _table_counts(db_session) == counts_before
    assert snapshot_after["accounts"]["rows"] == 3
    assert snapshot_after["accounts"]["sha256"] != (
        snapshot_before["accounts"]["sha256"]
    )
    assert snapshot_after["users"] == snapshot_before["users"]

    sentinel = tmp_path / "sentinela.csv"
    sentinel.write_bytes(SENTINEL_FILE_BYTES)
    files_before = _directory_digest(tmp_path)
    sentinel.write_bytes(SENTINEL_FILE_BYTES.upper())
    files_after = _directory_digest(tmp_path)

    assert len(SENTINEL_FILE_BYTES.upper()) == len(SENTINEL_FILE_BYTES)
    assert files_after["entries"] == files_before["entries"] == 1
    assert files_after["bytes"] == files_before["bytes"]
    assert files_after["sha256"] != files_before["sha256"]


def test_preview_does_not_persist_rows_or_files(
    client: TestClient,
    db_session: Session,
):
    _seed_sentinel_accounts(db_session)

    with _upload_sentinel_file():
        counts_before = _table_counts(db_session)
        snapshot_before = _domain_snapshot(db_session)
        uploads_before = _directory_digest(UPLOAD_DIR)

        assert {"accounts", "users", "auth_sessions", "knowledge"} <= set(
            counts_before
        )
        # Semeadura visível por outra transação, usuário/sessão do client
        # presentes: o digest cobre estado real, não tabelas vazias.
        assert snapshot_before["accounts"]["rows"] == 3
        assert snapshot_before["users"]["rows"] >= 1
        assert snapshot_before["auth_sessions"]["rows"] >= 1
        assert uploads_before["entries"] >= 1

        xlsx = _xlsx([
            ["cliente", "valor", "vencimento", "status"],
            [PII_SENTINEL, "10,00", "2026-12-31", "aberto"],
        ])
        responses = [
            _post(client, _mixed_csv()),
            _post(client, xlsx, url=f"{XLSX_URL}&include_values=true"),
            _post(client, b"PK\x03\x04", url=CSV_URL),
        ]

        assert [response.status_code for response in responses] == [
            200, 200, 422,
        ]
        assert _table_counts(db_session) == counts_before
        assert _domain_snapshot(db_session) == snapshot_before
        assert _directory_digest(UPLOAD_DIR) == uploads_before


def test_denied_requests_do_not_persist(
    unauthenticated_client: TestClient,
    db_session: Session,
):
    _seed_sentinel_accounts(db_session)

    with _upload_sentinel_file():
        counts_before = _table_counts(db_session)
        snapshot_before = _domain_snapshot(db_session)
        uploads_before = _directory_digest(UPLOAD_DIR)
        assert snapshot_before["accounts"]["rows"] == 3

        response = _post(unauthenticated_client, _mixed_csv())

        assert response.status_code == 401
        assert _table_counts(db_session) == counts_before
        assert _domain_snapshot(db_session) == snapshot_before
        assert _directory_digest(UPLOAD_DIR) == uploads_before
