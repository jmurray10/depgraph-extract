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

PyYAML is the only dependency (Compose, Kubernetes, Terraform, CloudFormation).
Every other parser uses the standard library. The measurements later in this
file are from this git tree. They are not a claim about an older upload on PyPI;
this commit is not published.

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

Fifteen parsers. Each one returns `list[tuple[str, str]]`.

| Category | Sources |
|---|---|
| **Agent frameworks** | LangGraph, CrewAI, AutoGen — Python AST, the framework is not imported |
| **Containers & orchestration** | Docker Compose (`depends_on`, `links`), Kubernetes (Service selectors, Ingress backends) |
| **Infrastructure as code** | Terraform `resource` and `module` blocks, CloudFormation (`DependsOn`, `Ref`, `Fn::GetAtt`, `Fn::Sub`), AWS CDK (Python), Pulumi (Python) |
| **CI / packaging** | GitHub Actions `needs`, `package.json` dependencies and workspaces, `pyproject.toml` (PEP 621, dependency-groups, Poetry), `requirements.txt` |
| **Code** | Python imports, including relative imports, with adjustable module `depth` |
| **Telemetry** | OpenTelemetry traces — OTLP JSON, Jaeger query JSON, Zipkin v2 (one span or an array). A Zipkin `remoteEndpoint` is the other side of the call |

`from_directory` scans all of them. `include=` and `exclude=` take those source
labels when you want a subset; omitting both keeps the full scan.

```python
edges, sources = depgraph_extract.from_directory(".", exclude={"github-actions", "python-imports"})
```

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

## What it does not extract

- **Terraform values that only live in `locals`.** A `module` or `resource`
  edge is a direct `type.name` / `module.name` reference in that block.
  A relative `source = "../../"` path is not a node. Shared short names
  become the full address (`aws_vpc.this`, `module.vpc`).
- **Construct calls that are not assigned.** `Monitoring(self, "Monitoring", database=database)`
  has no variable, so it is not a node. Assigned calls are, including
  `from backend.api import API` then `API(self, "API", ...)`.
- **LangGraph notebooks, and `goto` values that are only variables.**
  `add_conditional_edges` without a `path_map` uses the router's
  `Literal[...]` annotation and returned string constants. `Send("node")`
  inside `Command(goto=...)` is an edge.
- **A trace span whose parent is not in the file** adds no parent/child edge.
  Zipkin still records `remoteEndpoint` when that peer is present.
- **`from_directory` unions every Compose file it finds.** The same service
  name in two stacks is one node.
- **`pyproject.toml` `dynamic = ["dependencies"]`** is not resolved.
- **Kubernetes names that occur once stay bare.** A name used by more than
  one object is `Kind/name`, so `Service/frontend -> Deployment/frontend`
  is kept.

## Measured on public repos

2026-11-04. Pins and labels are in [`corpus/manifest.json`](corpus/manifest.json)
and [`corpus/labels`](corpus/labels). Tuning is 19 labeled files, precision 1
and recall 1. Held-out files below are also precision 1 and recall 1.
Every successful parse was run twice and the edge lists matched.

| Source | Held-out check |
|---|---|
| Docker Compose | example-voting-app `63e9150c` (5 edges) |
| Kubernetes | guestbook `209452cc` (3 `Kind/name` edges); podinfo kustomize `fd3a834e` |
| Terraform | s3 `examples/object` `3bdca54e` (11 module edges); dynamodb-table `examples/basic` `b6cc5157` |
| CloudFormation | `LambdaSample.yaml` `6de5bfef` |
| AWS CDK | `backend/component.py` `f78bdc2e` |
| Pulumi | `aws-py-s3-folder` `b2a63a2a` (4 edges, including a loop-variable object) |
| GitHub Actions | click `tests.yaml` `60b9c0e8` |
| package.json | lerna `9205c2b7` |
| pyproject.toml | click `60b9c0e8`, httpx `6c7af967` |
| requirements.txt | httpx `6c7af967` |
| Python imports | click `src` `60b9c0e8` (58 edges) |
| LangGraph | executive-ai-assistant `fb2d1255` (24 edges); react-agent `9e516c6d` (4 edges) |
| CrewAI | marketing_strategy `7fba976d` |
| Zipkin | `zipkin2-client.json` `a36e0577` (`frontend -> backend` via `remoteEndpoint`) |

HEAD versus those pins, same day: Flask pyproject 27 → 25, changesets
`package.json` 28 → 23, click imports 58 → 61 and pyproject 16 → 17, lerna
`package.json` 66 → 37 and workspace edges 2 → 1, s3 `examples/object`
11 → 6. `kubernetes/examples` `guestbook/`, open_deep_research `graph.py`,
and the crew files under `stock_analysis/` and `marketing_strategy/` are
not at those paths on HEAD, so those counts go to 0. AutoGen, voting-app,
the CDK structure repo, podinfo, dynamodb-table, and react-agent were
already HEAD (delta 0).

```bash
python corpus/score.py --clone --split tuning
```

The GitHub Actions workflow `Corpus` is `workflow_dispatch` only, so it does
not run on every push.

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
