import argparse
import asyncio
import json
import signal
from dataclasses import replace
from pathlib import Path

import uvicorn

from app.core.config import Settings
from app.core.errors import MESSAGES, AppError, Code
from app.core.security import SecretStore, initialize_keys
from app.main import create_app
from app.repositories.database import Database, upgrade_database
from app.services.application import Services
from app.services.migration import import_legacy
from app.workers.scheduler import Scheduler


async def run_worker(settings):
    services = Services(settings)
    await services.initialize()
    worker = Scheduler(services)
    worker.start()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        await stopped.wait()
    finally:
        await worker.close()
        await services.close()


def main():
    parser = argparse.ArgumentParser(
        description="Tavo independent backend; secrets are never printed."
    )
    parser.add_argument(
        "command", choices=["init", "serve", "worker", "import-legacy", "rotate-keys"]
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-worker", action="store_true")
    parser.add_argument("--container-local", action="store_true")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.data_dir:
        old = settings.data_dir
        directory = args.data_dir.resolve()
        settings = replace(
            settings,
            data_dir=directory,
            keyring_file=directory / "master.keys"
            if settings.keyring_file.parent == old
            else settings.keyring_file,
            client_token_file=directory / "client.token"
            if settings.client_token_file.parent == old
            else settings.client_token_file,
        )
    try:
        if args.command != "init" and not settings.database.is_file():
            raise AppError(Code.STORAGE_FAILED)
        if args.command == "init":
            upgrade_database(settings.database)
            initialize_keys(settings)
            print("Backend schema and private key files initialized; no secrets printed.")
        elif args.command == "serve":
            if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.container_local:
                parser.error(
                    "Non-loopback binding requires explicit container-local mode; public HTTPS deployment is not provided."
                )
            initialize_keys(settings)
            settings = replace(settings, worker_enabled=not args.no_worker)
            uvicorn.run(
                create_app(settings),
                host=args.host,
                port=args.port,
                access_log=False,
                log_level="warning",
                proxy_headers=False,
            )
        elif args.command == "worker":
            asyncio.run(run_worker(settings))
        elif args.command == "rotate-keys":
            database = Database(settings.database)
            try:
                SecretStore(database, settings).rotate()
            finally:
                database.close()
            print("Keyring rotated; old keys retained for recovery. No key material printed.")
        elif args.command == "import-legacy":
            if not args.source:
                parser.error("--source is required")
            database = Database(settings.database)
            try:
                report = import_legacy(database, args.source, settings.data_dir / "backups")
            finally:
                database.close()
            print(json.dumps(report, ensure_ascii=False))
    except AppError as error:
        parser.exit(1, f"{error.code}: {MESSAGES[error.code]}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
