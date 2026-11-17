"""Trusted Docker-exec control channel; never mounted or served to sandbox scripts."""

import fcntl
import json
import os
import sys
from pathlib import Path

from tavo_sandbox.network import NetworkFailure, Outbound

ROOT = Path("/run/private")


def main():
    raw = sys.stdin.buffer.read(4194305)
    if len(raw) > 4194304:
        raise SystemExit(1)
    value = json.loads(raw)
    action = value["action"]
    if action == "configure":
        target = ROOT / "config.json"
        with target.open("w") as stream:
            os.chmod(target, 0o600)
            json.dump(value["config"], stream)
        print('{"ok":true}')
    elif action == "pending":
        print(
            json.dumps(
                [json.loads(p.read_bytes()) for p in sorted((ROOT / "pending").glob("*.json"))]
            )
        )
    elif action == "begin":
        (ROOT / "errors.json").write_text("[]")
        print('{"ok":true}')
    elif action == "errors":
        path = ROOT / "errors.json"
        print(path.read_text() if path.is_file() else "[]")
    elif action in {"stats", "seal"}:
        if action == "seal":
            lock = (ROOT / "request.lock").open("a")
            fcntl.flock(lock, fcntl.LOCK_EX)
            (ROOT / "sealed").touch()
        path = ROOT / "stats.json"
        config = json.loads((ROOT / "config.json").read_bytes())
        print(
            path.read_text()
            if path.is_file()
            else json.dumps(config.get("usage", {"count": 0, "timestamps": []}))
        )
    elif action == "result":
        key = value["id"]
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise SystemExit(1)
        target = ROOT / "results"
        target.mkdir(exist_ok=True, mode=0o700)
        with (target / (key + ".json")).open("w") as stream:
            os.chmod(stream.name, 0o600)
            json.dump(value["result"], stream)
        print('{"ok":true}')
    elif action == "execute":
        config = json.loads((ROOT / "config.json").read_bytes())
        outbound = Outbound(config["policy"], config.get("credentials"))
        stats = ROOT / "stats.json"
        outbound.usage_read = lambda: (
            json.loads(stats.read_bytes())
            if stats.is_file()
            else config.get("usage", {"count": 0, "timestamps": []})
        )

        def store_usage(value):
            from tavo_sandbox.egress import atomic_json

            atomic_json(stats, value)

        outbound.usage_write = store_usage
        try:
            result = outbound.fetch(value["request"], approved=True)
            print(json.dumps(result))
        except NetworkFailure as error:
            print(json.dumps({"errorCode": error.code}))
        finally:
            outbound.close()
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
