import json
import os
from dataclasses import replace

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.agent.tools import ToolRegistry
from app.core.sandbox_config import NetworkTarget, SandboxPolicy
from app.models.entities import SandboxInstance, SandboxNetworkRequest
from tests.test_bash_sandbox import result
from tests.test_bash_sandbox import sandbox as original_sandbox
from tests.test_sandbox_development_docker import Sequence
from tests.test_sessions import drive, get_run, send

pytestmark = pytest.mark.skipif(
    os.environ.get("TAVO_TEST_DOCKER") != "1", reason="Explicit real controlled egress tests"
)
SIX = {
    "name": "six",
    "version": "1.17.0",
    "url": "https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl",
    "sha256": "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274",
}


@pytest.fixture
def gateway(f):
    fixture = original_sandbox.__wrapped__(f)
    _, create = next(fixture)

    def configure(targets, *, packages=None, **limits):
        data = f.services.settings.sandbox.model_dump()
        data["network"].update(
            mode="proxy",
            dns_mode="cloudflare-doh",
            targets=targets,
            packages=packages or [],
            **limits,
        )
        policy = SandboxPolicy.model_validate(data)
        settings = replace(f.services.settings, sandbox=policy)
        f.services.settings = settings
        f.services.bash.settings = settings
        f.services.bash.manager.policy = policy
        f.services.bash.manager.egress.keys.policy = policy
        registry = ToolRegistry(bash_enabled=True, policy=policy)
        f.services.assistant.registry = registry
        f.services.assistant.context.registry = registry
        f.services.assistant.reviewer.registry = registry

    yield f, create, configure
    try:
        next(fixture)
    except StopIteration:
        pass


def test_real_public_https_through_gateway_direct_and_unauthorized_access_denied(gateway):
    f, create, configure = gateway
    configure(
        [
            NetworkTarget(
                id="pypi", hostname="pypi.org", paths=["/pypi/six/1.17.0/json"]
            ).model_dump()
        ]
    )
    s = create()
    code = """python - <<'PY'
import tavo_http, socket
r=tavo_http.request('pypi',path='/pypi/six/1.17.0/json')
assert r.status_code==200 and r.json()['info']['name']=='six'
try:
 tavo_http.request('not-authorized')
 raise AssertionError('unknown target allowed')
except tavo_http.EgressError as e: assert e.code=='NETWORK_DENIED'
try:
 socket.create_connection(('1.1.1.1',443),.2)
 raise AssertionError('direct connection allowed')
except OSError:pass
print('public-https-and-denials-ok')
PY"""
    send(f, s, "bash: " + code)
    drive(f)
    assert result(f, s)["output"]["stdout"] == "public-https-and-denials-ok\n", result(f, s)


def test_real_public_url_without_alias_and_full_access_bulk_commit(gateway):
    f, create, configure = gateway
    configure([])
    s = create("full_access")
    code = """python - <<'PY'
import tavo_http, socket
from pathlib import Path
r=tavo_http.request('https://pypi.org/pypi/six/1.17.0/json')
assert r.status_code==200 and r.json()['info']['name']=='six'
for i in range(101):Path(f'note-{i}').write_text('task')
try:
 socket.create_connection(('1.1.1.1',443),.2)
 raise AssertionError('direct network allowed')
except OSError:pass
print('public-url-and-bulk-commit-ok')
PY"""
    accepted = send(f, s, "bash: " + code)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded", run
    assert result(f, s)["output"]["stdout"] == "public-url-and-bulk-commit-ok\n", result(f, s)
    assert all(d["source"] == "policy" for d in run["toolCalls"][0]["decisions"])
    current = f.client.get(f"/api/v1/sessions/{s['id']}").json()
    send(f, current, "bash: test -f note-100 && printf persisted", "next")
    drive(f)
    assert result(f, s)["output"]["stdout"] == "persisted", result(f, s)
    import asyncio

    with f.services.db.session() as db:
        rows = list(db.scalars(select(SandboxInstance).where(SandboxInstance.run_id.is_not(None))))
    for row in rows:
        assert asyncio.run(f.services.bash.manager.close_instance(row))


def test_default_gateway_fetches_deepseek_docs_without_manual_dns_override(gateway):
    f, create, _ = gateway
    assert f.services.bash.manager.policy.network.dns_mode == "cloudflare-doh"
    s = create("full_access")
    accepted = send(
        f,
        s,
        "bash: python -c \"import tavo_http; r=tavo_http.request('https://api-docs.deepseek.com'); assert r.status_code==200; print('deepseek-docs-http-200')\"",
    )
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded", run
    assert result(f, s)["output"]["stdout"] == "deepseek-docs-http-200\n", result(f, s)


def test_real_pinned_wheel_install_stays_in_task_and_survives_next_run(gateway):
    f, create, configure = gateway
    configure(
        [
            NetworkTarget(
                id="wheel-files",
                hostname="files.pythonhosted.org",
                paths=["/packages"],
                capability="package_download",
            ).model_dump()
        ],
        packages=[SIX],
    )
    s = create()
    send(
        f,
        s,
        'bash: python -m tavo_pip install six==1.17.0 && python -c "import six; print(six.__version__)"',
    )
    drive(f)
    assert result(f, s)["status"] == "succeeded" and "1.17.0" in result(f, s)["output"]["stdout"], (
        result(f, s)
    )
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    send(f, s, 'bash: python -c "import six; print(six.__version__)"', "reuse")
    drive(f)
    assert result(f, s)["output"]["stdout"] == "1.17.0\n", result(f, s)


