"""Testes offline de scripts/scheduled_detect_overdue.py (requests simulado)."""

from __future__ import annotations

import json

import pytest

import scripts.scheduled_detect_overdue as scan

FAKE_KEY = "FAKEKEY-0123456789-abcdef"
FAKE_PASS = "FAKEPASS-9876543210"
STALE_KEY = "STALEKEY-from-file-123456"


class FakeResp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("sem json")
        return self._payload


class FakeSession:
    chamadas: list = []
    respostas: dict = {}

    def __init__(self):
        self.headers = {}

    def post(self, url, **kwargs):
        FakeSession.chamadas.append((url, dict(self.headers), kwargs))
        return FakeSession.respostas[url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1]]


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    cred = tmp_path / "cred.json"
    cred.write_text(
        json.dumps({"email": "svc@x.test", "password": FAKE_PASS, "api_key": STALE_KEY}),
        encoding="utf-8",
    )
    log = tmp_path / "log.txt"
    monkeypatch.setattr(scan, "LOG_PATH", log)
    monkeypatch.setattr(scan, "_segredos", [])
    monkeypatch.setattr(scan.requests, "Session", FakeSession)
    FakeSession.chamadas = []
    FakeSession.respostas = {}
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    return cred, log


def _log(log) -> str:
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_login_401_sai_1_e_loga_autenticacao_falhou(ambiente, capsys):
    cred, log = ambiente
    FakeSession.respostas["auth/login"] = FakeResp(401, text="chave invalida")
    assert scan.main(["--credenciais", str(cred)]) == 1
    txt = _log(log)
    assert "AUTENTICACAO: FALHOU" in txt and "status=401" in txt
    assert "VARREDURA" not in txt
    assert len(FakeSession.chamadas) == 1


def test_sucesso_sai_0_loga_contagens_e_usa_chave_do_ambiente(ambiente):
    cred, log = ambiente
    FakeSession.respostas["auth/login"] = FakeResp(200, {})
    FakeSession.respostas["accounts/detect-overdue"] = FakeResp(
        200,
        {
            "contas_verificadas": 7,
            "propostas_criadas": 2,
            "ja_com_proposta": 1,
            "falhas": 0,
            "aprovacoes_solicitadas": 2,
            "aprovacoes_falharam": 0,
        },
    )
    assert scan.main(["--credenciais", str(cred)]) == 0
    txt = _log(log)
    assert "AUTENTICACAO: OK" in txt
    assert "contas_verificadas=7" in txt and "propostas_criadas=2" in txt
    assert "ja_com_proposta=1" in txt and "falhas=0" in txt
    assert "aprovacoes_solicitadas=2" in txt and "aprovacoes_falharam=0" in txt
    # toda chamada usa a chave do ambiente; a do arquivo e ignorada
    assert all(h.get("X-API-Key") == FAKE_KEY for _, h, _ in FakeSession.chamadas)
    assert STALE_KEY not in json.dumps([h for _, h, _ in FakeSession.chamadas])
    # nenhum segredo no log
    for s in (FAKE_KEY, FAKE_PASS, STALE_KEY):
        assert s not in txt


def test_falhas_parciais_saem_2(ambiente):
    cred, log = ambiente
    FakeSession.respostas["auth/login"] = FakeResp(200, {})
    FakeSession.respostas["accounts/detect-overdue"] = FakeResp(
        200, {"contas_verificadas": 3, "falhas": 1}
    )
    assert scan.main(["--credenciais", str(cred)]) == 2
    assert "ATENCAO" in _log(log)


def test_varredura_nao_200_sai_1(ambiente):
    cred, log = ambiente
    FakeSession.respostas["auth/login"] = FakeResp(200, {})
    FakeSession.respostas["accounts/detect-overdue"] = FakeResp(500, text="erro")
    assert scan.main(["--credenciais", str(cred)]) == 1
    txt = _log(log)
    assert "AUTENTICACAO: OK" in txt and "VARREDURA: FALHOU" in txt


def test_api_key_ausente_sai_1_sem_chamar_a_api(ambiente, monkeypatch):
    cred, log = ambiente
    monkeypatch.delenv("API_KEY", raising=False)
    assert scan.main(["--credenciais", str(cred)]) == 1
    assert "API_KEY ausente" in _log(log)
    assert FakeSession.chamadas == []


def test_api_key_do_arquivo_nao_substitui_ambiente_ausente(ambiente, monkeypatch):
    cred, _ = ambiente
    monkeypatch.setenv("API_KEY", "   ")
    assert scan.main(["--credenciais", str(cred)]) == 1
    assert FakeSession.chamadas == []


def test_credenciais_ausentes_saem_1(ambiente, tmp_path):
    _, log = ambiente
    assert scan.main(["--credenciais", str(tmp_path / "nao-existe.json")]) == 1
    assert "nao encontrado" in _log(log)
