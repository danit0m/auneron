import hashlib, json, os, sys
from datetime import datetime
from sim.collector.auth import ObserverSession
from sim.collector.client import GetOnlyClient, UrllibTransport
args = json.loads(sys.argv[1])
secrets = json.load(open(os.environ["SIM_COLLECTOR_SECRETS"]))
tr = UrllibTransport()
client = GetOnlyClient(tr, "http://sim-backend:8000", secrets["api_key"])
session = ObserverSession(tr, "http://sim-backend:8000", secrets["api_key"], args["email"], secrets["observer_password"],
                          "/state/dr2_session.json")
cookie = session.cookie(datetime.fromisoformat(args["now"]))
hashes = {}
statuses = {}
for rep in range(args["n"]):
    for path in args["paths"]:
        r = client.get(path, cookie)
        h = hashlib.sha256(json.dumps(r.json(), sort_keys=True).encode()).hexdigest()
        hashes.setdefault(path, []).append(h)
        statuses.setdefault(path, []).append(r.status)
print(json.dumps({"hashes": hashes, "statuses": statuses, "requests": args["n"] * len(args["paths"])}))
