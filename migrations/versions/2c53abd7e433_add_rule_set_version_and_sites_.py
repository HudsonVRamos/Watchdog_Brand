"""add_rule_set_version_and_sites_dispatched_to_monitoring_cycles

Adiciona colunas rule_set_version (VARCHAR 30) e sites_dispatched (INTEGER)
na tabela monitoring_cycles para suportar versionamento de regras e
rastreamento de sites despachados na fila SQS.

Revision ID: 2c53abd7e433
Revises:
Create Date: 2025-01-15

Requirements: 7.2, 3.1
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2c53abd7e433"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Adiciona colunas rule_set_version e sites_dispatched."""
    op.add_column(
        "monitoring_cycles",
        sa.Column("rule_set_version", sa.String(30), nullable=True),
    )
    op.add_column(
        "monitoring_cycles",
        sa.Column("sites_dispatched", sa.Integer(), server_default="0"),
    )


def downgrade() -> None:
    """Remove colunas rule_set_version e sites_dispatched."""
    op.drop_column("monitoring_cycles", "sites_dispatched")
    op.drop_column("monitoring_cycles", "rule_set_version")
