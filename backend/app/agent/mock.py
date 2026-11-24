import json
import re

from app.core.sandbox_config import default_policy
from app.llm.turns import AssistantTurn, proposal


class MockAssistantModel:
    async def turn(self, profile, history, specs, credential=None):
        last = history[-1]
        if last.get("role") == "tool":
            result = json.loads(last["content"])
            if result["status"] != "succeeded":
                text = "操作未获准或未完成，我不能声称执行成功。"
            elif "exitCode" in result["output"]:
                text = "已完成：" + result["output"].get("stdout", "")
            elif "result" in result["output"]:
                text = "计算结果：" + result["output"]["result"]
            else:
                text = "当前 UTC 时间：" + result["output"].get("utc", "")
            return AssistantTurn(text=text, native={"role": "assistant", "content": text})
        text = last.get("content", "")
        calls = []
        expression = re.search(r"(-?\d+(?:\.\d+)?)\s*([+*/×÷-])\s*(-?\d+(?:\.\d+)?)", text)
        names = {s.name for s in specs}
        command_tool = "host_bash" if text.startswith("host:") else "sandbox"
        if text.startswith(("bash:", "sandbox:", "host:")) and command_tool in names:
            calls = [
                proposal(
                    "mock-bash",
                    command_tool,
                    {
                        "command": text.split(":", 1)[1].strip(),
                        "cwd": ".",
                        "timeoutSeconds": default_policy().default_timeout_seconds,
                    },
                )
            ]
        elif expression and "calculate" in names:
            a, operation, b = expression.groups()
            op = {
                "+": "add",
                "-": "subtract",
                "*": "multiply",
                "×": "multiply",
                "/": "divide",
                "÷": "divide",
            }[operation]
            calls = [proposal("mock-call", "calculate", {"operation": op, "left": a, "right": b})]
        elif re.search("时间|几点|time", text, re.I) and "get_current_time" in names:
            calls = [proposal("mock-call", "get_current_time", {})]
        if calls:
            native = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c.provider_id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in calls
                ],
            }
            return AssistantTurn(calls=calls, native=native)
        if re.search("记忆|memory", text, re.I):
            answer = "记忆像一份可以跨对话使用的笔记，上下文则是本轮实际提供给模型的信息。会话历史会持久保存，但不会自动变成长久记忆。"
        elif re.search("工具|tool", text, re.I):
            answer = "我可以协助分析问题、编写代码和验证结果。请直接说明你希望完成的任务。"
        else:
            answer = (
                "你好，我是 Tavo。这是服务端 Mock 演示，没有调用真实模型。"
                if re.search("你好|tavo", text, re.I)
                else "收到你的消息了。这是服务端 Mock，消息已保存；可试试“计算 12 / 3”或“现在的 UTC 时间”。"
            )
        return AssistantTurn(text=answer, native={"role": "assistant", "content": answer})
