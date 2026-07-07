"""Minimal LangGraph fixture used for parser tests.

Important: this file is parsed via `ast`, not executed. The langgraph import
will fail at runtime if you try to actually run it, and that's fine — the
parser doesn't import the framework.
"""

from langgraph.graph import StateGraph, START, END


def planner(state):
    return state


def researcher(state):
    return state


def writer(state):
    return state


def critic(state):
    return state


def router(state):
    return "writer" if state.get("ready") else "researcher"


workflow = StateGraph(dict)
workflow.add_node("planner", planner)
workflow.add_node("researcher", researcher)
workflow.add_node("writer", writer)
workflow.add_node("critic", critic)

workflow.set_entry_point("planner")
workflow.add_edge("planner", "researcher")
workflow.add_conditional_edges(
    "researcher",
    router,
    {"writer": "writer", "researcher": "researcher"},
)
workflow.add_edge("writer", "critic")
workflow.set_finish_point("critic")

app = workflow.compile()
