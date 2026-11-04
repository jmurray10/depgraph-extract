from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command


def supervisor(state) -> Command[Literal["supervisor_tools"]]:
    return Command(goto="supervisor_tools")


def supervisor_tools(state) -> Command[Literal["supervisor", "__end__"]]:
    if state.get("done"):
        return Command(goto=END)
    return Command(goto="supervisor")


def clarify(state):
    return Command(goto="write_brief" if state else END)


def write_brief(state):
    return Command(goto="research")


sub = StateGraph(dict)
sub.add_node("supervisor", supervisor)
sub.add_node("supervisor_tools", supervisor_tools)
sub.add_edge(START, "supervisor")
sub_graph = sub.compile()

worker = StateGraph(dict)
worker.add_node("worker", lambda s: s)
worker.add_edge(START, "worker")
worker.add_edge("worker", END)

main = StateGraph(dict)
main.add_node("clarify", clarify)
main.add_node("write_brief", write_brief)
main.add_node("research", sub_graph)
main.add_node("report", lambda s: s)
main.add_edge(START, "clarify")
main.add_edge("research", "report")
main.add_edge("report", END)
