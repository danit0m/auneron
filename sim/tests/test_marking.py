"""
T-8 -- marcacao sintetica e anti-PII: todo cliente com [SIM], email em
dominio reservado (RFC 2606), telefone com DDD inexistente, nomes so da
gramatica local, nenhum padrao de CPF/CNPJ.
"""

from __future__ import annotations

import re

CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
WHATSAPP = re.compile(r"^\+55 00 0000-\d{4}$")
EMAIL = re.compile(r"^c-\d{4}@nova-horizonte\.example\.com$")


def test_t8_customer_marking(scenario):
    for customer in scenario.world["customers"]:
        assert customer["name"].startswith("[SIM] ")
        assert EMAIL.match(customer["email"]), customer["email"]
        assert WHATSAPP.match(customer["whatsapp"]), customer["whatsapp"]


def test_t8_agenda_marking(scenario):
    for op in scenario.agenda["ops"]:
        if op["op"] == "create_receivable":
            assert op["cliente"].startswith("[SIM] ")
            assert op["email"].endswith("@nova-horizonte.example.com")
            assert WHATSAPP.match(op["whatsapp"])


def test_t8_names_only_from_local_grammar(scenario, inputs):
    grammar = inputs.names
    tokens = {"[SIM]"}
    for words in grammar["segment_types"].values():
        tokens.update(words)
    for phrase in grammar["cores"] + grammar["qualifiers"]:
        tokens.update(phrase.split())
    for customer in scenario.world["customers"]:
        assert set(customer["name"].split()) <= tokens, customer["name"]


def test_t8_no_document_number_patterns(scenario):
    for name, data in scenario.files.items():
        text = data.decode("utf-8")
        assert not CPF.search(text), name
        assert not CNPJ.search(text), name


def test_t8_company_identity_is_fictitious(inputs):
    company = inputs.company["company"]
    assert "ficticia" in company["name"]
    assert company["email_domain"].endswith(".example.com")
    assert not re.search(r"\d{2}\.\d{3}\.\d{3}", company["identifier"])
