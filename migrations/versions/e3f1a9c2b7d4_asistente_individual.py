"""asistente individual: preguntas que cuentan para el límite, avisos aparte y página citada

Revision ID: e3f1a9c2b7d4
Revises: b5d2e8f4a1c6
Create Date: 2026-11-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f1a9c2b7d4'
down_revision: Union[str, Sequence[str], None] = 'b5d2e8f4a1c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('mensaje_asistente', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('cuenta_limite', sa.Boolean(), server_default=sa.text('true'), nullable=False)
        )
        batch_op.add_column(sa.Column('aviso', sa.Text(), nullable=True))
    with op.batch_alter_table('fuente_mensaje', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('orden', sa.Integer(), server_default=sa.text('0'), nullable=False)
        )
        batch_op.add_column(sa.Column('ubicacion', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('fuente_mensaje', schema=None) as batch_op:
        batch_op.drop_column('ubicacion')
        batch_op.drop_column('orden')
    with op.batch_alter_table('mensaje_asistente', schema=None) as batch_op:
        batch_op.drop_column('aviso')
        batch_op.drop_column('cuenta_limite')
