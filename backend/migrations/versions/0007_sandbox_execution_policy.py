"""Scoped reusable sandboxes, exact-operation approvals, and execution audit."""

import sqlalchemy as sa
from alembic import op

revision = "0007_sandbox_execution_policy"
down_revision = "0006_agent_pool_bash"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sandbox_workspace_checkpoints",
        sa.Column(
            "session_id", sa.String(), sa.ForeignKey("conversation_sessions.id"), primary_key=True
        ),
        sa.Column("revision", sa.String()),
        sa.Column("retained_revisions", sa.JSON(), nullable=False),
    )
    op.create_table(
        "sandbox_instances",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("assistant_runs.id")),
        sa.Column("session_id", sa.String(), sa.ForeignKey("conversation_sessions.id")),
        sa.Column("container_name", sa.String(), nullable=False, unique=True),
        sa.Column("work_volume", sa.String()),
        sa.Column("socket_volume", sa.String()),
        sa.Column("gateway_name", sa.String()),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("worker_owner", sa.String(), nullable=False),
        sa.Column("worker_fence", sa.Integer(), nullable=False),
        sa.Column("baseline_revision", sa.String()),
        sa.Column("resource_profile", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.String(), nullable=False),
        sa.Column("error_code", sa.String()),
        sa.Column("network_count", sa.Integer(), nullable=False),
        sa.Column("network_reservation", sa.Integer(), nullable=False),
        sa.Column("network_window", sa.JSON(), nullable=False),
    )
    op.create_index(
        "sandbox_single_live_slot",
        "sandbox_instances",
        ["slot"],
        unique=True,
        sqlite_where=sa.text("state != 'closed'"),
    )
    op.create_table(
        "sandbox_operations",
        sa.Column(
            "tool_call_id", sa.String(), sa.ForeignKey("assistant_tool_calls.id"), primary_key=True
        ),
        sa.Column("sandbox_id", sa.String(), sa.ForeignKey("sandbox_instances.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("argument_digest", sa.String(), nullable=False),
        sa.Column("approval_digest", sa.String()),
        sa.Column("approval_detail", sa.JSON()),
        sa.Column("output", sa.JSON()),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("finished_at", sa.String()),
    )
    op.create_table(
        "sandbox_network_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("assistant_runs.id"), nullable=False),
        sa.Column(
            "parent_call_id", sa.String(), sa.ForeignKey("assistant_tool_calls.id"), nullable=False
        ),
        sa.Column(
            "tool_call_id", sa.String(), sa.ForeignKey("assistant_tool_calls.id"), unique=True
        ),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("key_id", sa.String(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("result_key_id", sa.String()),
        sa.Column("result_ciphertext", sa.LargeBinary()),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("error_code", sa.String()),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup; destructive downgrade is unsupported.")
