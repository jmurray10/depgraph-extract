"""Coverage for the parsers and behaviors not exercised by the inherited
SDK tests: Kubernetes, Terraform, GitHub Actions, package.json (single +
workspaces), the opt-in `find_edges` egress guard, and the top-level API.
"""

from __future__ import annotations

import json
import textwrap

import pytest

import archgraph
from archgraph import extract


def _write(path, text):
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


class TestKubernetes:
    def test_service_to_deployment_and_ingress(self, tmp_path):
        _write(tmp_path / "app.yaml", """\
            apiVersion: v1
            kind: Service
            metadata:
              name: web
            spec:
              selector:
                app: web
            ---
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: web-deploy
            spec:
              template:
                metadata:
                  labels:
                    app: web
            ---
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: web-ingress
            spec:
              rules:
                - http:
                    paths:
                      - backend:
                          service:
                            name: web
        """)
        edges = {tuple(e) for e in extract.from_kubernetes(str(tmp_path / "app.yaml"))}
        assert ("web", "web-deploy") in edges       # Service -> Deployment (selector match)
        assert ("web-ingress", "web") in edges       # Ingress -> Service (backend)


class TestTerraform:
    def test_cross_resource_reference(self, tmp_path):
        _write(tmp_path / "main.tf", """\
            resource "aws_instance" "web" {
              subnet_id = aws_subnet.main.id
            }
            resource "aws_subnet" "main" {
              cidr_block = "10.0.1.0/24"
            }
        """)
        edges = {tuple(e) for e in extract.from_terraform(str(tmp_path))}
        assert ("web", "main") in edges

    def test_count_for_each_indexed_refs(self, tmp_path):
        # Real modules reference resources with count/for_each indexing, e.g.
        # `aws_subnet.public[0].id`. Regression for the terraform-aws-vpc miss.
        _write(tmp_path / "vpc.tf", """\
            resource "aws_nat_gateway" "gw" {
              subnet_id     = aws_subnet.public[0].id
              allocation_id = aws_eip.nat[count.index].id
            }
            resource "aws_subnet" "public" {
              count = 2
            }
            resource "aws_eip" "nat" {
              count = 2
            }
        """)
        edges = {tuple(e) for e in extract.from_terraform(str(tmp_path))}
        assert ("gw", "public") in edges     # aws_subnet.public[0].id
        assert ("gw", "nat") in edges        # aws_eip.nat[count.index].id


class TestGitHubActions:
    def test_needs_builds_job_graph(self, tmp_path):
        _write(tmp_path / "ci.yml", """\
            name: CI
            jobs:
              build:
                runs-on: ubuntu-latest
              test:
                needs: build
                runs-on: ubuntu-latest
        """)
        edges = {tuple(e) for e in extract.from_github_actions(str(tmp_path / "ci.yml"))}
        assert ("CI", "CI/build") in edges          # root job
        assert ("CI/build", "CI/test") in edges      # needs edge


class TestPackageJson:
    def test_single_manifest_deps(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "myapp",
            "dependencies": {"express": "^4"},
            "devDependencies": {"jest": "^29"},
        }), encoding="utf-8")
        edges = {tuple(e) for e in extract.from_package_json(str(tmp_path / "package.json"))}
        assert ("myapp", "express") in edges
        assert ("myapp", "jest") in edges

    def test_workspaces_link_local_packages_only(self, tmp_path):
        a = tmp_path / "packages" / "a"
        b = tmp_path / "packages" / "b"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        (a / "package.json").write_text(json.dumps({
            "name": "@repo/a",
            "dependencies": {"@repo/b": "*", "left-pad": "^1"},
        }), encoding="utf-8")
        (b / "package.json").write_text(json.dumps({"name": "@repo/b"}), encoding="utf-8")
        edges = {tuple(e) for e in extract.from_package_json_workspaces(str(tmp_path))}
        assert ("@repo/a", "@repo/b") in edges       # local -> local
        assert not any(dst == "left-pad" for _, dst in edges)  # external dep excluded


class TestFindEdgesEgressGuard:
    def test_empty_dir_does_not_egress_by_default(self, tmp_path):
        edges, sources, log = archgraph.find_edges(str(tmp_path))
        assert edges == []
        assert sources == {}
        # New guard: names the flag, and never mentions an LLM provider.
        assert any("llm_fallback=False" in entry for entry in log)
        assert not any("claude" in entry or "gemini" in entry for entry in log)


class TestFromDirectoryK8sDiscovery:
    def test_finds_manifests_in_nonstandard_dir(self, tmp_path):
        # Manifests under release/ — not a conventional k8s dir name. Regression
        # for the GoogleCloudPlatform/microservices-demo auto-detect miss.
        rel = tmp_path / "release"
        rel.mkdir()
        _write(rel / "kubernetes-manifests.yaml", """\
            apiVersion: v1
            kind: Service
            metadata:
              name: web
            spec:
              selector:
                app: web
            ---
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: web-deploy
            spec:
              template:
                metadata:
                  labels:
                    app: web
        """)
        edges, sources = archgraph.from_directory(str(tmp_path))
        assert "kubernetes" in sources
        assert ("web", "web-deploy") in {tuple(e) for e in edges}


class TestTopLevelApi:
    def test_convenience_exports_are_callable(self, tmp_path):
        _write(tmp_path / "docker-compose.yml", """\
            services:
              a:
                depends_on: [b]
              b: {}
        """)
        # from_directory re-exported at top level
        edges, sources = archgraph.from_directory(str(tmp_path))
        assert ("a", "b") in {tuple(e) for e in edges}
        assert "docker-compose" in sources
        # dedupe_edges re-exported at top level
        merged = archgraph.dedupe_edges([("A", "B"), ("a", "b")], normalize="lower")
        assert merged == [("a", "b")]
