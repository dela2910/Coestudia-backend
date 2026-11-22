"""rechazo automático al llenarse el grupo: sin solicitudes en espera

Revision ID: f4b8d2e6a9c1
Revises: cea7768fa53b
Create Date: 2026-11-22 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b8d2e6a9c1'
down_revision: Union[str, Sequence[str], None] = 'cea7768fa53b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE = sa.text("estado IN ('pendiente', 'en_espera')")
PENDING = sa.text("estado = 'pendiente'")


def upgrade() -> None:
    """Upgrade schema."""
    # Las solicitudes que esperaban un cupo quedan rechazadas, como si el grupo se hubiera llenado
    # con la regla nueva
    op.execute(
        "UPDATE solicitud SET estado = 'rechazada', respondida_en = CURRENT_TIMESTAMP "
        "WHERE estado = 'en_espera'"
    )
    with op.batch_alter_table('solicitud', schema=None) as batch_op:
        batch_op.drop_index('uq_solicitud_activa', sqlite_where=ACTIVE, postgresql_where=ACTIVE)
        batch_op.drop_constraint('ck_solicitud_estado', type_='check')
        batch_op.create_check_constraint(
            'ck_solicitud_estado',
            "estado IN ('pendiente', 'aceptada', 'rechazada', 'cancelada')",
        )
        batch_op.create_index(
            'uq_solicitud_pendiente', ['grupo_id', 'usuario_id'], unique=True,
            sqlite_where=PENDING, postgresql_where=PENDING,
        )


def downgrade() -> None:
    """Downgrade schema."""
    # Las que se rechazaron al migrar no vuelven a quedar en espera: no se sabe cuáles eran
    with op.batch_alter_table('solicitud', schema=None) as batch_op:
        batch_op.drop_index('uq_solicitud_pendiente', sqlite_where=PENDING, postgresql_where=PENDING)
        batch_op.drop_constraint('ck_solicitud_estado', type_='check')
        batch_op.create_check_constraint(
            'ck_solicitud_estado',
            "estado IN ('pendiente', 'en_espera', 'aceptada', 'rechazada', 'cancelada')",
        )
        batch_op.create_index(
            'uq_solicitud_activa', ['grupo_id', 'usuario_id'], unique=True,
            sqlite_where=ACTIVE, postgresql_where=ACTIVE,
        )
