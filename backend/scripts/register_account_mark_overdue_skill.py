"""
Registra o catalogo de skill account.mark_overdue (Pilot Action Space,
P1.2A).

Script operacional, rodado manualmente uma vez por ambiente (dev/test/
producao) -- nao roda automaticamente no startup do app, mesmo padrao
ja usado para account.mark_paid (scripts/register_account_mark_paid_skill.py).
Idempotente: se a skill_key ja existir, nao recria nada e so reporta o
estado atual.

Este script registra somente o catalogo (Skill/SkillVersion/capability).
Nao cria AgentSkillBinding -- esse binding pertence ao cenario agent-only
do corredor legado 25M/25O/OverdueDetectionAgent, fora do escopo desta
fatia.

handler_reference preserva literalmente "app.skills.account:mark_overdue"
-- e comparado pelo executor pilot existente
(AccountMarkOverdueExecutionService._validate_catalog) como metadado
contratual, nunca resolvido/importado como modulo Python real por este
corredor.

Uso (a partir de backend/, igual ao padrao de scripts/create_user.py e
scripts/register_account_mark_paid_skill.py):
    python -m scripts.register_account_mark_overdue_skill
"""

from app.database.database import SessionLocal
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService

SKILL_KEY = "account.mark_overdue"
PROVIDER = "auneron.core"
HANDLER_REFERENCE = "app.skills.account:mark_overdue"
CAPABILITY_KEY = "account.status.mark_overdue"


def main() -> None:
    db = SessionLocal()

    try:
        service = SkillService(db)

        existing_skill = service.repository.find_skill_by_key(
            SKILL_KEY,
        )

        if existing_skill is not None:
            print(
                f"Skill '{SKILL_KEY}' ja registrada "
                f"(id={existing_skill.id}, "
                f"status={existing_skill.status}). "
                "Nada foi alterado."
            )
            return

        skill = service.register_skill(
            skill_key=SKILL_KEY,
            provider=PROVIDER,
            display_name="Marcar conta como atrasada",
            description=(
                "Transicao governada de status de uma Account para "
                "'atrasado', recomendada por NBA e materializada "
                "apenas pelo corredor transacional dedicado "
                "(25M/25O). Registro de catalogo desta fatia nao "
                "conecta nenhum corredor humano nem altera a "
                "autoridade agent-only existente."
            ),
        )

        draft = service.create_draft_version(
            skill_id=skill.id,
            version="1.0.0",
            runtime_kind="internal_python",
            handler_reference=HANDLER_REFERENCE,
            execution_mode="mutating",
            input_schema={
                "type": "object",
                "required": [
                    "account_id",
                    "expected_status",
                    "expected_due_date",
                ],
                "properties": {
                    "account_id": {
                        "type": "integer",
                    },
                    "expected_status": {
                        "type": "string",
                        "enum": [
                            "aberto",
                        ],
                    },
                    "expected_due_date": {
                        "type": "string",
                        "format": "date",
                    },
                },
            },
            output_schema={
                "type": "object",
                "required": [
                    "action",
                    "account_id",
                    "previous_status",
                    "new_status",
                    "changed",
                ],
                "properties": {
                    "action": {"type": "string"},
                    "account_id": {"type": "integer"},
                    "previous_status": {"type": "string"},
                    "new_status": {"type": "string"},
                    "changed": {"type": "boolean"},
                },
            },
        )

        publication = service.publish_version(
            draft.id,
            capabilities=[
                CapabilityInput(
                    capability_key=CAPABILITY_KEY,
                    access_mode="write",
                    resource_scope="account",
                    required=True,
                ),
            ],
        )

        print(
            f"Skill '{SKILL_KEY}' registrada e publicada com sucesso."
        )
        print(f"  skill_id: {skill.id}")
        print(f"  skill_version_id: {publication.version.id}")
        print(
            f"  execution_mode: {publication.version.execution_mode}"
        )
        print(
            f"  capabilities: "
            f"{[c.capability_key for c in publication.capabilities]}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
