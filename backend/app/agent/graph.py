from decimal import Decimal
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.api.schemas import Analysis, AnalysisInput


class AgentState(TypedDict):
    event: dict
    severity: Literal["info", "warning"]
    analysis: dict


def classify(state: AgentState):
    event = AnalysisInput.model_validate(state["event"])
    threshold = Decimal("10.00") if event.currency == "USD" else Decimal("50.00")
    return {"severity": "warning" if Decimal(event.balance) < threshold else "info"}


def explain(state: AgentState):
    event = AnalysisInput.model_validate(state["event"])
    threshold = "10.00 USD" if event.currency == "USD" else "50.00 CNY"
    result = Analysis(
        event_id=event.id,
        severity=state["severity"],
        summary="模拟账户余额低于演示阈值。"
        if state["severity"] == "warning"
        else "模拟账户余额处于演示阈值之上。",
        evidence=[
            f"来源事件：{event.id}",
            f"模拟余额：{event.balance} {event.currency}",
            f"演示阈值：{threshold}",
        ],
        recommendation="仅用于验证本地工作流；请在接入真实平台后核对数据与提醒规则。",
    )
    return {"analysis": result.model_dump(by_alias=True)}


def create_graph(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("classify", classify)
    graph.add_node("explain", explain)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "explain")
    graph.add_edge("explain", END)
    return graph.compile(checkpointer=checkpointer)
