import csv
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import TextIO

from sqlalchemy.orm import Session

from app.core.account_identity_lock import lock_account_identity, account_identity_exists
from app.core.money import ZERO_MONEY
from app.core.money import money_to_json_number
from app.core.money import parse_localized_money
from app.models.account import Account
from app.services.account_data_entry_audit import audit_actor_from_ids
from app.services.account_data_entry_audit import record_create


STATUS_VALIDOS = {"aberto", "pago", "atrasado"}

SOURCE_HASH_CHUNK_BYTES = 64 * 1024


def abrir_csv(filepath: str) -> TextIO:
    """
    Tenta abrir o CSV usando as codificações mais comuns.
    """

    codificacoes = (
        "utf-8-sig",
        "cp1252",
        "latin-1",
    )

    ultimo_erro = None

    for codificacao in codificacoes:
        arquivo = None

        try:
            arquivo = open(
                filepath,
                mode="r",
                encoding=codificacao,
                newline="",
            )

            # Lê o arquivo inteiro para confirmar a codificação.
            arquivo.read()
            arquivo.seek(0)

            return arquivo

        except UnicodeDecodeError as erro:
            ultimo_erro = erro

            if arquivo is not None:
                arquivo.close()

    raise ValueError(
        "Não foi possível identificar a codificação do arquivo CSV."
    ) from ultimo_erro


