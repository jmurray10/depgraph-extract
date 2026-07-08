# depgraph-extract

[![CI](https://github.com/jmurray10/depgraph-extract/actions/workflows/ci.yml/badge.svg)](https://github.com/jmurray10/depgraph-extract/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Local · no signup](https://img.shields.io/badge/local-no%20signup%2C%20no%20egress-brightgreen.svg)](#install)
[![Sources](https://img.shields.io/badge/sources-15-8a2be2.svg)](#what-it-parses)

**Turn your infrastructure, code, and agent frameworks into a directed dependency graph.**

One consistent interface across **15 sources**. Every parser returns the same thing — a
plain `list[tuple[str, str]]` edge list — so you can point it at a Docker Compose file, a
Terraform directory, an OpenTelemetry trace, or a **LangGraph / CrewAI / AutoGen** app and
get a graph you can drop straight into graphviz, networkx, or your own analysis.

```python
import depgraph_extract

# Auto-detect everything in a directory
edges, sources = depgraph_extract.from_directory(".")
# edges  -> [("web", "api"), ("api", "db"), ...]
# sources -> {"docker-compose": 4, "terraform": 7, "langgraph": 3}

# Or target one source
edges = depgraph_extract.extract.from_docker_compose("docker-compose.yml")
edges = depgraph_extract.extract.from_langgraph("agent.py")
edges = depgraph_extract.extract.from_otel_traces("trace.json")
```

No graph runtime. No service to sign up for. On the deterministic path, **nothing leaves
your machine.**

## Why depgraph-extract?

You already describe your system somewhere — Compose files, Terraform, agent code,
OpenTelemetry traces. depgraph-extract turns any of it into the *same* edge list, so you can:

- **See what depends on what** — pipe the graph into graphviz or networkx; no separate
  diagram tool, no diagram to keep in sync by hand.
- **Read an agent's real control flow** — get the graph straight from LangGraph / CrewAI /
  AutoGen source, statically, without running it or installing the framework.
- **Gate drift in CI** — diff the edge list between commits and fail the build when a new
  dependency, cycle, or single point of failure sneaks in.
- **Feed structural analysis** — one normalized graph across every source, ready for
  centrality, cycle detection, or any graph algorithm you like.

## Install

```bash
pip install depgraph-extract
```

That's it — PyYAML is the only dependency (used by the Compose / k8s / Terraform /
CloudFormation parsers). The AST- and JSON-based parsers need nothing beyond the standard
library.

## Run it

No files of your own? Paste this — it needs nothing but `pip install depgraph-extract`:

```python
import depgraph_extract, tempfile, os

# A tiny LangGraph agent. depgraph-extract parses it by AST — it never runs the file
# and doesn't need langgraph installed.
code = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.set_entry_point("planner")
g.add_edge("planner", "researcher")
g.add_edge("researcher", "writer")
g.set_finish_point("writer")
'''
path = os.path.join(tempfile.mkdtemp(), "agent.py")
open(path, "w").write(code)

for src, dst in depgraph_extract.extract.from_langgraph(path):
    print(f"{src} -> {dst}")
# START -> planner
# planner -> researcher
# researcher -> writer
# writer -> END
```

Or clone the repo and run the fuller [`examples/quickstart.py`](examples/quickstart.py) —
it parses an agent and a Compose file, auto-detects the whole directory, then blends and
dedupes:

```bash
git clone https://github.com/jmurray10/depgraph-extract
cd depgraph-extract && pip install -e . && python examples/quickstart.py
```

## What it parses

| Category | Sources |
|---|---|
| **Agent frameworks** | LangGraph, CrewAI, AutoGen — by AST, without importing the framework |
| **Containers & orchestration** | Docker Compose, Kubernetes |
| **Infrastructure as code** | Terraform, CloudFormation, AWS CDK (Python), Pulumi (Python) |
| **CI / packaging** | GitHub Actions, `package.json` (+ workspaces), `pyproject.toml` / `requirements.txt` |
| **Code** | Python imports (with adjustable module `depth`) |
| **Telemetry** | OpenTelemetry traces — OTLP, Jaeger, Zipkin |

The agent-framework parsers are the reason this exists: extracting the call graph from a
LangGraph, CrewAI, or AutoGen file — statically, without running it or installing the
framework — is something most graph tools can't do.

## Blend sources

Combining extractors often yields the same logical node under several spellings
(`auth-svc`, `auth_svc`, `AuthService`). `dedupe_edges` canonicalizes and merges:

```python
compose = depgraph_extract.extract.from_docker_compose("docker-compose.yml")
traces  = depgraph_extract.extract.from_otel_traces("trace.json")
edges   = depgraph_extract.dedupe_edges(compose + traces, normalize="snake")
```

## One-call discovery

```python
edges, sources, log = depgraph_extract.find_edges(".")
```

`find_edges` runs the deterministic scan and returns the edges plus a human-readable log of
what it did. If a repo has no recognized infra files, you can opt into an LLM fallback with
`find_edges(".", llm_fallback=True, provider="claude")` — this **sends file contents** to
Anthropic or Google, which is why it's off by default.

## Where it goes next

An edge list is the universal input to structural graph analysis. These same parsers power
the [SemanticEmbed SDK](https://github.com/jmurray10/semanticembed-sdk), which encodes each
node's structural role and flags single points of failure, amplification cascades, and
convergence sinks from topology alone — but `depgraph-extract` stands on its own: the graph is
yours to do anything with.

## Contributing

New parsers are the most welcome contribution — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the one-rule parser contract, dev setup,
and the PR checklist. Changes are logged in [CHANGELOG.md](CHANGELOG.md).

```bash
git clone https://github.com/jmurray10/depgraph-extract
cd depgraph-extract && pip install -e '.[dev]' && pytest
```

## Contact

Built by **Jeff Murray** ([@jmurray10](https://github.com/jmurray10)).

- Email: **jeff.murray@alumni.upenn.edu**
- LinkedIn: [linkedin.com/in/jeff-murray-ai](https://www.linkedin.com/in/jeff-murray-ai)
- GitHub: [@jmurray10](https://github.com/jmurray10)

## License

MIT — see [LICENSE](LICENSE). No patent claims, no signup, no server. The graph
is yours.
