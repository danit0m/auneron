"""
Vincula (ou desvincula) o OverdueDetectionAgent a versao publicada da skill
account.mark_overdue.

Script operacional, rodado manualmente (comandos vincular-deteccao-vencidos e
desvincular-deteccao-vencidos do homologacao.ps1) a partir de backend/:
    python -m scripts.create_overdue_detection_binding vincular
    python -m scripts.create_overdue_detection_binding desvincular

Regras:
- A primeira coisa feita e conferir o banco (SELECT current_database()) na
  MESMA sessao que gravaria; se nao for o esperado, encerra sem escrever.
- vincular recusa (codigo 5) se houver PolicyAuthorityGrant ativo e nao
  expirado para account.mark_overdue: vincular o agente com L3 autorizado
  faria a deteccao executar sem aprovacao humana.
- Nunca apaga binding: desvincular apenas desabilita (enabled=false).
- Binding existente e desabilitado NAO e reabilitado por este script.

Codigos de saida:
    0  vinculado agora, ja vinculado, ou desvinculado/ja sem binding ativo
    3  skill/versao inelegivel, ou binding existente desabilitado (nada alterado)
    4  banco diferente do esperado (nada gravado)
    5  L3 com autoridade ativa (nada gravado)
"""

import sys

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.skill import AgentSkillBinding
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.services.overdue_detection_service import OVERDUE_AGENT_NAME
from app.services.skill_service import SkillService

EXPECTED_DATABASE = "auneron_neon_synthetic"
SKILL_KEY = "account.mark_overdue"
BINDING_PRIORITY = 100

MODE_BIND = "vincular"
MODE_UNBIND = "desvincular"
MODES = (MODE_BIND, MODE_UNBIND)

EXIT_OK = 0
EXIT_INELIGIBLE = 3
EXIT_WRONG_DATABASE = 4
EXIT_L3_AUTHORITY_ACTIVE = 5


def _active_grant_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(PolicyAuthorityGrant)
            .where(
                PolicyAuthorityGrant.skill_key == SKILL_KEY,
                PolicyAuthorityGrant.state == "active",
                PolicyAuthorityGrant.expires_at > func.now(),
            )
        ).scalar_one()
    )


def _active_skill(db: Session) -> SkillDefinition | None:
    return db.execute(
        select(SkillDefinition).where(
            SkillDefinition.skill_key == SKILL_KEY,
            SkillDefinition.status == "active",
        )
    ).scalar_one_or_none()


def _eligible_versions(db: Session, skill_id: int) -> list[SkillVersion]:
    return list(
        db.execute(
            select(SkillVersion)
            .where(
                SkillVersion.skill_id == skill_id,
                SkillVersion.status == "published",
                SkillVersion.execution_mode == "mutating",
                SkillVersion.runtime_kind == "internal_python",
            )
            .order_by(SkillVersion.id)
        ).scalars()
    )


def _bindings_for_skill(
    db: Session, skill_id: int
) -> list[AgentSkillBinding]:
    return list(
        db.execute(
            select(AgentSkillBinding)
            .join(
                SkillVersion,
                SkillVersion.id == AgentSkillBinding.skill_version_id,
            )
            .where(
                AgentSkillBinding.agent_name == OVERDUE_AGENT_NAME,
                SkillVersion.skill_id == skill_id,
            )
            .order_by(AgentSkillBinding.id)
        ).scalars()
    )


def _report(database: str, binding: AgentSkillBinding) -> None:
    print(f"  banco           : {database}")
    print(f"  binding_id      : {binding.id}")
    print(f"  agent           : {binding.agent_name}")
    print(f"  skill_version_id: {binding.skill_version_id}")
    print(f"  enabled         : {binding.enabled}")


def _refused_database(database: str, expected: str) -> int:
    print(
        f"RECUSADO: banco conectado e '{database}', esperado "
        f"'{expected}'. Nada foi gravado."
    )
    return EXIT_WRONG_DATABASE


