# Prepared migration; NOT applied. A2/A3 (registrar pagamento pela tela):
# duas tabelas laterais novas, somente create_table. accounts e
# approval_requests nao sao alteradas. FK RESTRICT: uma conta com
# pagamento registrado, ou um pedido com resumo/pagamento, nao pode ser
# apagado por baixo.
from alembic import op
from sqlalchemy import BigInteger, Column, Date, DateTime, ForeignKey, Integer, UniqueConstraint, func

revision = 'd5b8e2f4a671'
down_revision = 'e9a4c7d2b135'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'account_payments',
        Column('id', BigInteger, primary_key=True),
        Column(
            'account_id',
            Integer,
            ForeignKey(
                'accounts.id',
                ondelete='RESTRICT',
                name='fk_account_payments_account_id',
            ),
            nullable=False,
        ),
        Column('paid_on', Date, nullable=False),
        Column(
            'approval_request_id',
            BigInteger,
            ForeignKey(
                'approval_requests.id',
                ondelete='RESTRICT',
                name='fk_account_payments_approval_request_id',
            ),
            nullable=False,
        ),
        Column(
            'created_at',
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
        UniqueConstraint('account_id', name='uq_account_payments_account'),
        UniqueConstraint(
            'approval_request_id',
            name='uq_account_payments_approval_request',
        ),
    )
    op.create_table(
        'approval_request_inputs',
        Column('id', BigInteger, primary_key=True),
        Column(
            'approval_request_id',
            BigInteger,
            ForeignKey(
                'approval_requests.id',
                ondelete='RESTRICT',
                name='fk_approval_request_inputs_request_id',
            ),
            nullable=False,
        ),
        Column('effective_date', Date, nullable=False),
        Column(
            'created_at',
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
        UniqueConstraint(
            'approval_request_id',
            name='uq_approval_request_inputs_request',
        ),
    )


def downgrade():
    # Destrutivo: apaga datas de pagamento e resumos de pedidos. Somente
    # em banco descartavel ou com aprovacao explicita e backup previo.
    op.drop_table('approval_request_inputs')
    op.drop_table('account_payments')
