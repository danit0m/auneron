"""
Testes offline da rota NÃO REGISTRADA ``POST /imports/confirm``
(MVP-FIN-IMPORT-01 / TASK-024).

Standalone com unittest: app FastAPI mínimo e sintético, sem ``app.main``,
conftest, banco ou rede. Parser, transporte, plano, execução e adaptador
reais são carregados pelo caminho do arquivo; o repositório usado nos
fluxos felizes é um fake em memória.

    cd backend
    python -B tests/test_import_confirm_route.py -v
"""

import ast
import contextlib
from types import SimpleNamespace
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import APIRouter, Cookie, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, BACKEND_DIR / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parser = _load("confirm_route_parser", "app/services/import_preview_parser.py")
transport = _load("confirm_route_transport", "app/services/import_preview_transport.py")
plan = _load("confirm_route_plan", "app/services/import_confirmation_plan.py")
execution = _load("confirm_route_execution", "app/services/import_confirmation_execution.py")
repository_module = _load("confirm_route_repository", "app/services/import_confirmation_repository.py")
route = _load("confirm_route_under_test", "app/api/routes/import_confirm.py")

PII = "Fulana Sigilosa da Silva"
KEY = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
OTHER_KEY = "0d9e8f7a-6b5c-4d3e-9f1a-2b3c4d5e6f70"
CSV = f"cliente;valor;vencimento;status\r\n{PII};10,00;05/01/2026;aberto\r\nBruno;0,20;2026-02-28;".encode()
OTHER_CSV = b"cliente;valor;vencimento;status\r\nCarla;3,00;2026-03-01;aberto"
USERS = {"ok": (7, "admin"), "viewer": (8, "viewer"), "bad": ("7", "admin")}
ROLE_PERMS = {"admin": {"imports.execute", "clients.manage"}, "viewer": {"imports.execute"}}


class _Session:
    def __init__(self, user_id, role):
        self.user = type("U", (), {"id": user_id, "role": role})()


def require_api_key(x_api_key: str | None = Header(default=None)):
    if x_api_key != "k":
        raise HTTPException(401, "API key inválida.", headers={"WWW-Authenticate": "ApiKey"})


def require_permission(permission):
    def dependency(session: str | None = Cookie(default=None)):
        if session not in USERS:
            raise HTTPException(401, "Sessão ausente.")
        user_id, role = USERS[session]
        if permission not in ROLE_PERMS[role]:
            raise HTTPException(403, "Sem permissão.")
        return _Session(user_id, role)
    return dependency


class FakeRepository:
    def __init__(self, failure=None):
        self.receipts, self.accounts, self.failure = {}, [], failure

    @contextlib.contextmanager
    def transaction(self):
        if self.failure:
            raise self.failure
        yield self

    def lock_account_writers(self):
        pass

    def find_receipt(self, actor, key):
        return self.receipts.get((actor, key))

    def content_already_confirmed(self, sha, version):
        return any(r.content_sha256 == sha for r in self.receipts.values())

    def find_account_identities(self, candidates):
        return ()

    def create_account(self, draft):
        self.accounts.append(draft)
        return len(self.accounts)

    def append_audit_event(self, event):
        pass

    def insert_receipt(self, receipt):
        self.receipts[(receipt.actor_reference, receipt.idempotency_key)] = receipt


class _ParserSpy:
    def __init__(self):
        self.calls = 0
        self.ImportPreviewError = parser.ImportPreviewError
        self.MAX_FILE_BYTES = parser.MAX_FILE_BYTES

    def parse_import_preview(self, *args, **kwargs):
        self.calls += 1
        return parser.parse_import_preview(*args, **kwargs)


def _query(data, fmt="csv", content_sha=None):
    preview = parser.parse_import_preview(f"preview.{fmt}", data)
    return {
        "format": fmt,
        "expected_file_sha256": preview.file_sha256,
        "expected_content_sha256": content_sha or preview.content_sha256,
    }


