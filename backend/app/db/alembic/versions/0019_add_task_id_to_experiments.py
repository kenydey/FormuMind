"""add task_id and attempt_count to experiments

Revision ID: 0019_add_task_id_to_experiments
Revises: 0018_kg_link_composite_index
Create Date: 2026-08-29 16:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0019_add_task_id_to_experiments'
down_revision = '0018_kg_link_composite_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add task_id and attempt_count columns to experiments table
    op.add_column('experiments', sa.Column('task_id', sa.String(length=36), nullable=True))
    op.add_column('experiments', sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'))
    
    # Create index on task_id for faster lookups
    op.create_index('ix_experiments_task_id', 'experiments', ['task_id'])


def downgrade() -> None:
    # Drop index and columns
    op.drop_index('ix_experiments_task_id', table_name='experiments')
    op.drop_column('experiments', 'attempt_count')
    op.drop_column('experiments', 'task_id')
