# Changelog

All notable changes to **archgraph**. This project adheres to
[Semantic Versioning](https://semver.org/).

## 0.1.0 — First release

Dependency-graph extraction sliced out of the SemanticEmbed SDK into a
standalone, MIT-licensed package. One `list[tuple[str, str]]` edge-list
interface across 15 sources.

- **Agent frameworks** (AST-only, no need to install the framework):
  `extract.from_langgraph`, `extract.from_crewai`, `extract.from_autogen`.
- **Containers & orchestration:** `extract.from_docker_compose`,
  `extract.from_kubernetes`.
- **Infrastructure as code:** `extract.from_terraform`,
  `extract.from_cloudformation`, `extract.from_aws_cdk`, `extract.from_pulumi`.
- **CI / packaging:** `extract.from_github_actions`,
  `extract.from_package_json` (+ workspaces),
  `extract.from_python_imports(depth=N)`.
- **Telemetry:** `extract.from_otel_traces` — auto-detects OTLP / Jaeger / Zipkin.
- **Orchestration helpers:** `from_directory` (auto-detect everything),
  `dedupe_edges` (canonicalize + merge across sources), `find_edges`
  (deterministic scan with an **opt-in** LLM fallback — off by default, no
  data leaves your machine unless you pass `llm_fallback=True`).
- 63 tests, CI on Python 3.9 / 3.11 / 3.13.
