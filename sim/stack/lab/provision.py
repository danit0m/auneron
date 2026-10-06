"""
Provisionamento rerun-safe / convergence-safe (Design Freeze V1.1, sec. 3).

Classes: CREATED | EXISTS_EQUIVALENT | EXISTS_DIFFERENT | UNEXPECTED_ERROR.
O exit code NUNCA e ignorado; uma recusa de duplicata so vira
EXISTS_EQUIVALENT depois de verificacao positiva (login + /auth/me para
usuarios; catalogo read-only para skills). Sem informacao suficiente =>
falha fechada (nunca inferir equivalencia).
"""

from __future__ import annotations

from sim.stack.lab import sqlro
from sim.stack.lab.guard import require_container

CREATED = "CREATED"
EXISTS_EQUIVALENT = "EXISTS_EQUIVALENT"
EXISTS_DIFFERENT = "EXISTS_DIFFERENT"
UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
PASSING = (CREATED, EXISTS_EQUIVALENT)
DUPLICATE_USER_MESSAGE = "Já existe um usuário com esse e-mail."
BACKEND = "auneron-sim-backend"


def classify_user_creation(code: int, text: str, verify, expected_role: str) -> tuple[str, str]:
    """`verify()` -> (ok: bool, detalhe) faz login + /auth/me."""
    if code == 0 and "Usuário criado" in text and f"role={expected_role}" in text.split():
        return CREATED, "created"
    if code != 0 and DUPLICATE_USER_MESSAGE in text:
        ok, detail = verify()
        return (EXISTS_EQUIVALENT if ok else EXISTS_DIFFERENT), detail
    return UNEXPECTED_ERROR, f"code={code}"


def classify_skill_registration(code: int, text: str, catalog_ok) -> tuple[str, str]:
    if code != 0:
        return UNEXPECTED_ERROR, f"code={code}"
    if "ja registrada" in text and "status=active" in text:
        ok, detail = catalog_ok()
        return (EXISTS_EQUIVALENT if ok else EXISTS_DIFFERENT), detail
    if "ja registrada" in text:
        return EXISTS_DIFFERENT, "registrada com status diferente de active"
    if "registrada e publicada com sucesso" in text:
        ok, detail = catalog_ok()
        return (CREATED if ok else UNEXPECTED_ERROR), detail
    return UNEXPECTED_ERROR, "saida nao reconhecida"


def create_user(runner, config, *, user: str, name: str, role: str, password: str):
    require_container(BACKEND, config)
    return runner.run(
        ["docker", "exec", "-i", BACKEND, "python", "-m", "scripts.create_user",
         "--name", name, "--email", config.email(user), "--role", role],
        input_text=f"{password}\n{password}\n",
    )


def verify_user(client_factory, config, *, user: str, name: str, role: str, password: str):
    client = client_factory()
    response = client.login(config.email(user), password)
    if response.status != 200:
        return False, f"login={response.status}"
    me = client.request("GET", "/auth/me")
    if me.status != 200:
        return False, f"me={me.status}"
    data = me.json() or {}
    person = data.get("user", data)
    expected = {"email": config.email(user), "role": role, "active": True, "name": name}
    got = {key: person.get(key) for key in expected}
    return (got == expected), f"me={got}"


def skill_catalog_ok(runner, config, key: str):
    rows = sqlro.query(runner, config, (
        "select s.skill_key, s.status, v.status, v.execution_mode, v.runtime_kind "
        "from skills s join skill_versions v on v.skill_id = s.id "
        f"where s.skill_key = '{key}'"
    ))
    published = [r for r in rows if r[1] == "active" and r[2] == "published"
                 and r[3] == "mutating" and r[4] == "internal_python"]
    return (len(published) == 1), f"rows={rows}"


def provision(runner, config, secrets: dict, client_factory) -> list[dict]:
    results = []
    entries = [dict(p) for p in config["personas"]] + [dict(config["probe"])]
    for entry in entries:
        password = secrets["users"][entry["user"]]
        result = create_user(runner, config, user=entry["user"], name=entry["name"],
                             role=entry["role"], password=password)
        status, detail = classify_user_creation(
            result.code, result.text,
            lambda e=entry, p=password: verify_user(client_factory, config, user=e["user"],
                                                    name=e["name"], role=e["role"], password=p),
            entry["role"],
        )
        results.append({"resource": f"user:{entry['user']}", "role": entry["role"],
                        "class": status, "detail": detail})
    for skill in config["skills"]:
        require_container(BACKEND, config)
        result = runner.run(["docker", "exec", BACKEND, "python", "-m", skill["script"]])
        status, detail = classify_skill_registration(
            result.code, result.text, lambda k=skill["key"]: skill_catalog_ok(runner, config, k))
        results.append({"resource": f"skill:{skill['key']}", "class": status, "detail": detail})
    return results


def negative_divergent_role(runner, config, secrets: dict, client_factory) -> dict:
    """Teste negativo do S-5: persona existente pedida com papel divergente.
    O script recusa a duplicata (nada muda) e a verificacao deve dar EXISTS_DIFFERENT."""
    persona = dict(config["personas"][0])
    divergent = "manager" if persona["role"] != "manager" else "analyst"
    password = secrets["users"][persona["user"]]
    result = create_user(runner, config, user=persona["user"], name=persona["name"], role=divergent,
                         password=password)
    status, detail = classify_user_creation(
        result.code, result.text,
        lambda: verify_user(client_factory, config, user=persona["user"], name=persona["name"],
                            role=divergent, password=password),
        divergent,
    )
    return {"resource": f"user:{persona['user']}", "requested_role": divergent, "class": status,
            "detail": detail}


def forbidden_absences(runner, config) -> dict:
    principal = sqlro.query(runner, config,
                            f"select count(*) from users where email = '{config['forbidden_principal_email']}'")
    bindings = sqlro.query(runner, config, "select count(*) from agent_skill_bindings")
    grants = sqlro.query(runner, config, "select count(*) from policy_authority_grants")
    return {"system_principal": int(principal[0][0]), "agent_skill_bindings": int(bindings[0][0]),
            "policy_authority_grants": int(grants[0][0])}