class ConfirmRouteTests(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepository()
        self.parser = _ParserSpy()
        self.client = self._client(self.repo)

    def _client(self, repo, key_dependency=require_api_key):
        app = FastAPI()
        app.include_router(route.create_import_confirm_router(
            parser_module=self.parser, transport_module=transport, plan_module=plan,
            execution_module=execution, repository_module=repository_module,
            repository=repo, require_api_key=key_dependency,
            require_permission=require_permission,
        ))
        other = APIRouter()
        other.add_api_route("/other", lambda: {"ok": True})
        app.include_router(other)
        return TestClient(app, raise_server_exceptions=False)

    def _post(self, data=CSV, *, query=None, key=KEY, session="ok", api_key="k", headers=None, client=None):
        h = {"content-type": "application/octet-stream", "x-api-key": api_key, "idempotency-key": key}
        h.update(headers or {})
        (client or self.client).cookies.set("session", session)
        return (client or self.client).post(
            route.IMPORT_CONFIRM_PATH, params=query or _query(data), content=data, headers=h
        )

    def assertNoStore(self, response):
        self.assertEqual(response.headers.get_list("cache-control"), ["no-store"])

    def test_auth_failures_happen_before_parser_with_no_store(self):
        cases = ({"api_key": "x"}, {"session": "nobody"}, {"session": "viewer"})
        for kwargs, expected in zip(cases, (401, 401, 403)):
            response = self._post(**kwargs)
            self.assertEqual(response.status_code, expected)
            self.assertNoStore(response)
        self.assertEqual(self.parser.calls, 0)
        self.assertEqual(self.repo.accounts, [])

    def test_create_then_replay_with_session_actor_only(self):
        first = self._post(headers={"x-actor-reference": "user:999"})
        self.assertEqual(first.status_code, 201)
        self.assertNoStore(first)
        self.assertEqual(first.json()["account_references"], [1, 2])
        self.assertEqual(set(self.repo.receipts), {("user:7", KEY)})
        replay = self._post()
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["account_references"], [1, 2])
        self.assertEqual(len(self.repo.accounts), 2)

    def test_invalid_actor_in_session_fails_closed(self):
        response = self._post(session="bad")
        self.assertEqual(response.status_code, 500)
        self.assertNoStore(response)
        self.assertEqual(self.parser.calls, 0)

    def test_preview_mismatch_is_409_and_invalid_requests_are_422(self):
        mismatch = self._post(query=_query(CSV, content_sha="0" * 64))
        self.assertEqual(mismatch.json()["detail"]["code"], "content_mismatch")
        bad_rows = CSV + b"\r\nZeca;1,00;99/99/2026;aberto"
        self.assertEqual(self._post(bad_rows).json()["detail"]["code"], "invalid_rows")
        bad_key = self._post(key=KEY.upper())
        self.assertEqual(bad_key.json()["detail"]["code"], "invalid_idempotency_key")
        missing = self._post(query={"format": "csv"})
        self.assertEqual(mismatch.status_code, 409)
        self.assertNoStore(mismatch)
        for response in (bad_key, missing):
            self.assertEqual(response.status_code, 422)
            self.assertNoStore(response)
        self.assertEqual(self.repo.accounts, [])

    def test_conflicts_are_409(self):
        self.assertEqual(self._post().status_code, 201)
        same_content = self._post(key=OTHER_KEY)
        self.assertEqual(same_content.status_code, 409)
        self.assertEqual(same_content.json()["detail"]["code"], "content_already_imported")
        other_content = self._post(OTHER_CSV)
        self.assertEqual(other_content.status_code, 409)
        self.assertEqual(other_content.json()["detail"]["code"], "idempotency_conflict")
        self.assertEqual(len(self.repo.accounts), 2)

    def test_default_adapter_is_blocked_with_503_without_session(self):
        opened = []
        blocked = repository_module.PostgresImportConfirmationRepository(
            lambda: opened.append(1), execution
        )
        response = self._post(client=self._client(blocked))
        self.assertEqual(response.status_code, 503)
        self.assertNoStore(response)
        self.assertEqual(opened, [])

    def test_unexpected_failure_is_generic_500_without_pii(self):
        client = self._client(FakeRepository(failure=RuntimeError(PII)))
        response = self._post(client=client)
        self.assertEqual(response.status_code, 500)
        self.assertNoStore(response)
        self.assertEqual(response.json()["detail"], route.INTERNAL_ERROR)
        self.assertNotIn(PII, response.text)

    def test_slash_variant_has_auth_and_no_store_without_redirect(self):
        response = self.client.post(route.IMPORT_CONFIRM_PATH + '/', headers={'x-api-key': 'wrong'}, follow_redirects=False)
        self.assertEqual(response.status_code, 401)
        self.assertNoStore(response)
        self.assertEqual(self.parser.calls, 0)

    def test_http_exception_500_is_also_redacted(self):
        response = self._post(client=self._client(FakeRepository(failure=HTTPException(500, PII))))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['detail'], route.INTERNAL_ERROR)
        self.assertNotIn(PII, response.text)
        self.assertNoStore(response)

    def test_body_limit_and_media_type_fail_before_parser(self):
        too_big = self._post(b'x' * (parser.MAX_FILE_BYTES + 1), query=_query(CSV))
        self.assertEqual(too_big.status_code, 413)
        media = self._post(headers={'content-type': 'text/plain'})
        self.assertEqual(media.status_code, 415)
        for response in (too_big, media):
            self.assertNoStore(response)
        self.assertEqual(self.parser.calls, 0)

    def test_duplicate_idempotency_headers_are_rejected(self):
        self.client.cookies.set('session', 'ok')
        response = self.client.post(route.IMPORT_CONFIRM_PATH, params=_query(CSV), content=CSV,
            headers=[('x-api-key', 'k'), ('content-type', 'application/octet-stream'),
                     ('idempotency-key', KEY), ('idempotency-key', OTHER_KEY)])
        self.assertEqual(response.status_code, 422)
        self.assertNoStore(response)
        self.assertEqual(self.parser.calls, 0)

    def test_auth_service_503_is_preserved_and_redacted(self):
        def unavailable():
            raise HTTPException(503, PII)
        client = self._client(self.repo, key_dependency=unavailable)
        response = self._post(client=client)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail'], route.UNAVAILABLE)
        self.assertNotIn(PII, response.text)
        self.assertNoStore(response)
        self.assertEqual(self.parser.calls, 0)

    def test_other_routes_are_not_intercepted(self):
        response = self.client.get("/other")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cache-control", response.headers)

    def test_confirmation_is_registered_only_under_test_guard(self):
        tree = ast.parse((BACKEND_DIR / 'app/main.py').read_text(encoding='utf-8-sig'))
        calls = lambda node: [n for n in ast.walk(node) if isinstance(n,ast.Call) and getattr(n.func,'id',None) == 'create_import_confirm_router']
        guarded = [n for n in tree.body if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id == 'confirmation_available']
        self.assertEqual(len(calls(tree)),1)
        self.assertEqual(len(guarded),1)
        self.assertEqual(len(calls(guarded[0])),1)

    def test_test_only_guard_rejects_production_remote_and_wrong_database(self):
        settings = SimpleNamespace(environment='test', import_confirmation_test_enabled=True)
        url = SimpleNamespace(host='127.0.0.1', database='auneron_test', drivername='postgresql+psycopg')
        self.assertTrue(route.confirmation_test_is_available(settings,url))
        for target, field, value in ((settings,'environment','production'), (settings,'environment','development'),
            (settings,'import_confirmation_test_enabled',False),(url,'host','remote.example'),
            (url,'database','auneron'),(url,'drivername','sqlite')):
            original=getattr(target,field)
            setattr(target,field,value)
            self.assertFalse(route.confirmation_test_is_available(settings,url))
            setattr(target,field,original)