def sha256_arquivo(filepath: str) -> str:
    # Bytes brutos do arquivo, em blocos. O tamanho já é limitado pelo
    # upload (MAX_UPLOAD_BYTES); aqui a memória fica limitada ao bloco.
    digest = sha256()

    with open(filepath, mode="rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(SOURCE_HASH_CHUNK_BYTES), b""):
            digest.update(bloco)

    return digest.hexdigest()


def identidade_canonica(cliente: str, valor: Decimal, vencimento) -> tuple:
    # Mesma normalização de account_identity_exists (v1): espaços colapsados
    # e casefold no cliente; valor e vencimento exatos.
    return (" ".join(cliente.split()).casefold(), valor, vencimento)


def converter_valor(valor_texto: str) -> Decimal:
    """
    Converte valores como:
    12400
    12400.00
    12.400,00
    12,400.00
    R$ 12.400,00
    """

    valor = parse_localized_money(
        valor_texto
    )

    if valor < ZERO_MONEY:
        raise ValueError(
            "O valor não pode ser negativo."
        )

    return valor


def converter_data(data_texto: str):
    """
    Aceita datas nos formatos:
    YYYY-MM-DD
    DD/MM/YYYY
    """

    data_texto = str(data_texto or "").strip()

    if not data_texto:
        raise ValueError(
            "O campo vencimento está vazio."
        )

    formatos = (
        "%Y-%m-%d",
        "%d/%m/%Y",
    )

    for formato in formatos:
        try:
            return datetime.strptime(
                data_texto,
                formato,
            ).date()

        except ValueError:
            continue

    raise ValueError(
        f"Data inválida: '{data_texto}'. "
        "Use YYYY-MM-DD ou DD/MM/YYYY."
    )


def linha_esta_vazia(row: dict) -> bool:
    """
    Retorna True quando todos os campos da linha estão vazios.
    """

    for valor in row.values():

        if isinstance(valor, list):
            if any(
                str(item or "").strip()
                for item in valor
            ):
                return False

        elif str(valor or "").strip():
            return False

    return True


def normalizar_linha(row: dict) -> dict:
    """
    Padroniza os nomes das colunas e ignora colunas sem cabeçalho.

    O DictReader utiliza a chave None quando uma linha possui
    mais colunas do que o cabeçalho.
    """

    linha_normalizada = {}

    for chave, valor in row.items():

        # Ignora colunas extras que não possuem cabeçalho.
        if chave is None:
            continue

        chave_normalizada = str(chave).strip().lower()

        if isinstance(valor, str):
            valor_normalizado = valor.strip()

        elif valor is None:
            valor_normalizado = ""

        else:
            valor_normalizado = valor

        linha_normalizada[chave_normalizada] = valor_normalizado

    return linha_normalizada


def import_clients(filepath: str, db: Session, *, actor_user_id, auth_session_id=None):
    # Ator humano obrigatório (sem default, sem fallback de sistema) e SHA do
    # arquivo, ambos antes de abrir o CSV e, portanto, antes do lock.
    actor = audit_actor_from_ids(actor_user_id, auth_session_id)
    source_sha256 = sha256_arquivo(filepath)

    importados = 0
    duplicados = 0
    erros = 0
    valor_total = ZERO_MONEY
    detalhes_erros = []

    # Linhas aceitas neste arquivo. Com autoflush=False as contas novas não
    # aparecem nas consultas de duplicata, daí o conjunto em memória.
    aceitas = []
    identidades_aceitas = set()

    arquivo = abrir_csv(filepath)

    try:
        lock_account_identity(db)

        amostra = arquivo.read(4096)
        arquivo.seek(0)

        try:
            dialect = csv.Sniffer().sniff(
                amostra,
                delimiters=",;",
            )

            delimitador = dialect.delimiter

        except csv.Error:
            delimitador = ","

        reader = csv.DictReader(
            arquivo,
            delimiter=delimitador,
        )

        if not reader.fieldnames:
            raise ValueError(
                "O arquivo CSV não possui cabeçalho."
            )

        cabecalho_normalizado = [
            str(campo).strip().lower()
            for campo in reader.fieldnames
            if campo is not None
        ]

        campos_obrigatorios = {
            "cliente",
            "valor",
            "vencimento",
            "status",
        }

        campos_ausentes = (
            campos_obrigatorios
            - set(cabecalho_normalizado)
        )

        if campos_ausentes:
            raise ValueError(
                "Campos obrigatórios ausentes no CSV: "
                + ", ".join(sorted(campos_ausentes))
            )

        for numero_linha, row in enumerate(
            reader,
            start=2,
        ):
            # Ignora linhas completamente vazias.
            if linha_esta_vazia(row):
                continue

            # Só a validação da linha conta como erro de linha; falhas de
            # banco ou de auditoria escapam para o rollback externo.
            try:
                row = normalizar_linha(row)

                cliente = str(
                    row.get("cliente") or ""
                ).strip()

                if not cliente:
                    raise ValueError(
                        "O campo cliente está vazio."
                    )

                email = str(
                    row.get("email") or ""
                ).strip() or None

                whatsapp = str(
                    row.get("whatsapp") or ""
                ).strip() or None

                valor = converter_valor(
                    row.get("valor")
                )

                vencimento = converter_data(
                    row.get("vencimento")
                )

                status = str(
                    row.get("status") or ""
                ).strip().lower()

                if status and status != "aberto":
                    raise ValueError(
                        "Status inicial não permitido no piloto: "
                        f"'{status}'. Use aberto ou deixe vazio."
                    )

                status = "aberto"

            except Exception as erro:
                erros += 1

                detalhes_erros.append({
                    "linha": numero_linha,
                    "erro": str(erro),
                })

                continue

            identidade = identidade_canonica(cliente, valor, vencimento)

            if identidade in identidades_aceitas:
                duplicados += 1
                continue

            existente = (
                db.query(Account)
                .filter(
                    Account.cliente == cliente,
                    Account.valor == valor,
                    Account.vencimento == vencimento,
                )
                .first()
            )

            if existente or account_identity_exists(db, Account, cliente, valor, vencimento):
                duplicados += 1
                continue

            conta = Account(
                cliente=cliente,
                email=email,
                whatsapp=whatsapp,
                valor=valor,
                vencimento=vencimento,
                status="aberto",
            )

            # Só linha aceita reserva a identidade.
            identidades_aceitas.add(identidade)
            aceitas.append((conta, numero_linha))

            importados += 1
            valor_total += valor

        if aceitas:
            db.add_all([conta for conta, _ in aceitas])
            # Um único flush após o laço (ids para a auditoria); um CHECK do
            # banco falha aqui e desfaz o arquivo inteiro.
            db.flush()

            for conta, numero_linha in aceitas:
                record_create(
                    db,
                    conta,
                    actor,
                    channel="legacy_csv_upload",
                    source_sha256=source_sha256,
                    source_row=numero_linha,
                )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        arquivo.close()

    return {
        "message": "Importação concluída",
        "summary": {
            "importados": importados,
            "duplicados": duplicados,
            "erros": erros,
            "valor_total": money_to_json_number(
                valor_total
            ),
        },
        "detalhes_erros": detalhes_erros,
    }
