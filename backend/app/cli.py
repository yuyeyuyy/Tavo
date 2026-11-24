import argparse
import asyncio
import getpass
import signal
import sys
import warnings
from dataclasses import replace
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from app.agent.tools.builtin.web import SearchKeys
from app.core.config import Settings
from app.core.errors import MESSAGES, AppError, Code
from app.core.security import SecretStore, initialize_keys
from app.core.startup import report_startup
from app.llm.keys import ModelKeys
from app.main import create_app
from app.repositories.database import Database, upgrade_database
from app.services.application import Services
from app.services.model_profiles import ModelProfileService
from app.workers.scheduler import Scheduler


async def run_worker(settings):
    services = Services(settings)
    worker = None
    try:
        await services.initialize()
        worker = Scheduler(services)
        worker.start()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopped.set)
        report_startup("独立 Worker 启动完成，可以领取任务。")
        await stopped.wait()
    finally:
        try:
            if worker is not None:
                await worker.close()
        finally:
            await services.close()


def main():
    parser = argparse.ArgumentParser(
        description="Tavo independent backend; secrets are never printed."
    )
    parser.add_argument(
        "command",
        choices=[
            "init",
            "serve",
            "worker",
            "import-legacy",
            "rotate-keys",
            "model-key",
            "api-key",
            "web-search-key",
        ],
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-worker", action="store_true")
    parser.add_argument("--container-local", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--target")
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
    except AppError as error:
        parser.exit(1, f"{error.code}: {MESSAGES[error.code]}\n")
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
            database = Database(settings.database)
            try:
                ModelProfileService(
                    database, SecretStore(database, settings), settings.models_file
                ).initialize()
            finally:
                database.close()
            print("Backend schema and private key files initialized; no secrets printed.")
        elif args.command in {"model-key", "api-key", "web-search-key"}:
            api_key = args.command == "api-key"
            search_key = args.command == "web-search-key"
            if not search_key and not (args.target if api_key else args.profile):
                parser.error("--target is required" if api_key else "--profile is required")
            database = Database(settings.database)
            try:
                store = SecretStore(database, settings)
                if search_key:
                    keys = SearchKeys(store)
                    profile = None
                else:
                    models = ModelProfileService(database, store, settings.models_file)
                    models.initialize()
                    if api_key:
                        from app.agent.api_credentials import ApiKeys

                        keys = ApiKeys(store, settings.sandbox)
                        profile = keys.target(args.target)
                    else:
                        profile = models.profile(args.profile, enabled=False)
                        keys = ModelKeys(store)
                label = "Brave Search" if search_key else "Model"
                if args.delete:
                    keys.remove(profile)
                    print(f"{label} key removed; no secrets printed.")
                else:
                    if not sys.stdin.isatty():
                        parser.error(
                            "Use an interactive terminal; keys are never accepted as arguments or piped input."
                        )
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", getpass.GetPassWarning)
                        try:
                            key = SecretStr(getpass.getpass(f"{label} key (hidden): "))
                        except (getpass.GetPassWarning, EOFError):
                            raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None
                    keys.put(profile, key)
                    print(f"{label} key encrypted and saved; no restart required.")
            finally:
                database.close()
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
                log_level="info",
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
            raise AppError(Code.FEATURE_REMOVED)
    except AppError as error:
        parser.exit(1, f"{error.code}: {MESSAGES[error.code]}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
