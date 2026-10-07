"""
SIM-1.5 E-3.3 -- gate ESTATICO heuristico de leituras sem efeito do Collector.

Isto NAO e uma claim de "GET comprovadamente side-effect-free": e uma
heuristica sobre o AST do backend (PO: o DR-2 live continua obrigatorio).
O controle negativo (NBA) PRECISA ser detectado, senao o gate e vazio.
"""

from __future__ import annotations

import pytest

from sim.collector.allowlist import COLLECTOR_ROUTES
from sim.collector.allowlist import KNOWN_WRITING_GETS
from sim.tests.purity_scan import CONTROL_NEGATIVE
from sim.tests.purity_scan import HANDLERS
from sim.tests.purity_scan import Index
from sim.tests.purity_scan import scan


@pytest.fixture(scope="module")
def index():
    return Index()


def test_every_collector_route_has_a_scanned_handler():
    assert {name for name, _, _ in COLLECTOR_ROUTES} == set(HANDLERS)


@pytest.mark.parametrize("route", sorted(HANDLERS))
def test_allowlisted_handlers_have_no_write_calls_in_their_call_graph(index, route):
    rel, handler = HANDLERS[route]
    visited: set = set()
    assert scan(index, rel, handler, visited_out=visited) == [], route
    assert len(visited) >= 1


def test_scan_is_not_vacuous_it_follows_services_and_detects_the_nba_control(index):
    findings = scan(index, *CONTROL_NEGATIVE)
    assert any("persist" in call for _, call in findings)
    assert any("commit" in call for _, call in findings)
    visited: set = set()
    scan(index, *HANDLERS["approvals"], visited_out=visited)
    assert any(name.endswith("ApprovalService.list_requests") for name in visited)
    visited = set()
    scan(index, *HANDLERS["brain"], visited_out=visited)
    assert any(name.endswith("KnowledgeService.list") or name.endswith("KnowledgeService.find_by_account")
               for name in visited)
    assert KNOWN_WRITING_GETS == ("/recommendations/next-best-action/",)
