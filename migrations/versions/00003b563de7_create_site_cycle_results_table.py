"""create_site_cycle_results_table

Cria tabela site_cycle_results para rastrear o resultado de processamento
de cada site individual dentro de um ciclo de monitoramento.

Revision ID: 00003b563de7
Revises: 46f43161d4eb
Create Date: 2025-01-15

Requirements: 3.1, 3.4, 3.5
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "00003b563de7"
down_revision: Union[str, None] = "46f43161d4eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cria tabela site_cycle_results."""
    op.create_table(
        "site_cycle_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "site_id",
            sa.String(),
            sa.ForeignKey("target_sites.id"),
            nullable=False,
        ),
        sa.Column(
            "cycle_id",
            sa.String(),
            sa.ForeignKey("monitoring_cycles.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "detections_count", sa.Integer(), server_default="0"
        ),
        sa.Column("failure_reason", sa.String(1024), nullable=True),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint("site_id", "cycle_id", name="uq_site_cycle"),
    )
    op.create_index(
        "ix_site_cycle_results_cycle_id",
        "site_cycle_results",
        ["cycle_id"],
    )


def downgrade() -> None:
    """Remove tabela site_cycle_results."""
    op.drop_index("ix_site_cycle_results_cycle_id")
    op.drop_table("site_cycle_results")
