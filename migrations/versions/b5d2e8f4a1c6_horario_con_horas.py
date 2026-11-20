"""horario con hora de inicio y fin en vez de mañana o tarde

Revision ID: b5d2e8f4a1c6
Revises: 4cb380af6e72
Create Date: 2026-11-17 23:00:00.000000

"""
from datetime import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d2e8f4a1c6'
down_revision: Union[str, Sequence[str], None] = '4cb380af6e72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Horas con que se reemplazan los bloques de los grupos que ya existen
MORNING = (time(8, 30), time(13, 0))
AFTERNOON = (time(14, 0), time(18, 30))


def old_table():
    return sa.table(
        'horario_grupo',
        sa.column('grupo_id', sa.Integer()),
        sa.column('dia_semana', sa.Integer()),
        sa.column('es_manana', sa.Boolean()),
    )


def new_table():
    return sa.table(
        'horario_grupo',
        sa.column('grupo_id', sa.Integer()),
        sa.column('dia_semana', sa.Integer()),
        sa.column('hora_inicio', sa.Time()),
        sa.column('hora_fin', sa.Time()),
    )


def create_old() -> None:
    op.create_table('horario_grupo',
    sa.Column('grupo_id', sa.Integer(), nullable=False),
    sa.Column('dia_semana', sa.Integer(), nullable=False),
    sa.Column('es_manana', sa.Boolean(), nullable=False),
    sa.CheckConstraint('dia_semana BETWEEN 0 AND 5', name='ck_horario_dia'),
    sa.ForeignKeyConstraint(['grupo_id'], ['grupo_estudio.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('grupo_id', 'dia_semana', 'es_manana')
    )
    with op.batch_alter_table('horario_grupo', schema=None) as batch_op:
        batch_op.create_index('ix_horario_dia_bloque', ['dia_semana', 'es_manana'], unique=False)


def create_new() -> None:
    op.create_table('horario_grupo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('grupo_id', sa.Integer(), nullable=False),
    sa.Column('dia_semana', sa.Integer(), nullable=False),
    sa.Column('hora_inicio', sa.Time(), nullable=False),
    sa.Column('hora_fin', sa.Time(), nullable=False),
    sa.CheckConstraint('dia_semana BETWEEN 0 AND 5', name='ck_horario_dia'),
    sa.CheckConstraint('hora_fin > hora_inicio', name='ck_horario_rango'),
    sa.ForeignKeyConstraint(['grupo_id'], ['grupo_estudio.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('horario_grupo', schema=None) as batch_op:
        batch_op.create_index('ix_horario_dia_inicio', ['dia_semana', 'hora_inicio'], unique=False)
        batch_op.create_index('ix_horario_grupo', ['grupo_id'], unique=False)


def upgrade() -> None:
    """Upgrade schema."""
    # Cambia la clave primaria: se guarda lo que hay, se rehace la tabla y se vuelve a insertar
    # con los bloques convertidos a horas. Se hace desde Python para que SQLite y PostgreSQL
    # guarden las horas cada uno en su formato.
    rows = op.get_bind().execute(sa.select(old_table())).all()
    with op.batch_alter_table('horario_grupo', schema=None) as batch_op:
        batch_op.drop_index('ix_horario_dia_bloque')
    op.drop_table('horario_grupo')
    create_new()
    if rows:
        op.bulk_insert(new_table(), [
            {
                'grupo_id': grupo_id,
                'dia_semana': day,
                'hora_inicio': (MORNING if morning else AFTERNOON)[0],
                'hora_fin': (MORNING if morning else AFTERNOON)[1],
            }
            for grupo_id, day, morning in rows
        ])


def downgrade() -> None:
    """Downgrade schema."""
    # Cada horario vuelve al bloque donde empieza; los que quedan repetidos se juntan
    t = new_table()
    rows = op.get_bind().execute(sa.select(t.c.grupo_id, t.c.dia_semana, t.c.hora_inicio)).all()
    with op.batch_alter_table('horario_grupo', schema=None) as batch_op:
        batch_op.drop_index('ix_horario_grupo')
        batch_op.drop_index('ix_horario_dia_inicio')
    op.drop_table('horario_grupo')
    create_old()
    blocks = {(grupo_id, day, start < time(13, 0)) for grupo_id, day, start in rows}
    if blocks:
        op.bulk_insert(old_table(), [
            {'grupo_id': g, 'dia_semana': d, 'es_manana': m} for g, d, m in sorted(blocks)
        ])
