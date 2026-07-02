"""create_notification_dedup_table

Cria tabela notification_dedup para garantir idempotência de notificações.
Evita reenvio de emails para o mesmo cycle_id + target_url.

Revision ID: c3c3661057f0
Revises: 00003b563de7
Create Date: 2025-01-15

Requirements: 6.6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3c3661057f0"
down_revision: Union[str, None] = "00003b563de7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cria tabela notification_dedup."""
    op.create_table(
        "notification_dedup",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("cycle_id", sa.String(), nullable=False),
        sa.Column("target_url", sa.String(2048), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "cycle_id", "target_url", name="uq_cycle_url"
        ),
    )
    op.create_index(
        "ix_notification_dedup_cycle_url",
        "notification_dedup",
        ["cycle_id", "target_url"],
    )


def downgrade() -> None:
    """Remove tabela notification_dedup."""
    op.drop_index("ix_notification_dedup_cycle_url")
    op.drop_table("notification_dedup")
