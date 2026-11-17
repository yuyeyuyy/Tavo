"""Structured monitoring memory and bounded Agent execution records."""

import sqlalchemy as sa
from alembic import op

revision = "0002_agent_foundations"
down_revision = "0001_backend"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "account_id", sa.String(), sa.ForeignKey("account_connections.id"), nullable=False
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.String(), nullable=True),
        sa.UniqueConstraint("account_id", "kind"),
    )
    op.create_table(
        "agent_run_contexts",
        sa.Column("run_id", sa.String(), sa.ForeignKey("agent_runs.id"), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )
    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("evidence_references", sa.JSON(), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "step"),
    )


def downgrade():
    raise RuntimeError("Destructive downgrade is not supported; restore a verified backup.")
