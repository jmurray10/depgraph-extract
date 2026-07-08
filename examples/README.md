# Examples

Runnable, dependency-free demonstrations. Nothing here needs an API key, a
network connection, or the frameworks being parsed installed.

```bash
python examples/quickstart.py
```

| File | What it is | Parser |
|------|------------|--------|
| [`agent_pipeline.py`](agent_pipeline.py) | A LangGraph agent (parsed by AST, langgraph not imported) | `extract.from_langgraph` |
| [`docker-compose.yml`](docker-compose.yml) | A 5-service microservice topology | `extract.from_docker_compose` |
| [`quickstart.py`](quickstart.py) | Parse one source, auto-detect the whole dir, then blend + dedupe | all of the above |

Every parser returns the same thing — a `list[tuple[str, str]]` edge list —
so you can pipe the output straight into `networkx`, `graphviz`, or your own
analysis:

```python
import depgraph_extract, networkx as nx
edges, _ = depgraph_extract.from_directory(".")
G = nx.DiGraph(edges)
```
