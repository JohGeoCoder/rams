"""Add start and end time to access groups

Revision ID: 1ad8716bfc74
Revises: 51f17950b88e
Create Date: 2024-01-10 06:42:20.302203

"""


# revision identifiers, used by Alembic.
revision = '1ad8716bfc74'
down_revision = '51f17950b88e'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
import residue


try:
    is_sqlite = op.get_context().dialect.name == 'sqlite'
except Exception:
    is_sqlite = False

if is_sqlite:
    op.get_context().connection.execute('PRAGMA foreign_keys=ON;')
    utcnow_server_default = "(datetime('now', 'utc'))"
else:
    utcnow_server_default = "timezone('utc', current_timestamp)"

def sqlite_column_reflect_listener(inspector, table, column_info):
    """Adds parenthesis around SQLite datetime defaults for utcnow."""
    if column_info['default'] == "datetime('now', 'utc')":
        column_info['default'] = utcnow_server_default

sqlite_reflect_kwargs = {
    'listeners': [('column_reflect', sqlite_column_reflect_listener)]
}

# ===========================================================================
# HOWTO: Handle alter statements in SQLite
#
# def upgrade():
#     if is_sqlite:
#         with op.batch_alter_table('table_name', reflect_kwargs=sqlite_reflect_kwargs) as batch_op:
#             batch_op.alter_column('column_name', type_=sa.Unicode(), server_default='', nullable=False)
#     else:
#         op.alter_column('table_name', 'column_name', type_=sa.Unicode(), server_default='', nullable=False)
#
# ===========================================================================


def upgrade():
    op.add_column('access_group', sa.Column('start_time', residue.UTCDateTime(), nullable=True))
    op.add_column('access_group', sa.Column('end_time', residue.UTCDateTime(), nullable=True))


def downgrade():
    op.drop_column('access_group', 'start_time')
    op.drop_column('access_group', 'end_time')
