"""Independent Tavo runtime; this Mock stage never enables remote tracing."""

import os

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
