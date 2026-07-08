"""Fixture-based tests for depgraph_extract.extract.

No network calls — these tests parse small fixtures committed to tests/fixtures/.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from depgraph_extract import extract


FIXTURES = Path(__file__).parent / "fixtures"


class TestDockerCompose:
    def test_parses_depends_on(self):
        edges = extract.from_docker_compose(str(FIXTURES / "docker-compose.yml"))
        edges_set = {tuple(e) for e in edges}
        assert ("frontend", "api") in edges_set
        assert ("frontend", "auth") in edges_set
        assert ("api", "db") in edges_set
        assert ("api", "cache") in edges_set
        assert ("auth", "db") in edges_set
        # 5 dependency edges total
        assert len(edges) == 5


class TestFromDirectory:
    def test_returns_compose_edges_when_present(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              web:
                image: nginx
                depends_on:
                  - api
              api:
                image: my-api
        """))
        edges, sources = extract.from_directory(str(tmp_path))
        assert ("web", "api") in {tuple(e) for e in edges}
        assert sources.get("docker-compose") == 1

    def test_empty_directory_returns_empty(self, tmp_path):
        edges, sources = extract.from_directory(str(tmp_path))
        assert edges == []
        assert sources == {}

    def test_unrecognized_files_ignored(self, tmp_path):
        (tmp_path / "README.md").write_text("# nothing here")
        (tmp_path / "random.txt").write_text("not infra")
        edges, sources = extract.from_directory(str(tmp_path))
        assert edges == []
        assert sources == {}


class TestPythonImportsDepth:
    def _make_repo(self, tmp_path):
        """services/auth/{__init__,user}.py + services/payments/{__init__,gateway}.py"""
        (tmp_path / "services" / "auth").mkdir(parents=True)
        (tmp_path / "services" / "payments").mkdir(parents=True)
        (tmp_path / "services" / "__init__.py").write_text("")
        (tmp_path / "services" / "auth" / "__init__.py").write_text("")
        (tmp_path / "services" / "auth" / "user.py").write_text(
            "from services.payments.gateway import charge\n"
        )
        (tmp_path / "services" / "payments" / "__init__.py").write_text("")
        (tmp_path / "services" / "payments" / "gateway.py").write_text("def charge(): pass\n")
        return tmp_path

    def test_default_uses_short_names(self, tmp_path):
        from depgraph_extract import extract
        self._make_repo(tmp_path)
        edges = extract.from_python_imports(str(tmp_path))
        edges_set = {tuple(e) for e in edges}
        assert ("user", "gateway") in edges_set

    def test_depth_1_rolls_to_top_level(self, tmp_path):
        from depgraph_extract import extract
        # Single top-level package -> all internal edges are self-loops -> dropped.
        self._make_repo(tmp_path)
        edges = extract.from_python_imports(str(tmp_path), depth=1)
        # All edges within `services` -> services -> services -> dropped as self-loop.
        assert edges == []

    def test_depth_2_groups_at_service_boundary(self, tmp_path):
        from depgraph_extract import extract
        self._make_repo(tmp_path)
        edges = extract.from_python_imports(str(tmp_path), depth=2)
        edges_set = {tuple(e) for e in edges}
        assert ("services.auth", "services.payments") in edges_set


class TestFindEdges:
    def test_deterministic_path_skips_llm(self, tmp_path):
        from depgraph_extract import find_edges
        (tmp_path / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              a:
                image: x
                depends_on: [b]
              b:
                image: y
        """))
        edges, sources, log = find_edges(str(tmp_path))
        assert ("a", "b") in {tuple(e) for e in edges}
        assert "docker-compose" in sources
        assert any("deterministic" in entry for entry in log)
        # No LLM keyword in log
        assert not any("claude" in entry or "gemini" in entry for entry in log)

    def test_max_nodes_truncates(self, tmp_path):
        from depgraph_extract import find_edges
        # docker-compose with 6 services in a chain
        (tmp_path / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              a:
                image: x
                depends_on: [b]
              b:
                image: x
                depends_on: [c]
              c:
                image: x
                depends_on: [d]
              d:
                image: x
                depends_on: [e]
              e:
                image: x
                depends_on: [f]
              f:
                image: x
        """))
        edges, sources, log = find_edges(str(tmp_path), max_nodes=3)
        nodes = {n for e in edges for n in e}
        assert len(nodes) <= 3
        assert any("pruned" in entry for entry in log)
