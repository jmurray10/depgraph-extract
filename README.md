# archgraph

[![CI](https://github.com/jmurray10/jmurray10-archgraph/actions/workflows/ci.yml/badge.svg)](https://github.com/jmurray10/jmurray10-archgraph/actions/workflows/ci.yml)
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
import archgraph

# Auto-detect everything in a directory
edges, sources = archgraph.from_directory(".")
# edges  -> [("web", "api"), ("api", "db"), ...]
# sources -> {"docker-compose": 4, "terraform": 7, "langgraph": 3}

# Or target one source
edges = archgraph.extract.from_docker_compose("docker-compose.yml")
edges = archgraph.extract.from_langgraph("agent.py")
edges = archgraph.extract.from_otel_traces("trace.json")
```

No graph runtime. No service to sign up for. On the deterministic path, **nothing leaves
your machine.**

## Why archgraph?

You already describe your system somewhere — Compose files, Terraform, agent code,
OpenTelemetry traces. archgraph turns any of it into the *same* edge list, so you can:

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
pip install archgraph
```

That's it — PyYAML is the only dependency (used by the Compose / k8s / Terraform /
CloudFormation parsers). The AST- and JSON-based parsers need nothing beyond the standard
library.

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
compose = archgraph.extract.from_docker_compose("docker-compose.yml")
traces  = archgraph.extract.from_otel_traces("trace.json")
edges   = archgraph.dedupe_edges(compose + traces, normalize="snake")
```

## One-call discovery

```python
edges, sources, log = archgraph.find_edges(".")
```

`find_edges` runs the deterministic scan and returns the edges plus a human-readable log of
what it did. If a repo has no recognized infra files, you can opt into an LLM fallback with
`find_edges(".", llm_fallback=True, provider="claude")` — this **sends file contents** to
Anthropic or Google, which is why it's off by default.

## Where it goes next

An edge list is the universal input to structural graph analysis. These same parsers power
the [SemanticEmbed SDK](https://github.com/jmurray10/semanticembed-sdk), which encodes each
node's structural role and flags single points of failure, amplification cascades, and
convergence sinks from topology alone — but `archgraph` stands on its own: the graph is
yours to do anything with.

## Contributing

New parsers are the most welcome contribution — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the one-rule parser contract, dev setup,
and the PR checklist. Changes are logged in [CHANGELOG.md](CHANGELOG.md).

```bash
git clone https://github.com/jmurray10/jmurray10-archgraph
cd jmurray10-archgraph && pip install -e '.[dev]' && pytest
```

## License

MIT — see [LICENSE](LICENSE). No patent claims, no signup, no server. The graph
is yours.
