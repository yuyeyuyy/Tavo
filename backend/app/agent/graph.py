from typing import TypedDict

from langgraph.graph import END, START, StateGraph


# 定义 Agent 状态；持久状态由 Repository 管理，图仅编排受控步骤。
class AgentState(TypedDict):
    step: str
    done: bool


def create_graph(assistant, scope):
    graph = StateGraph(AgentState)

    async def model(_state):
        return await assistant.model(scope)

    async def review(_state):
        return await assistant.review(scope)

    async def tools(_state):
        return await assistant.tools(scope)

    graph.add_node("model", model)
    graph.add_node("review", review)
    graph.add_node("tools", tools)

    def route(state):
        return END if state.get("done") else state["step"]

    graph.add_conditional_edges(START, route)
    for node in ("model", "review", "tools"):
        graph.add_conditional_edges(node, route)
    return graph.compile()
