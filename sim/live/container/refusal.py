import json
from sim.collector import client as C
from sim.collector.allowlist import CapabilityViolation
calls = []
class Rec:
    def request(self, method, url, headers, body, timeout):
        calls.append((method, url)); return C.Response(200, {}, b"{}")
c = C.GetOnlyClient(Rec(), "http://sim-backend:8000", "k" * 64)
cases = [("POST", "/accounts/"), ("PUT", "/accounts/1"), ("PATCH", "/accounts/1"), ("DELETE", "/brain/1"),
         ("POST", "/brain/1/resolve"), ("POST", "/approvals/1/decision"), ("POST", "/accounts/1/execute-mark-paid"),
         ("POST", "/auth/login"), ("GET", "/recommendations/next-best-action/accounts/1/episodes/2026-01-07"),
         ("GET", "/users"), ("GET", "/auth/login"), ("GET", "/brain/1"), ("GET", "/accounts/?cliente=x&evil=1"),
         ("GET", "/work-items/1/human-assessment"), ("GET", "/docs")]
out = {}
for method, path in cases:
    try:
        c.request(method, path)
        out[f"{method} {path}"] = "NOT_REFUSED"
    except CapabilityViolation:
        out[f"{method} {path}"] = "REFUSED"
host = "NOT_REFUSED"
try:
    C.GetOnlyClient(Rec(), "http://127.0.0.1:8100", "k" * 64)
except CapabilityViolation:
    host = "REFUSED"
print(json.dumps({"cases": out, "wrong_host": host, "network_calls": len(calls),
                  "mutating_attrs": [a for a in ("post", "put", "patch", "delete") if hasattr(c, a)]}))
