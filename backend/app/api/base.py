from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Id = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9:_-]+$")]
Money = Annotated[str, Field(pattern=r"^[0-9]{1,12}\.[0-9]{2}$")]


# 基础传输对象
class DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, from_attributes=True, alias_generator=to_camel
    )