@pytest.mark.parametrize("mode", ["auto_sandbox", "full_access"])
def test_sensitive_http_approval_executes_only_exact_request_and_no_script_replay(
    gateway, monkeypatch, mode
):
    f, create, configure = gateway
    target = NetworkTarget(
        id="fixture-api",
        hostname="api.example.test",
        paths=["/v1"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        capability="api_test",
        credential=True,
    )
    configure([target.model_dump()])
    keys = f.services.bash.manager.egress.keys
    keys.put(target, SecretStr("synthetic-egress-token"))
    # Test-only process injection. Production has no environment/Renderer switch for a fake
    # resolver or transport. Real public TLS egress is covered by the separate test above.
    bootstrap = """import httpx,socket
from tavo_sandbox import network
base=network.Outbound
def answer(request):
 if request.headers['host']=='api.example.test':
  assert request.headers['Authorization']=='Bearer synthetic-egress-token'
 else: assert 'Authorization' not in request.headers
 return httpx.Response(200,json={'value':42,'authenticated':True})
def resolver(*a,**k):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
class FixtureOutbound(base):
 def __init__(self,*a,**k):
  k.update(resolver=resolver,transport=httpx.MockTransport(answer));super().__init__(*a,**k)
network.Outbound=FixtureOutbound
"""
    original = f.services.bash.cli

    async def fixture_cli(*args, **kwargs):
        values = list(args)
        if len(values) >= 4 and values[-4:] == ["python", "-I", "-m", "tavo_sandbox.egress"]:
            values[-4:] = [
                "python",
                "-I",
                "-c",
                bootstrap + "\nfrom tavo_sandbox.egress import main;main()",
            ]
        if len(values) >= 4 and values[-4:] == ["python", "-I", "-m", "tavo_sandbox.control"]:
            values[-4:] = [
                "python",
                "-I",
                "-c",
                bootstrap + "\nfrom tavo_sandbox.control import main;main()",
            ]
        return await original(*values, **kwargs)

    monkeypatch.setattr(f.services.bash, "cli", fixture_cli)
    code = "python - <<'PY'\nfrom pathlib import Path\nimport tavo_http,os\np=Path('counter');p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nassert 'API_TOKEN' not in os.environ\ntavo_http.request('fixture-api',method='POST',path='/v1/test',json={'value':42})\nPY"
    model = Sequence([("bash", {"command": code})])
    f.services.assistant.mock = model
    if mode == "full_access":
        code = (
            code.removesuffix("\nPY")
            + "\nfor method in ['PUT','PATCH','DELETE']:\n assert tavo_http.request('fixture-api',method=method,path='/v1/test',json={'value':42}).json()['value']==42\nfor method in ['POST','PUT','PATCH','DELETE']:\n assert tavo_http.request('https://public.example.test/v1/test',method=method,json={'value':42}).status_code==200\nprint(p.read_text())\nPY"
        )
        model = Sequence([("bash", {"command": code})])
        f.services.assistant.mock = model
    s = create(mode)
    accepted = send(f, s, "Test one authorized API operation.")
    drive(f)
    run = get_run(f, s, accepted)
    if mode == "full_access":
        assert run["status"] == "succeeded", run
        assert len(run["toolCalls"]) == 1 and run["toolCalls"][0]["attempts"] == 1
        assert all(d["source"] == "policy" for d in run["toolCalls"][0]["decisions"])
        assert result(f, s)["output"]["stdout"] == "1\n", result(f, s)
        with f.services.db.session() as db:
            assert not list(db.scalars(select(SandboxNetworkRequest)))
            assert sum(db.scalars(select(SandboxInstance.network_count))) == 8
        return
    assert run["status"] == "waiting_review", run
    child = next(c for c in run["toolCalls"] if c["name"] == "http_request")
    preview = f.client.get(f"/api/v1/sessions/{s['id']}/tool-calls/{child['id']}/approval").json()
    assert preview["details"]["hostname"] == "api.example.test"
    assert preview["details"]["body"] == '{"value": 42}'
    assert "synthetic-egress-token" not in json.dumps(preview)
    request_id = child["arguments"]["requestId"]
    model.calls = iter(
        [
            (
                "bash",
                {
                    "command": f"python -c \"import tavo_http; assert tavo_http.result('{request_id}').json()['value']==42; print(open('counter').read())\""
                },
            )
        ]
    )
    approved = f.client.post(
        f"/api/v1/sessions/{s['id']}/tool-calls/{child['id']}/decision",
        json={"action": "approve", "expectedRevision": child["reviewRevision"]},
    )
    assert approved.status_code == 200
    drive(f)
    final = get_run(f, s, accepted)
    assert final["status"] == "succeeded", final
    assert result(f, s)["output"]["stdout"] == "1\n", result(f, s)
    assert final["toolCalls"][0]["attempts"] == 1
    with f.services.db.session() as db:
        request = db.get(SandboxNetworkRequest, request_id)
        assert request.state == "completed" and request.result_ciphertext
        assert sum(db.scalars(select(SandboxInstance.network_count))) == 1
