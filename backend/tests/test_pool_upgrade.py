import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.core.private_state import PrivateStateStore
from app.core.security import SecretStore, initialize_keys
from app.repositories.database import Database


@pytest.mark.parametrize("start_revision", ["0005_session_agent_core", "0006_agent_pool_bash"])
def test_upgrade_preserves_legacy_context_cipher_and_approval(tmp_path, start_revision):
    settings = Settings(
        bash_enabled=False,
        data_dir=tmp_path,
        keyring_file=tmp_path / "master.keys",
        client_token_file=tmp_path / "client.token",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database}")
    command.upgrade(config, start_revision)
    initialize_keys(settings)
    db = Database(settings.database)
    private = PrivateStateStore(SecretStore(db, settings))
    binding = {"runId": "old-run", "sessionId": "old-session", "round": 1, "profile": None}
    key, cipher = private.seal(binding, {"native": "private-old-continuation"})
    now = "2026-11-16T00:00:00.000Z"
    with sqlite3.connect(settings.database) as sql:
        sql.execute(
            "INSERT INTO conversation_sessions (id,title,profile_id,review_mode,revision,next_sequence,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("old-session", "Old", "mock", "ask", 2, 2, now, now),
        )
        sql.execute(
            "INSERT INTO assistant_runs (id,session_id,user_message_id,status,step,rounds,tool_count,active_ms,revision,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("old-run", "old-session", "old-user", "waiting_review", "tools", 1, 1, 10, 1, now),
        )
        sql.execute(
            "INSERT INTO assistant_model_turns (id,run_id,round,status,ciphertext,key_id,created_at) VALUES (?,?,?,?,?,?,?)",
            ("old-turn", "old-run", 1, "completed", cipher, key, now),
        )
        sql.execute(
            "INSERT INTO assistant_tool_calls (id,run_id,round,ordinal,provider_call_id,name,arguments,argument_digest,status,review_revision,attempts,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old-call",
                "old-run",
                1,
                0,
                "p",
                "calculate",
                json.dumps({}),
                "digest",
                "waiting_review",
                1,
                0,
                now,
            ),
        )
        sql.execute(
            "INSERT INTO assistant_reviews (id,tool_call_id,revision,action,source,reason_code,policy_version,argument_digest,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "old-review",
                "old-call",
                1,
                "ask",
                "policy",
                "USER_CONFIRMATION_REQUIRED",
                "1",
                "digest",
                now,
            ),
        )
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database) as sql:
        assert sql.execute(
            "SELECT bash_enabled,workspace_unconfirmed FROM conversation_sessions"
        ).fetchone() == (0, 0)
        row = sql.execute("SELECT purpose,ciphertext FROM assistant_model_turns").fetchone()
        assert row == ("assistant", cipher)
        assert sql.execute(
            "SELECT action,source,policy_version,summary FROM assistant_reviews"
        ).fetchone() == ("ask", "policy", "1", None)
        assert sql.execute("PRAGMA foreign_key_check").fetchall() == []
    assert private.open(binding, cipher) == {"native": "private-old-continuation"}
    command.check(config)
    db.close()
