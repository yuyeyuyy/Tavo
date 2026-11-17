"""Bounded AgentPool, self-review and sandbox execution intents."""

import sqlalchemy as sa
from alembic import op

revision = "0006_agent_pool_bash"
down_revision = "0005_session_agent_core"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversation_sessions",
        sa.Column("bash_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("workspace_unconfirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("assistant_reviews", sa.Column("summary", sa.String(), nullable=True))
    with op.batch_alter_table(
        "assistant_model_turns",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s"},
    ) as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(), nullable=False, server_default="assistant")
        )
        batch.drop_constraint("uq_assistant_model_turns_run_id_round", type_="unique")
        batch.create_unique_constraint("model_turn_purpose_round", ["run_id", "purpose", "round"])
    op.create_table(
        "bash_executions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "tool_call_id",
            sa.String(),
            sa.ForeignKey("assistant_tool_calls.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "session_id", sa.String(), sa.ForeignKey("conversation_sessions.id"), nullable=False
        ),
        sa.Column("container_name", sa.String(), nullable=False, unique=True),
        sa.Column("container_id", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("worker_owner", sa.String(), nullable=False),
        sa.Column("worker_fence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup; destructive downgrade is unsupported.")
