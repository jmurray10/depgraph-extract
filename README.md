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
| **Infrastructure as code** | Terraform resource references, CloudFormation (`DependsOn`, `Ref`, `Fn::GetAtt`, `Fn::Sub`), AWS CDK (Python), Pulumi (Python) |
| **CI / packaging** | GitHub Actions `needs`, `package.json` dependencies and workspaces, `pyproject.toml` (PEP 621, dependency-groups, Poetry), `requirements.txt` |
| **Code** | Python imports, including relative imports, with adjustable module `depth` |
| **Telemetry** | OpenTelemetry traces — OTLP JSON, Jaeger query JSON, Zipkin v2 JSON array |

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

These are limits of this tree, measured on the corpus below. They are not
bugs that a repo name can special-case away.

- **Same-name Kubernetes Service and Deployment.** The edge would be a
  self-loop, and self-loops are dropped. On `kubernetes/examples` guestbook
  (`209452cc`) every Service shares its name with its Deployment, so the
  scan returns no edges (held-out recall 0 on those three pairs).
- **Terraform modules and brace scope.** `module` blocks are not resources.
  A `resource` body is read until the next `resource` header, so a reference
  that sits after the last resource is attributed to that resource
  (`terraform-aws-s3-bucket` `examples/object` at `3bdca54e`: one false edge,
  precision 0). References that appear only inside `locals` are not followed.
  When a short name is used by more than one resource, the label is `type.name`.
- **CDK calls that are not `module.Class(...)`.** `API(self, "API", database=database)`
  is invisible. Held-out `aws-cdk-project-structure-python` `backend/component.py`
  recall 0. Method calls on a variable that *was* built as `module.Class`
  (`rule.add_target(...)`, `bucket.grant_read(fn)`) are edges.
- **Pulumi names that are not string literals.** `BucketObject(file, bucket=bucket.id)`
  inside a loop is skipped. Held-out `aws-py-s3-folder` recall 0.75 (3 of 4).
- **LangGraph routers without `path_map`.** `add_conditional_edges(node, router)`
  does not read the router's `Literal` returns. Held-out
  `executive-ai-assistant` `eaia/main/graph.py` at `fb2d1255`: precision 1,
  recall 0.46 (11 of 24 static edges). Notebooks are not parsed. A `goto`
  that is only a variable is not an edge. `Send("node", ...)` inside
  `Command(goto=...)` is.
- **Zipkin JSON that is one object, not an array,** raises `ValueError`.
  `openzipkin/zipkin` `zipkin2-client.json` at `a36e0577` is that shape.
  Edges are parent-span service to child-span service. `remoteEndpoint` is
  not an edge. A trace whose parent span is outside the file contributes nothing.
- **`from_python_imports` on a directory whose child is `__init__.py`**
  names that file `__init__`. Scan the parent directory (`src/`, not `src/pkg/`).
- **`from_directory` unions every Compose file it finds.** Identical service
  names from different stacks become one node.
- **`pyproject.toml` `dynamic = ["dependencies"]`** is not resolved.

## Measured on public repos

2026-11-04. Pins and labels are in [`corpus/manifest.json`](corpus/manifest.json)
and [`corpus/labels`](corpus/labels). Tuning repos were used to fix parsers.
Held-out repos were labeled and scored after that, and did not change the
parsers. Every successful parse was run twice and the edge lists matched.

Tuning, 19 labeled files, precision 1 and recall 1: Compose
`awesome-compose` `18f59bdb`, Kubernetes online-boutique frontend
`7cfba453`, Terraform vpc flow logs `7c1f791e`, CloudFormation EIP
`6de5bfef`, CDK `lambda-cron` `20308818`, Pulumi `aws-py-webserver`
`b2a63a2a`, Flask tests workflow and `pyproject.toml` and `sansio` imports
`a42c4d54`, changesets `package.json` `9ce98a59`, requests `requirements-dev.txt`
`a7e1c745`, LangGraph new-project `e1e44e15` and open_deep_research `graph.py`
`aff4b5b8`, CrewAI stock_analysis `7fba976d`, AutoGen `app_team.py`
`d07c667a`, OTLP `opentelemetry-proto` `examples/trace.json` `68f9c632`
(one span, zero edges), Jaeger UI `testTrace.json` `3b43e330`.

Held-out precision / recall on the labeled files:

| Source | Result |
|---|---|
| Docker Compose | 1.00 / 1.00 (`example-voting-app` `63e9150c`) |
| GitHub Actions | 1.00 / 1.00 (click `tests.yaml` `60b9c0e8`) |
| CloudFormation | 1.00 / 1.00 (`LambdaSample.yaml` `6de5bfef`) |
| package.json and workspaces | 1.00 / 1.00 (lerna `9205c2b7`) |
| pyproject.toml | 1.00 / 1.00 (click `60b9c0e8`, httpx `6c7af967`) |
| requirements.txt | 1.00 / 1.00 (httpx `6c7af967`) |
| Python imports | 1.00 / 1.00 (click `src`, 58 edges) |
| CrewAI | 1.00 / 1.00 (`marketing_strategy` `7fba976d`) |
| Pulumi | 1.00 / 0.75 (`aws-py-s3-folder` `b2a63a2a`) |
| LangGraph | 1.00 / 0.46 (`executive-ai-assistant` `fb2d1255`, 11 of 24) |
| Terraform | 0.00 / 1.00 (no true resource edge; one false edge, s3 `examples/object` `3bdca54e`) |
| Kubernetes | 0.00 / 0.00 (guestbook `209452cc`, three same-name pairs dropped) |
| AWS CDK | 0.00 / 0.00 (`backend/component.py` `f78bdc2e`, bare class calls) |
| OpenTelemetry / Zipkin | not scored — `zipkin2-client.json` `a36e0577` is one JSON object and the parser rejects it |

Re-running the pins against each repo's HEAD on the same day moved some
counts because the upstream files changed, not because the parser is random:
Flask `pyproject.toml` 27 → 25, changesets `package.json` 28 → 23, click
imports 58 → 61 and pyproject 16 → 17, lerna `package.json` 66 → 37 and
workspace edges 2 → 1, s3 `examples/object` 1 → 0. `graph.py` in
open_deep_research, and the crew files under `stock_analysis/` and
`marketing_strategy/`, are not at those paths on HEAD, so those pins go
to 0 edges. AutoGen `d07c667a`, voting-app `63e9150c`, and the CDK
structure repo `f78bdc2e` were already HEAD, so their delta is 0.

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
