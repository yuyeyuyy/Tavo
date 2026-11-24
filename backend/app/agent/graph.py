from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Literal, NotRequired, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.contracts import RunScope
from app.core.errors import AppError, Code

type StepName = Literal["model", "review", "tools"]
STEPS: tuple[StepName, ...] = ("model", "review", "tools")


# 下一步  dataclass自动创建构造方法
@dataclass(frozen=True)
class Continue:
    next_step: StepName


# loop退出条件
class LoopExit(StrEnum):
    COMPLETED = "completed"
    WAITING_REVIEW = "waiting_review"


# 下一步是继续还是退出loop
# 分三种情况，一种是继续continue，继续循环，
# 另一种是退出循环loopexit，loopexit分为两种情况，一种是完成，一种是需要审批，审批完继续loop
type StepOutcome = Continue | LoopExit


# 执行步骤（model：对话，review：审核，tools：工具）定义方法规范 interface
class RunSteps(Protocol):
    async def model(self, scope: RunScope) -> StepOutcome: ...

    async def review(self, scope: RunScope) -> StepOutcome: ...

    async def tools(self, scope: RunScope) -> StepOutcome: ...


# Agent状态（当前步骤和输出内容）  DTO / record（近似） NotRequired表示这个字段可以不存在
class AgentState(TypedDict):
    # SQLite holds the resumable step. This state only routes one invocation.
    entry_step: StepName
    outcome: NotRequired[StepOutcome]


"""
路由裁判，规定了一些规则
当前节点	允许继续	允许退出
model	review	COMPLETED
review	tools	WAITING_REVIEW
tools	model	WAITING_REVIEW
"""


def route_after(state: AgentState, *, next_step: StepName, exit_outcome: LoopExit) -> str:
    outcome = state["outcome"]
    if outcome is exit_outcome:
        return END
    if isinstance(outcome, Continue) and outcome.next_step == next_step:
        return next_step
    raise AppError(Code.INVALID_STATE)


# 创建graph
"""
graph更像一个轻量级执行调度器，state没有放记录之类的
"""


def create_graph(steps: RunSteps, scope: RunScope):
    graph = StateGraph(AgentState)

    async def model(_state):
        return {"outcome": await steps.model(scope)}

    async def review(_state):
        return {"outcome": await steps.review(scope)}

    async def tools(_state):
        return {"outcome": await steps.tools(scope)}

    graph.add_node("model", model)
    graph.add_node("review", review)
    graph.add_node("tools", tools)
    graph.add_conditional_edges(START, lambda state: state["entry_step"], list(STEPS))
    for node, next_step, exit_outcome in (
        ("model", "review", LoopExit.COMPLETED),
        ("review", "tools", LoopExit.WAITING_REVIEW),
        ("tools", "model", LoopExit.WAITING_REVIEW),
    ):
        graph.add_conditional_edges(
            node,
            partial(route_after, next_step=next_step, exit_outcome=exit_outcome),
            [next_step, END],
        )
    return graph.compile()
