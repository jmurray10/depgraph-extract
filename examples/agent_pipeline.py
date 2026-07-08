"""A small LangGraph-style pipeline. depgraph_extract parses this by AST — it never
imports langgraph or runs the file — so `from_langgraph` works without
`pip install langgraph`.
"""

from langgraph.graph import StateGraph

graph = StateGraph(dict)
graph.add_node("planner", plan)
graph.add_node("researcher", research)
graph.add_node("writer", write)
graph.add_node("critic", critique)

graph.set_entry_point("planner")
graph.add_edge("planner", "researcher")
graph.add_edge("researcher", "writer")
graph.add_edge("writer", "critic")
graph.add_conditional_edges("critic", route, {"revise": "writer", "done": "__end__"})
graph.set_finish_point("critic")
