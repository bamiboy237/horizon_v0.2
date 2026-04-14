"""Change opportunities embedding dimension to 1536.

Revision ID: 20260405_0002
Revises: 20260405_0001
Create Date: 2026-04-05 23:00:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260405_0002"
down_revision: str | None = "20260405_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_opp_embedding;")
    op.execute(
        "ALTER TABLE opportunities ALTER COLUMN embedding TYPE vector(1536) USING NULL::vector(1536);"
    )
    op.execute(
        "CREATE INDEX idx_opp_embedding ON opportunities USING hnsw (embedding vector_cosine_ops);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_opp_embedding;")
    op.execute(
        "ALTER TABLE opportunities ALTER COLUMN embedding TYPE vector(768) USING NULL::vector(768);"
    )
    op.execute(
        "CREATE INDEX idx_opp_embedding ON opportunities USING hnsw (embedding vector_cosine_ops);"
    )
