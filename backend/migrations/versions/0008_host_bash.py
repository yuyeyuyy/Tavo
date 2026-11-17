"""Separate host execution capability from historical Docker commands."""

import sqlalchemy as sa
from alembic import op

revision = "0008_host_bash"
down_revision = "0007_sandbox_execution_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversation_sessions",
        sa.Column("execution_scope", sa.String(), nullable=False, server_default="sandbox_only"),
    )
    op.create_table(
        "host_executions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "tool_call_id",
            sa.String(),
            sa.ForeignKey("assistant_tool_calls.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("run_id", sa.String(), sa.ForeignKey("assistant_runs.id"), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("worker_owner", sa.String(), nullable=False),
        sa.Column("worker_fence", sa.Integer(), nullable=False),
        sa.Column("pid", sa.Integer()),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("finished_at", sa.String()),
        sa.Column("error_code", sa.String()),
    )
    op.create_index(
        "host_single_live_slot",
        "host_executions",
        ["slot"],
        unique=True,
        sqlite_where=sa.text("state != 'finished'"),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup; destructive downgrade is unsupported.")