def bind(db: Session, database: str) -> int:
    grants = _active_grant_count(db)
    if grants > 0:
        db.rollback()
        print(
            f"RECUSADO: L3 com autoridade ativa ({grants} "
            f"PolicyAuthorityGrant ativo e nao expirado para {SKILL_KEY}). "
            "Nada foi gravado."
        )
        return EXIT_L3_AUTHORITY_ACTIVE

    skill = _active_skill(db)
    if skill is None:
        db.rollback()
        print(
            f"INELEGIVEL: nao existe skill ativa '{SKILL_KEY}'. "
            "Nada foi gravado."
        )
        return EXIT_INELIGIBLE

    versions = _eligible_versions(db, skill.id)
    if len(versions) != 1:
        db.rollback()
        print(
            f"INELEGIVEL: esperada exatamente 1 versao published + mutating "
            f"+ internal_python de '{SKILL_KEY}', encontradas "
            f"{len(versions)}. Nada foi gravado."
        )
        return EXIT_INELIGIBLE
    version_id = versions[0].id

    existing = db.execute(
        select(AgentSkillBinding).where(
            AgentSkillBinding.agent_name == OVERDUE_AGENT_NAME,
            AgentSkillBinding.skill_version_id == version_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.enabled:
            print("Agente ja vinculado. Nada foi alterado.")
            _report(database, existing)
            db.rollback()
            return EXIT_OK
        print(
            "DIVERGENCIA: o binding ja existe, mas esta desabilitado. "
            "Nada foi alterado (este script nao reabilita)."
        )
        _report(database, existing)
        db.rollback()
        return EXIT_INELIGIBLE

    # Fecha a transacao de leitura antes do servico (que commita sozinho).
    db.rollback()
    created = SkillService(db).bind_agent(
        agent_name=OVERDUE_AGENT_NAME,
        version_id=version_id,
        priority=BINDING_PRIORITY,
        enabled=True,
    )
    binding_id = created.id
    db.expire_all()
    binding = db.get(AgentSkillBinding, binding_id)
    print("Agente vinculado agora.")
    _report(database, binding)
    return EXIT_OK


def unbind(db: Session, database: str) -> int:
    skill = _active_skill(db)
    bindings = _bindings_for_skill(db, skill.id) if skill else []
    enabled_ids = [b.id for b in bindings if b.enabled]
    db.rollback()

    if not bindings:
        print("Nenhum binding do agente para a skill. Nada foi alterado.")
        return EXIT_OK
    if not enabled_ids:
        print("Binding ja desabilitado. Nada foi alterado.")
        for binding in bindings:
            _report(database, binding)
        db.rollback()
        return EXIT_OK

    service = SkillService(db)
    for binding_id in enabled_ids:
        service.set_binding_enabled(binding_id, enabled=False)
    db.expire_all()
    print("Binding desabilitado (nenhum registro apagado).")
    for binding_id in enabled_ids:
        _report(database, db.get(AgentSkillBinding, binding_id))
    return EXIT_OK


def run(
    db: Session,
    mode: str = MODE_BIND,
    expected_database: str = EXPECTED_DATABASE,
) -> int:
    database = db.execute(text("SELECT current_database()")).scalar()
    if database != expected_database:
        db.rollback()
        return _refused_database(database, expected_database)

    if mode == MODE_BIND:
        return bind(db, database)
    if mode == MODE_UNBIND:
        return unbind(db, database)
    db.rollback()
    print(f"ERRO: modo desconhecido '{mode}'. Use vincular ou desvincular.")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1 or (args and args[0] not in MODES):
        print("Uso: python -m scripts.create_overdue_detection_binding "
              "[vincular|desvincular]")
        return 2
    mode = args[0] if args else MODE_BIND

    from app.database.database import SessionLocal

    db = SessionLocal()
    try:
        return run(db, mode)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
