"""web imports v2: one-time import into Notion, updates compared with a local cache

The V2.3 tables (notes page + 原文 page per article) are replaced. Their rows only described
Notion pages that were deleted by hand, so they are dropped rather than converted.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-26 08:40:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_tables() -> None:
    with op.batch_alter_table("web_articles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_articles_import_id"))

    op.drop_table("web_articles")
    with op.batch_alter_table("web_imports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_imports_user_id"))

    op.drop_table("web_imports")


def upgrade() -> None:
    _drop_tables()
    op.create_table(
        "web_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("index_url", sa.String(length=1024), nullable=False),
        sa.Column("target_page_id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("section_pages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_web_imports_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_imports")),
        sa.UniqueConstraint("user_id", "index_url", name=op.f("uq_web_imports_user_id_index_url")),
    )
    with op.batch_alter_table("web_imports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_imports_user_id"), ["user_id"], unique=False)

    op.create_table(
        "web_articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("section", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("notion_page_id", sa.String(length=64), nullable=True),
        sa.Column("source_modified", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=200), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("cache_path", sa.String(length=1024), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["web_imports.id"],
            name=op.f("fk_web_articles_import_id_web_imports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_articles")),
        sa.UniqueConstraint("import_id", "url", name=op.f("uq_web_articles_import_id_url")),
    )
    with op.batch_alter_table("web_articles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_articles_import_id"), ["import_id"], unique=False)


def downgrade() -> None:
    _drop_tables()
    op.create_table(
        "web_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("index_url", sa.String(length=1024), nullable=False),
        sa.Column("target_page_id", sa.String(length=64), nullable=False),
        sa.Column("section_pages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_web_imports_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_imports")),
        sa.UniqueConstraint("user_id", "index_url", name=op.f("uq_web_imports_user_id_index_url")),
    )
    with op.batch_alter_table("web_imports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_imports_user_id"), ["user_id"], unique=False)

    op.create_table(
        "web_articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("section", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notes_page_id", sa.String(length=64), nullable=True),
        sa.Column("original_page_id", sa.String(length=64), nullable=True),
        sa.Column("status_block_id", sa.String(length=64), nullable=True),
        sa.Column("checkbox_block_id", sa.String(length=64), nullable=True),
        sa.Column("source_modified", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=200), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("pending", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["web_imports.id"],
            name=op.f("fk_web_articles_import_id_web_imports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_articles")),
        sa.UniqueConstraint("import_id", "url", name=op.f("uq_web_articles_import_id_url")),
    )
    with op.batch_alter_table("web_articles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_articles_import_id"), ["import_id"], unique=False)
