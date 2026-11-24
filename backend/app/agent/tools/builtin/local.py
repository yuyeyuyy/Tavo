from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field

from app.agent.tools.definition import define_tool
from app.api.base import DTO
from app.core.errors import AppError, Code


class EmptyArguments(DTO):
    pass


class ClockResult(DTO):
    utc: str


Number = Annotated[str, Field(pattern=r"^-?[0-9]{1,18}(\.[0-9]{1,12})?$")]


class Calculation(DTO):
    operation: Literal["add", "subtract", "multiply", "divide"]
    left: Number
    right: Number


class CalculationResult(DTO):
    result: Annotated[str, Field(max_length=100)]


def calculate(value, _context):
    with localcontext() as context:
        context.prec = 64
        left, right = Decimal(value.left), Decimal(value.right)
        if value.operation == "divide" and right == 0:
            raise AppError(Code.INVALID_INPUT)
        result = {
            "add": lambda: left + right,
            "subtract": lambda: left - right,
            "multiply": lambda: left * right,
            "divide": lambda: left / right,
        }[value.operation]()
        if not result.is_finite():
            raise AppError(Code.INVALID_DATA)
        return CalculationResult(result=format(result, "f"))


def current_time(_arguments, context):
    return ClockResult(utc=context.now())


def local_tools():
    return [
        define_tool(
            "get_current_time",
            "Read the current UTC time. No arguments or external access.",
            EmptyArguments,
            ClockResult,
            current_time,
        ),
        define_tool(
            "calculate",
            "Calculate add/subtract/multiply/divide on two decimal strings. No expressions or code are executed.",
            Calculation,
            CalculationResult,
            calculate,
        ),
    ]
