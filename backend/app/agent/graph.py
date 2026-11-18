from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Literal, NotRequired, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.contracts import RunScope
from app.core.errors import AppError, Code

type StepName = Literal["model", "review", "tools"]
STEPS: tuple[StepName, ...] = ("model", "review", "tools")


@dataclass(frozen=True)
class Continue:
    next_step: StepName


class LoopExit(StrEnum):
    COMPLETED = "completed"
    WAITING_REVIEW = "waiting_review"


type StepOutcome = Continue | LoopExit


class RunSteps(Protocol):
    async def model(self, scope: RunScope) -> StepOutcome: ...

    async def review(self, scope: RunScope) -> StepOutcome: ...

    async def tools(self, scope: RunScope) -> StepOutcome: ...


class AgentState(TypedDict):
    # SQLite holds the resumable step. This state only routes one invocation.
    entry_step: StepName
    outcome: NotRequired[StepOutcome]


def route_after(state: AgentState, *, next_step: StepName, exit_outcome: LoopExit) -> str:
    outcome = state["outcome"]
    if outcome is exit_outcome:
        return END
    if isinstance(outcome, Continue) and outcome.next_step == next_step:
        return next_step
    raise AppError(Code.INVALID_STATE)


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
