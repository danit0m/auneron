"""
Registra o catalogo de skill account.mark_paid (Fatia 1, passo 5a/N).

Script operacional, rodado manualmente uma vez por ambiente (dev/test/
producao) -- nao roda automaticamente no startup do app, seguindo o
mesmo padrao ja usado para account.mark_overdue (tambem nunca foi
seedado em migracao). Idempotente: se a skill_key ja existir, nao
recria nada e so reporta o estado atual.

Uso (a partir de backend/, igual ao padrao de scripts/create_user.py
documentado em docs/AUTHENTICATION.md e docs/DEPLOYMENT.md):
    python -m scripts.register_account_mark_paid_skill
"""

from app.database.database import SessionLocal
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService

SKILL_KEY = "account.mark_paid"
PROVIDER = "auneron.core"
HANDLER_REFERENCE = "app.skills.account:mark_paid"
CAPABILITY_KEY = "account.status.mark_paid"


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
            display_name="Marcar conta como paga",
            description=(
                "Transicao governada de status de uma Account para "
                "'pago', solicitada por um humano e aprovada por "
                "outro humano com permissao approval:decide (fluxo "
                "sem Advisory Proposal -- nao ha agente sugerindo "
                "esta acao, ao contrario de account.mark_overdue)."
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
                ],
                "properties": {
                    "account_id": {
                        "type": "integer",
                    },
                    "expected_status": {
                        "type": "string",
                        "enum": [
                            "aberto",
                            "atrasado",
                        ],
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
