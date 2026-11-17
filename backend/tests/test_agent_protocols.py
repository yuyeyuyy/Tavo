import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.errors import AppError
from app.core.private_state import PrivateStateStore
from app.llm.adapter import ModelGateway
from app.models.entities import AssistantModelTurn, AssistantToolCall
from tests.test_sessions import drive, get_run, send, session

PRIVATE = "synthetic-private-continuation"


def tool_response(protocol, name="calculate", arguments=None, final=False):
    args = {"operation": "divide", "left": "12", "right": "3"} if arguments is None else arguments
    if protocol == "responses":
        return {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "id": "reason-1",
                    "summary": [],
                    "encrypted_content": PRIVATE,
                },
                *(
                    [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Result is 4"}],
                        }
                    ]
                    if final
                    else [
                        {
                            "type": "function_call",
                            "id": "fc1",
                            "call_id": "provider-1",
                            "name": name,
                            "arguments": json.dumps(args),
                        }
                    ]
                ),
            ],
        }
    if protocol == "messages":
        return {
            "stop_reason": "end_turn" if final else "tool_use",
            "content": [
                {"type": "thinking", "thinking": PRIVATE, "signature": "signature-a"},
                *(
                    [{"type": "text", "text": "Result is 4"}]
                    if final
                    else [{"type": "tool_use", "id": "provider-1", "name": name, "input": args}]
                ),
            ],
        }
    if protocol == "gemini":
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": PRIVATE, "thought": True, "thoughtSignature": "signature-a"},
                            *(
                                [{"text": "Result is 4"}]
                                if final
                                else [
                                    {
                                        "functionCall": {
                                            "id": "provider-1",
                                            "name": name,
                                            "args": args,
                                        },
                                        "thoughtSignature": "signature-b",
                                    }
                                ]
                            ),
                        ],
                    },
                }
            ]
        }
    return {
        "choices": [
            {
                "finish_reason": "stop" if final else "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "Result is 4" if final else None,
                    "reasoning_content": PRIVATE,
                    "reasoning_details": [{"type": "reasoning.encrypted", "data": PRIVATE}],
                    **(
                        {}
                        if final
                        else {
                            "tool_calls": [
                                {
                                    "id": "provider-1",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ]
                        }
                    ),
                },
            }
        ]
    }


def configure(f, provider, respond, review_action="allow"):
    response = f.client.post(
        "/api/v1/model-profiles", json={"provider": provider, "model": "fixture-text"}
    )
    assert response.status_code == 201
    p = f.services.models.profile(response.json()["id"])
    f.services.chat.keys.put(p, SecretStr("synthetic-agent-model-key"))
    review_requests = []

    def transport(request):
        body = json.loads(request.content)
        from app.agent.self_review import REVIEW_SYSTEM

        if json.dumps(REVIEW_SYSTEM) in json.dumps(body) and review_action is not None:
            review_requests.append(body)
            assert not body.get("tools")
            protocol = p.protocol
            user = body.get("input", body.get("messages", body.get("contents")))[-1]
            text = user["parts"][0]["text"] if protocol == "gemini" else user["content"]
            payload = json.loads(text)
            decision = json.dumps(
                {
                    "decisions": [
                        {
                            "toolCallId": c["toolCallId"],
                            "action": review_action,
                            "reasonCode": "GOAL_ALIGNED",
                            "summary": "Reviewed fixture request",
                        }
                        for c in payload["calls"]
                    ]
                }
            )
            result = tool_response(protocol, final=True)
            if protocol == "responses":
                result["output"][-1]["content"][0]["text"] = decision
            elif protocol == "messages":
                result["content"][-1]["text"] = decision
            elif protocol == "gemini":
                result["candidates"][0]["content"]["parts"][-1]["text"] = decision
            else:
                result["choices"][0]["message"]["content"] = decision
            return httpx.Response(200, json=result)
        return respond(request)

    gateway = ModelGateway(httpx.MockTransport(transport))
    gateway.review_requests = review_requests
    f.services.chat.gateway = gateway
    f.services.assistant.gateway = gateway
    return p


@pytest.mark.parametrize(
    "provider",
    ["openai", "xai", "anthropic", "gemini", "deepseek", "qwen", "kimi", "zhipu", "openrouter"],
)
def test_real_adapter_tool_roundtrip_preserves_private_state_and_never_exposes_it(
    f, provider, caplog
):
    calls = []

    def respond(request):
        value = json.loads(request.content)
        calls.append(value)
        assert "synthetic-agent-model-key" not in request.content.decode()
        protocol = f.services.models.profile(profile.id).protocol
        if len(calls) == 1:
            assert "tools" in value and len(value["tools"]) > 0
            return httpx.Response(200, json=tool_response(protocol))
        serialized = json.dumps(value)
        assert PRIVATE in serialized
        if protocol == "responses":
            results = [
                item for item in value["input"] if item.get("type") == "function_call_output"
            ]
            assert results[-1]["call_id"] == "provider-1" and json.loads(results[-1]["output"])[
                "output"
            ] == {"result": "4"}
            assert value["store"] is False and value["include"] == ["reasoning.encrypted_content"]
        elif protocol == "messages":
            result = value["messages"][-1]["content"][0]
            assert result["tool_use_id"] == "provider-1" and not result["is_error"]
        elif protocol == "gemini":
            result = value["contents"][-1]["parts"][0]["functionResponse"]
            assert result["id"] == "provider-1" and result["response"]["output"] == {"result": "4"}
            assert value["contents"][-2]["parts"][1]["thoughtSignature"] == "signature-b"
        else:
            assert value["messages"][-1]["tool_call_id"] == "provider-1"
            assert value["messages"][-2]["reasoning_content"] == PRIVATE
        return httpx.Response(200, json=tool_response(protocol, final=True))

    profile = configure(f, provider, respond)
    s = session(f, profile.id)
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded", run
    public = (
        json.dumps(run) + f.client.get(f"/api/v1/sessions/{s['id']}/messages").text + caplog.text
    )
    assert (
        PRIVATE not in public
        and "signature-a" not in public
        and "synthetic-agent-model-key" not in public
    )
    with f.services.db.session() as db:
        row = db.scalar(select(AssistantModelTurn).where(AssistantModelTurn.run_id == run["id"]))
        assert PRIVATE.encode() not in row.ciphertext
        old = row.ciphertext
    f.services.secrets.rotate()
    with f.services.db.session() as db:
        assert db.get(AssistantModelTurn, row.id).ciphertext != old
    assert len(calls) == 2
    assert len(f.services.assistant.gateway.review_requests) == 1


