"""Persistent model profiles and one-time legacy configuration import state."""

import sqlalchemy as sa
from alembic import op

revision = "0004_client_model_profiles"
down_revision = "0003_secret_submission_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint("max_output_tokens BETWEEN 128 AND 8192", name="model_output_budget"),
    )
    op.create_table(
        "model_catalog_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("default_profile_id", sa.String(), sa.ForeignKey("model_profiles.id")),
        sa.CheckConstraint("id = 1", name="model_catalog_singleton"),
    )


def downgrade():
    raise RuntimeError("Destructive downgrade is not supported; restore a verified backup.")
