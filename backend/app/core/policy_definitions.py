"""
DW-7.3 -- Policy Definition V1 (code-versioned, not a database table).

Uma unica policy existe nesta primeira rodada de autoridade
policy-governed: `account.mark_overdue`. Nao ha requisito de editar a
regra em runtime sem deploy, e DW-7.1 proibe explicitamente que
aprendizado altere policy silenciosamente -- uma constante versionada em
codigo e a menor representacao suficiente (mesmo espirito de
`ACTION_METADATA` em `app/core/action_space_evaluator.py`: uma
declaracao propria deste modulo, nao um dado descoberto).

`PolicyAuthorityGrant.policy_key`/`policy_version` sao comparados contra
este registro em tempo de consumo -- um grant cuja `policy_version` nao
bate mais com a versao aqui registrada para o mesmo `policy_key` falha
fechado (version binding, DW-7.2 Bloco 4).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDefinition:
    policy_key: str
    policy_version: str
    skill_key: str


ACCOUNT_MARK_OVERDUE_POLICY_V1 = PolicyDefinition(
    policy_key="policy.account_mark_overdue.v1",
    policy_version="1",
    skill_key="account.mark_overdue",
)


POLICY_DEFINITIONS: dict[str, PolicyDefinition] = {
    ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key: (
        ACCOUNT_MARK_OVERDUE_POLICY_V1
    ),
}


def get_policy_definition(policy_key: str) -> PolicyDefinition | None:
    return POLICY_DEFINITIONS.get(policy_key)
