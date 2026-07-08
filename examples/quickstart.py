"""Run me:  python examples/quickstart.py

Shows the three things depgraph_extract does: parse one source, auto-detect a whole
directory, and blend sources with name canonicalization. No network, no keys.
"""

import os
import depgraph_extract

HERE = os.path.dirname(__file__)


def main() -> None:
    # 1. One source — a LangGraph agent, parsed by AST (langgraph not installed).
    agent_edges = depgraph_extract.extract.from_langgraph(os.path.join(HERE, "agent_pipeline.py"))
    print("LangGraph pipeline:")
    for src, dst in agent_edges:
        print(f"  {src} -> {dst}")

    # 2. One source — a Docker Compose microservice graph.
    svc_edges = depgraph_extract.extract.from_docker_compose(os.path.join(HERE, "docker-compose.yml"))
    print("\nCompose services:")
    for src, dst in svc_edges:
        print(f"  {src} -> {dst}")

    # 3. Auto-detect everything in this directory at once.
    edges, sources = depgraph_extract.from_directory(HERE)
    print(f"\nfrom_directory found {len(edges)} edges across {dict(sources)}")

    # 4. Blend + canonicalize names (e.g. `api-gateway` and `api_gateway` merge).
    blended = depgraph_extract.dedupe_edges(agent_edges + svc_edges, normalize="snake")
    print(f"deduped to {len(blended)} unique edges")


if __name__ == "__main__":
    main()
