"""rename_file_path_to_s3_key_in_screenshots

Renomeia coluna file_path para s3_key na tabela screenshots.
Screenshots agora são armazenados diretamente no S3.

Revision ID: 46f43161d4eb
Revises: 2c53abd7e433
Create Date: 2025-01-15

Requirements: 4.2
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "46f43161d4eb"
down_revision: Union[str, None] = "2c53abd7e433"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Renomeia file_path para s3_key."""
    op.alter_column(
        "screenshots",
        "file_path",
        new_column_name="s3_key",
    )


def downgrade() -> None:
    """Renomeia s3_key de volta para file_path."""
    op.alter_column(
        "screenshots",
        "s3_key",
        new_column_name="file_path",
    )
