"""web import layout: the --into page mirrors the index, so section pages are gone

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-26 09:20:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("web_imports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cursor", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("layout_hash", sa.String(length=64), nullable=True))
        batch_op.drop_column("section_pages")


def downgrade() -> None:
    with op.batch_alter_table("web_imports", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("section_pages", sa.JSON(), server_default="{}", nullable=False)
        )
        batch_op.drop_column("layout_hash")
        batch_op.drop_column("cursor")