@pytest.mark.parametrize(
    "name,args",
    [
        ("execute_shell", {}),
        ("calculate", {"operation": "divide", "left": "1", "right": "0"}),
        ("calculate", {"operation": "add", "left": "1", "right": "2", "sessionId": "other"}),
    ],
)
def test_denied_or_failed_tools_return_correlated_results_without_execution_privilege(
    f, name, args
):
    bodies = []

    def respond(request):
        value = json.loads(request.content)
        bodies.append(value)
        if len(bodies) == 1:
            return httpx.Response(200, json=tool_response("completions", name, args))
        result = json.loads(value["messages"][-1]["content"])
        assert result["status"] in ("denied", "failed") and result["output"] == {}
        return httpx.Response(200, json=tool_response("completions", final=True))

    p = configure(f, "deepseek", respond)
    s = session(f, p.id)
    accepted = send(f, s)
    drive(f)
    assert get_run(f, s, accepted)["status"] == "succeeded"
    assert len(bodies) == 2


def test_private_key_echo_and_nonstandard_json_fail_closed(f):
    p = configure(
        f,
        "deepseek",
        lambda _r: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "public",
                            "reasoning_content": "synthetic-agent-model-key",
                        },
                    }
                ]
            },
        ),
    )
    s = session(f, p.id)
    accepted = send(f, s)
    drive(f)
    assert get_run(f, s, accepted)["errorCode"] == "MODEL_RESPONSE_INVALID"
    f.services.assistant.gateway = ModelGateway(
        httpx.MockTransport(
            lambda _r: httpx.Response(
                200, content=b'{"choices":NaN}', headers={"content-type": "application/json"}
            )
        )
    )
    other = session(f, p.id)
    accepted = send(f, other)
    drive(f)
    assert get_run(f, other, accepted)["errorCode"] == "MODEL_RESPONSE_INVALID"


def test_waiting_review_cannot_be_approved_after_argument_mutation(f):
    s = session(f, mode="ask")
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    call = run["toolCalls"][0]
    with f.services.db.session(write=True) as db:
        row = db.get(AssistantToolCall, call["id"])
        row.arguments = {"operation": "multiply", "left": "2", "right": "5"}
    response = f.client.post(
        f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/decision",
        json={"action": "approve", "expectedRevision": 1},
    )
    assert response.status_code == 409


def test_encrypted_state_is_bound_to_run_and_round(f):
    store = PrivateStateStore(f.services.secrets)
    _, cipher = store.seal({"run": "a", "round": 1}, {"native": PRIVATE})
    assert store.open({"run": "a", "round": 1}, cipher) == {"native": PRIVATE}
    with pytest.raises(AppError, match="CREDENTIAL_UNAVAILABLE"):
        store.open({"run": "b", "round": 1}, cipher)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_id",
        "too_many",
        "truncated",
        "array_arguments",
        "duplicate_json_key",
        "oversized_arguments",
    ],
)
def test_malformed_tool_proposals_fail_closed_or_receive_denied_results(f, case):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) > 1:
            assert json.loads(body["messages"][-1]["content"])["status"] == "denied"
            return httpx.Response(200, json=tool_response("completions", final=True))
        raw = tool_response("completions")
        choice = raw["choices"][0]
        call = choice["message"]["tool_calls"][0]
        if case == "duplicate_id":
            choice["message"]["tool_calls"].append(call.copy())
        elif case == "too_many":
            choice["message"]["tool_calls"] = [{**call, "id": "call-" + str(i)} for i in range(9)]
        elif case == "truncated":
            choice["finish_reason"] = "length"
        else:
            call["function"]["arguments"] = {
                "array_arguments": "[]",
                "duplicate_json_key": '{"left":"1","left":"2"}',
                "oversized_arguments": '{"padding":"' + ("x" * 4100) + '"}',
            }[case]
        return httpx.Response(200, json=raw)

    p = configure(f, "deepseek", respond)
    s = session(f, p.id, "ask")
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    if case in ("duplicate_id", "too_many", "truncated"):
        assert run["status"] == "failed" and run["errorCode"] == "MODEL_RESPONSE_INVALID"
        assert not run["toolCalls"]
    else:
        assert run["status"] == "succeeded" and run["toolCalls"][0]["status"] == "denied"
        assert run["toolCalls"][0]["decisions"][0]["action"] == "deny"
        assert run["toolCalls"][0]["attempts"] == 0


@pytest.mark.parametrize("source", ['{"a":NaN}', '{"a":1e999}', '{"a":1,"a":2}'])
def test_provider_json_rejects_non_finite_numbers_and_ambiguous_members(source):
    from app.core.json_data import strict_loads

    with pytest.raises(ValueError):
        strict_loads(source)
