# Contributing

archgraph is MIT-licensed and contributions are genuinely welcome — new
parsers especially. The whole value of this library is breadth: the more
formats it reads, the more useful it is.

## Great first contributions

- **New edge extractors** in `extract.py` — additional infrastructure
  formats, agent frameworks, CI/CD systems, IaC tools. Ideally pure Python
  (AST or text parsing), with no runtime dependency on the thing being
  parsed. Ideas: Nomad, Helm charts, Ansible, Argo Workflows, Dagger,
  Prefect/Airflow DAGs, Temporal, LangChain LCEL, `Makefile`, `justfile`,
  Bazel/Buck build graphs, TypeScript CDK/Pulumi.
- **Bug fixes** in any parser — edge cases in real-world files are the most
  valuable reports.
- **Docs & examples** — a real-world file that a parser handles well (or
  badly) makes a great `tests/fixtures/` addition.

## The one rule every parser follows

Every extractor returns a `list[tuple[str, str]]` of `(source, target)`
edges, drops self-loops, and uses the exact node names that appear in the
source. That uniform contract is what lets sources blend via `dedupe_edges`.

## Development setup

```bash
git clone https://github.com/jmurray10/jmurray10-archgraph
cd jmurray10-archgraph
pip install -e '.[dev]'
pytest
```

Only dependency is PyYAML (for the Compose / k8s / Terraform / CloudFormation
parsers). Everything else is standard library. No network or credentials are
needed to run the tests.

## Pull-request checklist

1. **Tests** — every new parser ships with at least one fixture in
   `tests/fixtures/` and a test asserting the edges it produces.
2. **Type hints** — annotate arguments and return types.
3. **Docstring** — a one-line summary plus a short usage example.
4. **No new runtime deps** without discussion. Stdlib + PyYAML is the whole
   footprint; keep it that way where possible (lazy-import anything heavier).
5. **CHANGELOG.md** — add a bullet under an "Unreleased" header.

## Releasing (maintainers)

Publishing uses PyPI Trusted Publishing (OIDC) — no token to manage. Pushing
a `v*` tag triggers `.github/workflows/publish.yml`:

```bash
# bump version in pyproject.toml AND src/archgraph/__init__.py
git commit -am "vX.Y.Z: description"
git tag -a vX.Y.Z -m "vX.Y.Z — short summary"
git push && git push origin vX.Y.Z
```

## Questions

Open a GitHub issue.
