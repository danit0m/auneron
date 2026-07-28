import pandas as pd
from datetime import date
from sqlalchemy.orm import Session
from ..financeiro.models import AccountsReceivable

def import_accounts_from_excel(file_content: bytes, db: Session):
    df = pd.read_excel(file_content)

    required_columns = ['client_name', 'invoice_number', 'due_date', 'amount']
    if not all(col in df.columns for col in required_columns):
        missing_cols = [col for col in required_columns if col not in df.columns]
        raise ValueError(f"Colunas obrigatórias faltando: {', '.join(missing_cols)}")

    imported_count = 0
    total_amount = 0.0
    overdue_amount = 0.0
    overdue_count = 0
    today = date.today()

    for index, row in df.iterrows():
        try:
            # Validação e conversão de tipos
            client_name = str(row['client_name'])
            invoice_number = str(row['invoice_number'])
            due_date = pd.to_datetime(row['due_date']).date()
            amount = float(row['amount'])

            # Verificar se já existe para evitar duplicidade (pelo invoice_number)
            existing_account = db.query(AccountsReceivable).filter(AccountsReceivable.invoice_number == invoice_number).first()
            if existing_account:
                # Opcional: atualizar existente ou pular
                continue

            account = AccountsReceivable(
                client_name=client_name,
                invoice_number=invoice_number,
                due_date=due_date,
                amount=amount,
                status="pending" # Default status
            )
            db.add(account)
            imported_count += 1
            total_amount += amount

            if due_date < today:
                overdue_amount += amount
                overdue_count += 1

        except Exception as e:
            print(f"Erro ao processar linha {index + 1}: {e}")
            # Continuar processando as outras linhas ou levantar exceção
            continue
    
    db.commit()

    return {
        "imported_count": imported_count,
        "total_amount_imported": total_amount,
        "overdue_count": overdue_count,
        "overdue_amount": overdue_amount,
        "message": f"{imported_count} contas importadas com sucesso."
    }
