"""notificaciones: cuándo vio el usuario sus avisos por última vez

Revision ID: b8e2d4f6a3c7
Revises: f4b8d2e6a9c1
Create Date: 2026-11-22 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e2d4f6a3c7'
down_revision: Union[str, Sequence[str], None] = 'f4b8d2e6a9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # add_column directo (sin batch): en SQLite el batch recrea la tabla usuario y falla por las
    # claves foráneas de las demás tablas
    op.add_column('usuario', sa.Column('avisos_vistos_en', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('usuario', 'avisos_vistos_en')
