"""Trusted host coordinator. Vendor credentials cross only a private Docker stdin channel."""

import base64
import json
import time

from sqlalchemy import select

from app.agent.api_credentials import ApiKeys
from app.agent.bash import bash_result
from app.core.errors import AppError, Code
from app.core.private_state import PrivateStateStore
from app.models.entities import AssistantRun, SandboxInstance, SandboxNetworkRequest
from app.repositories.sessions import require_leader, require_scope


def binding(row, purpose="request"):
    return {"purpose": "sandbox-network-" + purpose, "runId": row.run_id, "requestId": row.id}


class EgressCoordinator:
    def __init__(self, manager, secrets):
        self.manager = manager
        self.keys = ApiKeys(secrets, manager.policy)
        self.private = PrivateStateStore(secrets)

    def configuration(self, run_id):
        if run_id is None:
            policy = self.manager.policy.network.model_dump()
            policy.update(mode="none", targets=[], packages=[])
            return {
                "policy": policy,
                "credentials": {},
                "expires": time.time() + 60,
                "usage": {"count": 0, "timestamps": []},
            }
        policy = self.network_for_run(run_id)
        credentials = {}
        for target in self.manager.policy.network.targets:
            if target.model_dump() not in policy["targets"]:
                continue
            if target.credential and self.keys.ready(target):
                credentials[target.id] = self.keys.get(target).get_secret_value()
        with self.manager.db.session() as db:
            previous = list(
                db.scalars(
                    select(SandboxInstance)
                    .where(SandboxInstance.run_id == run_id)
                    .order_by(SandboxInstance.created_at)
                )
            )
            count = sum(max(row.network_count, row.network_reservation) for row in previous)
            timestamps = next(
                (row.network_window for row in reversed(previous) if row.network_window), []
            )
        return {
            "usage": {"count": count, "timestamps": timestamps},
            "policy": policy,
            "credentials": credentials,
            "expires": time.time() + self.manager.policy.max_active_seconds,
        }

    def network_for_run(self, run_id):
        current = self.manager.policy.network.model_dump()
        with self.manager.db.session() as db:
            run = db.get(AssistantRun, run_id)
            frozen = (run.context or {}).get("sandboxPolicy", {}).get("network", {})
            legacy = (run.context or {}).get("policyVersion") not in {"4", "5"}
        current["targets"] = [
            target for target in current["targets"] if target in frozen.get("targets", [])
        ]
        current["packages"] = [
            package for package in current["packages"] if package in frozen.get("packages", [])
        ]
        current["mode"] = "proxy" if current["mode"] == frozen.get("mode") == "proxy" else "none"
        current["allow_public_https"] = current["allow_public_https"] and frozen.get(
            "allow_public_https", False
        )
        current["require_write_approval"] = current["require_write_approval"] or frozen.get(
            "require_write_approval", True
        )
        if legacy:
            current.update(allow_public_https=False, require_write_approval=True)
        if current["dns_mode"] != frozen.get("dns_mode", "system"):
            current["mode"] = "none"
        current["protected_cidrs"] = list(
            dict.fromkeys(current["protected_cidrs"] + frozen.get("protected_cidrs", []))
        )
        for name in (
            "request_seconds",
            "request_bytes",
            "response_bytes",
            "requests_per_run",
            "requests_per_minute",
            "redirects",
            "download_bytes",
            "install_bytes",
            "install_files",
        ):
            current[name] = min(current[name], frozen.get(name, current[name]))
        return current

    def reserve(self, row, configuration):
        with self.manager.db.session(write=True) as db:
            db.get(SandboxInstance, row.id).network_reservation = max(
                0, configuration["policy"]["requests_per_run"] - configuration["usage"]["count"]
            )

    def store_usage(self, row, value, *, release=False):
        with self.manager.db.session(write=True) as db:
            other = sum(
                max(item.network_count, item.network_reservation)
                for item in db.scalars(
                    select(SandboxInstance).where(
                        SandboxInstance.run_id == row.run_id, SandboxInstance.id != row.id
                    )
                )
            )
            current = db.get(SandboxInstance, row.id)
            current.network_count = max(current.network_count, max(0, value["count"] - other))
            current.network_window = value["timestamps"][
                -self.manager.policy.network.requests_per_minute :
            ]
            if release:
                current.network_reservation = 0

    async def restore_results(self, row):
        with self.manager.db.session() as db:
            requests = list(
                db.scalars(
                    select(SandboxNetworkRequest).where(
                        SandboxNetworkRequest.run_id == row.run_id,
                        SandboxNetworkRequest.state == "completed",
                    )
                )
            )
        for request in requests:
            result = self.private.open(binding(request, "result"), request.result_ciphertext)
            await self.manager.control(
                row, {"action": "result", "id": request.id, "result": result}
            )

    async def collect(self, scope, call_id, row):
        if not row.gateway_name:
            return
        await self.save_usage(scope, row)
        pending = await self.manager.control(row, {"action": "pending"})
        if not isinstance(pending, list) or len(pending) > 8:
            raise AppError(Code.SANDBOX_ERROR)
        from sandbox.runtime.tavo_sandbox.network import digest, normalize_request

        for item in pending:
            request = normalize_request(item["request"], self.network_for_run(scope.run_id))
            if (
                item["digest"] != digest(request)
                or len(item["id"]) != 32
                or any(c not in "0123456789abcdef" for c in item["id"])
            ):
                raise AppError(Code.POLICY_DENIED)
            record = SandboxNetworkRequest(
                id=item["id"],
                run_id=scope.run_id,
                parent_call_id=call_id,
                target=request["target"],
                method=request["method"],
                request_digest=item["digest"],
                state="pending",
                created_at=self.manager.now(),
            )
            record.key_id, record.ciphertext = self.private.seal(binding(record), request)
            with self.manager.db.session(write=True) as db:
                require_scope(db, scope, self.manager.now())
                if not db.get(SandboxNetworkRequest, record.id):
                    db.add(record)

    async def save_usage(self, scope, row):
        value = await self.manager.control(row, {"action": "stats"})
        with self.manager.db.session(write=True) as db:
            require_scope(db, scope, self.manager.now())
        self.store_usage(row, value)

    async def execute(self, scope, call_id, request_id):
        async with self.manager.serialize():
            return await self._execute(scope, call_id, request_id)

    async def _execute(self, scope, call_id, request_id):
        from app.models.entities import AssistantReview

        requires_approval = self.network_for_run(scope.run_id)["require_write_approval"]
        with self.manager.db.session(write=True) as db:
            require_scope(db, scope, self.manager.now())
            row = db.get(SandboxNetworkRequest, request_id)
            if not row or row.run_id != scope.run_id or row.tool_call_id != call_id:
                raise AppError(Code.POLICY_DENIED)
            if row.state == "completed":
                result = self.private.open(binding(row, "result"), row.result_ciphertext)
                return self.public(result, None)
            if row.state in {"inflight", "unknown"}:
                raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
            approved = db.scalar(
                select(AssistantReview.id).where(
                    AssistantReview.tool_call_id == call_id,
                    AssistantReview.source == "human",
                    AssistantReview.action == "allow",
                )
            )
            if requires_approval and not approved:
                raise AppError(Code.APPROVAL_REQUIRED)
            request = self.private.open(binding(row), row.ciphertext)
            # Durable intent before any external effect. Recovery must never re-send it.
        instance = await self.manager.acquire(scope)
        with self.manager.db.session(write=True) as db:
            require_scope(db, scope, self.manager.now())
            db.get(SandboxNetworkRequest, request_id).state = "inflight"
        started = time.monotonic()
        try:
            result = await self.manager.control(
                instance,
                {"action": "execute", "request": request},
                timeout=self.manager.policy.network.request_seconds + 5,
            )
        except BaseException as failure:
            with self.manager.db.session(write=True) as db:
                try:
                    require_leader(db, scope.owner, scope.fence, self.manager.now())
                    db.get(SandboxNetworkRequest, request_id).state = "unknown"
                except AppError as expired:
                    if expired.code != Code.LEASE_LOST:
                        raise
            import asyncio

            if isinstance(failure, asyncio.CancelledError):
                raise
            raise AppError(Code.TOOL_EXECUTION_UNKNOWN) from None
        with self.manager.db.session(write=True) as db:
            require_scope(db, scope, self.manager.now())
            row = db.get(SandboxNetworkRequest, request_id)
            row.result_key_id, row.result_ciphertext = self.private.seal(
                binding(row, "result"), result, max_bytes=4194304
            )
            row.state = "completed"
            row.error_code = result.get("errorCode")
        await self.manager.control(
            instance, {"action": "result", "id": request_id, "result": result}
        )
        await self.save_usage(scope, instance)
        return self.public(result, instance.id, int((time.monotonic() - started) * 1000))

    def public(self, result, sandbox_id, duration=0):
        error = Code(result["errorCode"]) if result.get("errorCode") else None
        raw = base64.b64decode(result.get("body", ""))
        text = json.dumps(
            {
                "httpStatus": result.get("status"),
                "body": raw[:1024].decode("utf-8", "replace"),
                "fullResult": "Use tavo_http.result(requestId) inside the task; never rerun a previously approved external operation.",
            },
            ensure_ascii=False,
        )
        return bash_result(
            error,
            stdout=text,
            duration_ms=duration,
            sandbox_id=sandbox_id,
            truncated=len(raw) > 1024,
        ), str(error) if error else None
