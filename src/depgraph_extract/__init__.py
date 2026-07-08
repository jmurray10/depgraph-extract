"""depgraph_extract — turn your infrastructure, code, and agent frameworks into a directed dependency graph.

One consistent interface across 15 sources — Docker Compose, Kubernetes, Terraform,
CloudFormation, AWS CDK, Pulumi, GitHub Actions, package.json, pyproject/requirements,
Python imports, OpenTelemetry traces (OTLP/Jaeger/Zipkin), and the LangGraph / CrewAI /
AutoGen agent frameworks — every one returns a plain ``list[tuple[str, str]]`` edge list.

    import depgraph_extract

    edges, sources = depgraph_extract.from_directory(".")   # auto-detect everything
    edges = depgraph_extract.extract.from_docker_compose("docker-compose.yml")

No graph runtime, no service to sign up for, nothing leaves your machine on the
deterministic path. Feed the edge list to graphviz, networkx, or your own analysis.
"""

from __future__ import annotations

from . import extract
from .extract import from_directory
from .dedupe import dedupe_edges
from .find_edges import find_edges

__version__ = "0.1.0"

__all__ = [
    "extract",
    "from_directory",
    "find_edges",
    "dedupe_edges",
]
