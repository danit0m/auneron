import csv
from datetime import datetime
from decimal import Decimal
from typing import TextIO

from sqlalchemy.orm import Session

from app.core.money import ZERO_MONEY
from app.core.money import money_to_json_number
from app.core.money import parse_localized_money
from app.models.account import Account


STATUS_VALIDOS = {"aberto", "pago", "atrasado"}


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


def import_clients(filepath: str, db: Session):
    importados = 0
    duplicados = 0
    erros = 0
    valor_total = ZERO_MONEY
    detalhes_erros = []

    arquivo = abrir_csv(filepath)

    try:
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

                existente = (
                    db.query(Account)
                    .filter(
                        Account.cliente == cliente,
                        Account.valor == valor,
                        Account.vencimento == vencimento,
                    )
                    .first()
                )

                if existente:
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

                db.add(conta)

                importados += 1
                valor_total += valor

            except Exception as erro:
                erros += 1

                detalhes_erros.append({
                    "linha": numero_linha,
                    "erro": str(erro),
                })

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