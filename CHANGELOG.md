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
- **Validated against real public repos** — GoogleCloudPlatform/microservices-demo
  (Kubernetes), docker/awesome-compose (Compose), terraform-aws-modules/terraform-aws-vpc
  (Terraform). Two fixes came out of it:
  - `from_terraform` now resolves `count` / `for_each`-indexed references
    (`aws_subnet.this[0].id`, `aws_eip.nat[count.index].id`), the form real
    modules use — 2 → 56 edges on terraform-aws-vpc.
  - `from_directory` finds Kubernetes manifests by content (`apiVersion` + `kind`)
    in any directory, not just conventional names like `k8s/` — so manifests under
    `release/` or `kubernetes-manifests/` are detected.
- 72 tests covering all 15 parsers plus the `find_edges` egress guard, the
  top-level API, and the two real-repo regressions above. CI on Python 3.9 / 3.11 / 3.13.
