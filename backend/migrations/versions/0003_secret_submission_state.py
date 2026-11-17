"""Persistent credential revisions and tombstones; no secret plaintext."""

import sqlalchemy as sa
from alembic import op

revision = "0003_secret_submission_state"
down_revision = "0002_agent_foundations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credential_states",
        sa.Column("reference", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="credential_revision_positive"),
    )
    # Existing model ciphertext is untouched. Connector credentials keep their old lifecycle.
    op.execute(
        sa.text("""
        INSERT INTO credential_states (reference, revision, present, updated_at)
        SELECT reference, 1, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM secrets WHERE reference LIKE 'llm:%'
    """)
    )


def downgrade():
    raise RuntimeError("Destructive downgrade is not supported; restore a verified backup.")
