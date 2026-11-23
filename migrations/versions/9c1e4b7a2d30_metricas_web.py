"""metricas web: Core Web Vitals reportadas por el frontend

Revision ID: 9c1e4b7a2d30
Revises: b8e2d4f6a3c7
Create Date: 2026-11-22 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c1e4b7a2d30'
down_revision: Union[str, Sequence[str], None] = 'b8e2d4f6a3c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'metrica_web',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=8), nullable=False),
        sa.Column('valor', sa.Float(), nullable=False),
        sa.Column('calificacion', sa.String(length=20), nullable=False),
        sa.Column('vista', sa.String(length=30), nullable=False),
        sa.Column('navegacion', sa.String(length=30), nullable=True),
        sa.Column('creada_en', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "nombre IN ('LCP', 'INP', 'CLS', 'FCP', 'TTFB')", name='ck_metrica_web_nombre'
        ),
        sa.CheckConstraint(
            "calificacion IN ('good', 'needs-improvement', 'poor')",
            name='ck_metrica_web_calificacion',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_metrica_web_nombre_creada', 'metrica_web', ['nombre', 'creada_en'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_metrica_web_nombre_creada', table_name='metrica_web')
    op.drop_table('metrica_web')
