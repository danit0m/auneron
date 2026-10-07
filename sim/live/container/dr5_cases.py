import json, os, sys, time
from datetime import date, timedelta
from urllib.parse import quote
from sim.driver import cli as C
from sim.driver.errors import HarnessError
from sim.driver.http import TransportError, UrllibTransport

DAY = __DAY__
CASE = int(sys.argv[1])
real = UrllibTransport()
SEQ = {0: 1, 1: 2, 2: 3, 3: 4}
MODE = {0: "drop_before", 1: "drop_after", 2: "drop_before", 3: "drop_before"}


class Fault:
    """Transporte do processo de teste: delega ao real; injeta a falha no 1o POST /accounts/ do caso."""
    def __init__(self, mode):
        self.mode, self.fired = mode, False

    def request(self, method, url, headers, body, timeout):
        if not self.fired and method == "POST" and url.endswith("/accounts/"):
            self.fired = True
            if self.mode == "drop_before":
                raise TransportError("injected:before_send")
            real.request(method, url, headers, body, timeout)          # o produto PROCESSOU a criacao
            raise TransportError("injected:after_send")
        return real.request(method, url, headers, body, timeout)


home = f"/tmp/dr5_case{CASE}"
env = dict(os.environ)
env["SIM_DRIVER_HOME"] = home
driver = C.build_driver(env, transport=Fault(MODE[CASE]))
op = next(o for o in driver.slots[(DAY, "09:00")] if o["seq"] == SEQ[CASE])
driver.slots[(DAY, "09:00")] = [op]
api_key = json.load(open(env["SIM_DRIVER_SECRETS"]))["api_key"]


def cookie():
    return driver.sessions.cookie_for(op["actor"], driver._now_virtual)


def accounts():
    rows, skip = [], 0
    while True:
        r = driver.http.request("GET", f"/accounts/?cliente={quote(op['cliente'], safe='')}&skip={skip}&limit=200",
                                cookie=cookie())
        rows.extend(r.json())
        if len(r.json()) < 200:
            return rows
        skip += 200


def create_direct():
    body = {"cliente": op["cliente"], "email": op["email"], "whatsapp": op["whatsapp"], "valor": op["valor"],
            "vencimento": (date.fromisoformat(driver.params["d0"]) + timedelta(days=int(op["vencimento_day"]))).isoformat()}
    return real.request("POST", "http://sim-backend:8000/accounts/",
                        {"X-API-Key": api_key, "Content-Type": "application/json", "Cookie": cookie(),
                         "Accept": "application/json"}, json.dumps(body).encode(), 30).status


outcome = {"case": CASE}
if CASE == 2:
    outcome["preexisting_status"] = [create_direct(), create_direct()]        # as 2 contas-fixture identicas
if CASE == 3:
    state = {"done": False}

    def hook(seconds):
        if not state["done"]:
            state["done"] = True
            outcome["injected_between_reads"] = create_direct()                 # a conta passa a existir entre R1 e R2
        time.sleep(seconds)

    driver.sleep = hook
else:
    driver.sleep = time.sleep
outcome["accounts_before"] = len(accounts())
started = time.time()
try:
    driver.run_slot(DAY, "09:00")
    outcome["result"] = "completed"
except HarnessError as error:
    outcome["result"] = "HARNESS_ERROR"
    outcome["error"] = str(error)[:300]
outcome["seconds"] = round(time.time() - started, 1)
outcome["accounts_after"] = len(accounts())
outcome["op_status"] = driver.store.op_status(op["seq"])
lines = [json.loads(l) for l in open(home + "/driver_evidence.jsonl", encoding="utf-8")]
keep = ("kind", "read", "pages", "seen", "candidates", "outcome", "http_status", "adopted_account_id",
        "resent_after_stable_absence", "account_id", "detail", "status", "attempt")
outcome["evidence"] = [{k: r[k] for k in keep if k in r} for r in lines]
print(json.dumps(outcome, default=str))