def _load_settings_class():
    # config.py instancia ``settings`` no import (le .env e o ambiente real).
    # Só a classe é carregada: a atribuição de módulo é removida antes do exec.
    path = BACKEND_DIR / 'app/core/config.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    tree.body = [n for n in tree.body if not (isinstance(n, ast.Assign)
        and [getattr(t, 'id', None) for t in n.targets] == ['settings'])]
    module = type(sys)('confirm_route_config')
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    # _secrets_dir=None volta ao default da classe (/run/secrets); a subclasse
    # remove .env e secrets na própria configuração.
    class SyntheticSettings(module.Settings):
        model_config = {**module.Settings.model_config, 'env_file': None, 'secrets_dir': None}
    return SyntheticSettings


Settings = _load_settings_class()
# Valores sintéticos de test_intelligence_config_flags.py.
TEST_URL = 'postgresql+psycopg://auneron:test_password@localhost:5432/auneron_test'
DEV_URL = TEST_URL.replace('auneron_test', 'auneron')
PROD_URL = 'postgresql+psycopg://auneron:strong-password@postgres:5432/auneron'
PROD_API_KEY = 'Q7d9R2m8L4x1P6v3N5k0T8z2B9c4W7y1'
PROD_PROXIES = '172.28.1.10/32,172.28.0.10/32'
ENVIRONMENTS = {
    'test': {'APP_ENV': 'test', 'DATABASE_URL': TEST_URL},
    'development': {'APP_ENV': 'development', 'DATABASE_URL': DEV_URL},
    'production': {'APP_ENV': 'production', 'DATABASE_URL': PROD_URL, 'API_KEY': PROD_API_KEY,
        'CORS_ORIGINS': '', 'EXPECTED_DATABASE_NAME': 'auneron', 'EXPECTED_DATABASE_HOST': 'postgres',
        'FORWARDED_ALLOW_IPS': PROD_PROXIES},
}


class ConfirmationFlagSettingsTests(unittest.TestCase):
    def _settings(self, environment, flag=None):
        env = dict(ENVIRONMENTS[environment])
        if flag is not None:
            env['IMPORT_CONFIRMATION_TEST_ENABLED'] = flag
        # Ambiente real isolado: só a configuração sintética é visível.
        with mock.patch.dict(os.environ, env, clear=True):
            return Settings(_env_file=None, _secrets_dir=None)

    def test_flag_defaults_to_false_in_every_environment(self):
        for environment in ENVIRONMENTS:
            with self.subTest(environment=environment):
                settings = self._settings(environment)
                self.assertEqual(settings.environment, environment)
                self.assertIs(settings.import_confirmation_test_enabled, False)

    def test_flag_true_is_allowed_only_with_app_env_test(self):
        self.assertIs(self._settings('test', 'true').import_confirmation_test_enabled, True)
        for environment in ('development', 'production'):
            with self.subTest(environment=environment):
                with self.assertRaises(ValidationError) as raised:
                    self._settings(environment, 'true')
                self.assertIn('IMPORT_CONFIRMATION_TEST_ENABLED', str(raised.exception))
                self.assertNotIn(PROD_API_KEY, str(raised.exception))

    def test_explicit_false_is_accepted_outside_test(self):
        for environment in ('development', 'production'):
            with self.subTest(environment=environment):
                self.assertIs(self._settings(environment, 'false').import_confirmation_test_enabled, False)


if __name__ == "__main__":
    unittest.main()
