"""solicitudes en espera y abierto solo por decisión del creador

Revision ID: a7c3e9d1f2b4
Revises: cd045042d78f
Create Date: 2026-11-08 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9d1f2b4'
down_revision: Union[str, Sequence[str], None] = 'cd045042d78f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE = sa.text("estado IN ('pendiente', 'en_espera')")
PENDING = sa.text("estado = 'pendiente'")

# Grupos con todos los cupos ocupados
FULL_GROUPS = """
    SELECT g.id FROM grupo_estudio g
    WHERE (SELECT count(*) FROM miembro_grupo m WHERE m.grupo_id = g.id) >= g.cupo_maximo
"""


def upgrade() -> None:
    """Upgrade schema."""
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
    # Hasta ahora un grupo se cerraba solo al llenarse (el creador no podía cerrarlo): abierto pasa
    # a ser solo la decisión del creador, y las solicitudes de los grupos llenos quedan en espera
    op.execute("UPDATE grupo_estudio SET abierto = TRUE")
    op.execute(
        f"UPDATE solicitud SET estado = 'en_espera' "
        f"WHERE estado = 'pendiente' AND grupo_id IN ({FULL_GROUPS})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"UPDATE grupo_estudio SET abierto = FALSE WHERE id IN ({FULL_GROUPS})")
    op.execute("UPDATE solicitud SET estado = 'pendiente' WHERE estado = 'en_espera'")
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
