"""Contract tests for README claims that are checkable without a network.

The public-repo corpus lives in ``corpus/`` and is scored separately. These
tests lock the generic parser behavior those claims depend on.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from depgraph_extract import extract


def test_readme_langgraph_snippet(tmp_path):
    # The "Run it" example in the README. The printed edges are a claim.
    code = textwrap.dedent("""\
        from langgraph.graph import StateGraph
        g = StateGraph(dict)
        g.set_entry_point("planner")
        g.add_edge("planner", "researcher")
        g.add_edge("researcher", "writer")
        g.set_finish_point("writer")
    """)
    path = tmp_path / "agent.py"
    path.write_text(code)
    assert extract.from_langgraph(str(path)) == [
        ("START", "planner"),
        ("planner", "researcher"),
        ("researcher", "writer"),
        ("writer", "END"),
    ]


def test_relative_imports_between_siblings(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "app.py").write_text("from .scaffold import Scaffold\nfrom . import blueprints\n")
    (pkg / "blueprints.py").write_text("from .scaffold import Scaffold\n")
    (pkg / "scaffold.py").write_text("VALUE = 1\n")
    edges = {tuple(e) for e in extract.from_python_imports(str(pkg))}
    assert edges == {("app", "scaffold"), ("app", "blueprints"), ("blueprints", "scaffold")}


def test_pyproject_pep621_optional_and_dependency_groups(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(textwrap.dedent("""\
        [project]
        name = "Flask"
        dependencies = [
            "blinker>=1.9.0",
            "importlib-metadata>=3.6.0; python_version < '3.10'",
        ]
        [project.optional-dependencies]
        dotenv = ["python-dotenv"]
        [dependency-groups]
        tests = [
            "pytest",
        ]
        [tool.poetry.dependencies]
        python = "^3.9"
        requests = "^2"
    """))
    edges = set(extract.from_pyproject(str(path)))
    assert ("Flask", "blinker") in edges
    assert ("Flask", "importlib-metadata") in edges
    assert ("Flask", "python-dotenv") in edges
    assert ("Flask", "pytest") in edges
    assert ("Flask", "requests") in edges
    assert not any(dst == "python" for _, dst in edges)


def test_requirements_skips_editable_self_and_comments(tmp_path):
    path = tmp_path / "requirements-dev.txt"
    path.write_text("# comment\n-e .[socks]\npytest>=2.8.0,<9\nhttpbin~=0.10.0\n")
    edges = set(extract.from_requirements(str(path)))
    assert edges == {
        (tmp_path.name, "pytest"),
        (tmp_path.name, "httpbin"),
    }


def test_cdk_method_call_and_pulumi_attribute_ref(tmp_path):
    cdk = tmp_path / "cdk.py"
    cdk.write_text(textwrap.dedent("""\
        import aws_lambda as lambda_
        import aws_events as events
        import aws_events_targets as targets
        lambdaFn = lambda_.Function(self, "Singleton", runtime="py")
        rule = events.Rule(self, "Rule", schedule="cron")
        rule.add_target(targets.LambdaFunction(lambdaFn))
    """))
    assert extract.from_aws_cdk(str(cdk)) == [("rule", "lambdaFn")]

    pulumi = tmp_path / "pulumi.py"
    pulumi.write_text(textwrap.dedent("""\
        import pulumi_aws as aws
        group = aws.ec2.SecurityGroup("web-secgrp", description="http")
        server = aws.ec2.Instance("web-server", vpc_security_group_ids=[group.id])
    """))
    assert extract.from_pulumi(str(pulumi)) == [("server", "group")]


def test_langgraph_send_inside_command(tmp_path):
    path = tmp_path / "g.py"
    path.write_text(textwrap.dedent("""\
        from langgraph.graph import StateGraph, START
        from langgraph.types import Command, Send
        def route(state):
            return Command(goto=[Send("worker", {"x": 1})])
        g = StateGraph(dict)
        g.add_node("route", route)
        g.add_node("worker", lambda s: s)
        g.add_edge(START, "route")
    """))
    edges = set(extract.from_langgraph(str(path)))
    assert ("route", "worker") in edges
    assert ("START", "route") in edges


def test_jaeger_trace_object_without_data_wrapper(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(
        '{"traceID":"t","spans":['
        '{"spanID":"a","processID":"p1","references":[]},'
        '{"spanID":"b","processID":"p2","references":[{"refType":"CHILD_OF","spanID":"a"}]}'
        '],"processes":{"p1":{"serviceName":"frontend"},"p2":{"serviceName":"db"}}}'
    )
    assert extract.from_otel_traces(str(path)) == [("frontend", "db")]


def test_from_directory_finds_nested_compose(tmp_path):
    nested = tmp_path / "stacks" / "web"
    nested.mkdir(parents=True)
    (nested / "compose.yaml").write_text(
        "services:\n  proxy:\n    depends_on: [backend]\n  backend:\n    image: app\n"
    )
    edges, sources = extract.from_directory(str(tmp_path))
    assert ("proxy", "backend") in {tuple(e) for e in edges}
    assert sources.get("docker-compose") == 1


def test_kubernetes_same_name_service_and_deployment_keeps_kind(tmp_path):
    (tmp_path / "guestbook.yaml").write_text(textwrap.dedent("""\
        apiVersion: v1
        kind: Service
        metadata:
          name: frontend
        spec:
          selector:
            app: guestbook
        ---
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: frontend
        spec:
          template:
            metadata:
              labels:
                app: guestbook
    """))
    edges = set(extract.from_kubernetes(str(tmp_path)))
    assert ("Service/frontend", "Deployment/frontend") in edges


def test_terraform_module_refs_stay_inside_their_block(tmp_path):
    (tmp_path / "main.tf").write_text(textwrap.dedent("""\
        resource "aws_kms_key" "this" {
          description = "key"
        }
        resource "random_pet" "this" {
          length = 2
        }
        module "bucket" {
          source = "../../"
          bucket = random_pet.this.id
          kms_key_id = aws_kms_key.this.arn
        }
    """))
    edges = set(extract.from_terraform(str(tmp_path)))
    assert ("aws_kms_key.this", "random_pet.this") not in edges
    assert ("bucket", "random_pet.this") in edges
    assert ("bucket", "aws_kms_key.this") in edges


def test_terraform_heredoc_braces_do_not_end_the_block(tmp_path):
    (tmp_path / "main.tf").write_text(textwrap.dedent("""\
        resource "random_pet" "name" {
          length = 2
        }
        module "table" {
          source = "../../"
          name   = "t-${random_pet.name.id}"
          policy = <<POLICY
        { "Statement": [] }
        POLICY
        }
        resource "aws_iam_role" "extra" {
          name = random_pet.name.id
        }
    """))
    edges = set(extract.from_terraform(str(tmp_path)))
    assert ("table", "name") in edges
    assert ("extra", "name") in edges


def test_zipkin_single_object_uses_remote_endpoint(tmp_path):
    path = tmp_path / "span.json"
    path.write_text(
        '{"traceId":"abc","id":"child","parentId":"missing","kind":"CLIENT",'
        '"localEndpoint":{"serviceName":"frontend"},'
        '"remoteEndpoint":{"serviceName":"backend"}}'
    )
    assert extract.from_otel_traces(str(path)) == [("frontend", "backend")]
    path.write_text(
        '{"traceId":"abc","id":"s","kind":"SERVER",'
        '"localEndpoint":{"serviceName":"backend"},'
        '"remoteEndpoint":{"serviceName":"frontend"}}'
    )
    assert extract.from_otel_traces(str(path)) == [("frontend", "backend")]


def test_langgraph_conditional_edges_without_path_map(tmp_path):
    path = tmp_path / "g.py"
    path.write_text(textwrap.dedent('''\
        from typing import Literal
        from langgraph.graph import StateGraph, START, END
        def route(state) -> Literal["draft", "notify"]:
            if state:
                return "draft"
            return "notify"
        g = StateGraph(dict)
        g.add_node("triage", lambda s: s)
        g.add_node("draft", lambda s: s)
        g.set_entry_point("triage")
        g.add_conditional_edges("triage", route)
        g.add_edge("notify", END)
    '''))
    edges = set(extract.from_langgraph(str(path)))
    assert ("triage", "draft") in edges
    assert ("triage", "notify") in edges
    assert ("START", "triage") in edges


def test_cdk_bare_imported_construct_call(tmp_path):
    path = tmp_path / "app.py"
    path.write_text(textwrap.dedent("""\
        from backend.database import Database
        from backend.api import API
        database = Database(self, "Database", billing="on_demand")
        api = API(self, "API", table=database.table)
        database.table.grant_read_write_data(api.function)
    """))
    edges = set(extract.from_aws_cdk(str(path)))
    assert ("api", "database") in edges
    assert ("database", "api") in edges


def test_pulumi_loop_variable_resource_name(tmp_path):
    path = tmp_path / "main.py"
    path.write_text(textwrap.dedent("""\
        import pulumi_aws as aws
        bucket = aws.s3.Bucket("web")
        for filename in files:
            obj = aws.s3.BucketObject(filename, bucket=bucket.id)
        named = aws.s3.BucketObject(f"web-{suffix}", bucket=bucket.id)
    """))
    edges = set(extract.from_pulumi(str(path)))
    assert ("obj", "bucket") in edges
    assert ("named", "bucket") in edges
    # Helper calls are not resources, so their variables are not nodes.
    assert not any(node == "filepath" or node == "filename" for edge in edges for node in edge)


def test_python_imports_package_root_init(tmp_path):
    pkg = tmp_path / "click"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .core import Command\n")
    (pkg / "core.py").write_text("from .utils import echo\n")
    (pkg / "utils.py").write_text("VALUE = 1\n")
    edges = {tuple(e) for e in extract.from_python_imports(str(pkg))}
    assert ("click", "core") in edges
    assert ("core", "utils") in edges


def test_terraform_local_refs_resolve_to_resources(tmp_path):
    (tmp_path / "main.tf").write_text(textwrap.dedent("""\
        locals {
          bucket_id = aws_s3_bucket.logs.id
          # not an assignment: foo = aws_s3_bucket.ghost.id
          aliases   = [local.bucket_id, aws_s3_bucket.extra.id]
          groups    = [for g in aws_cloudwatch_log_group.main : g.arn]
          missing   = local.unknown
        }
        resource "aws_iam_policy" "read" {
          resources = [local.aliases, local.groups, local.missing]
        }
        resource "aws_s3_bucket" "logs" {
          tags = { Name = local.bucket_id }
        }
        resource "aws_s3_bucket" "extra" {}
        resource "aws_cloudwatch_log_group" "main" {}
    """))
    edges = set(extract.from_terraform(str(tmp_path)))
    assert ("read", "logs") in edges
    assert ("read", "extra") in edges
    assert ("read", "main") in edges
    assert ("logs", "logs") not in edges
    assert not any("ghost" in node or "unknown" in node for edge in edges for node in edge)


def test_langgraph_notebook_and_same_scope_goto(tmp_path):
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["ignore Command(goto='markdown')"]},
            {
                "cell_type": "code",
                "source": [
                    "%pip install langgraph\n",
                    "from langgraph.graph import StateGraph, START, END\n",
                    "from langgraph.types import Command\n",
                ],
            },
            {
                "cell_type": "code",
                "source": [
                    "def node(state):\n",
                    "    dest = 'sink'\n",
                    "    return Command(goto=dest)\n",
                    "def other(state):\n",
                    "    dest = 'nope'\n",
                    "    return state\n",
                    "def mixed(state):\n",
                    "    dest = 'kept'\n",
                    "    dest = state['next']\n",
                    "    return Command(goto=dest)\n",
                ],
            },
            {
                "cell_type": "code",
                "source": [
                    "g = StateGraph(dict)\n",
                    "g.add_node('node', node)\n",
                    "g.add_node('other', other)\n",
                    "g.add_node('mixed', mixed)\n",
                    "g.add_node('sink', lambda s: s)\n",
                    "g.add_edge(START, 'node')\n",
                    "g.add_edge('sink', END)\n",
                ],
            },
        ]
    }
    path = tmp_path / "graph.ipynb"
    path.write_text(json.dumps(notebook))
    edges = set(extract.from_langgraph(str(path)))
    assert ("node", "sink") in edges
    assert ("START", "node") in edges
    assert ("sink", "END") in edges
    assert ("other", "nope") not in edges
    assert ("mixed", "kept") not in edges

    script = tmp_path / "g.py"
    script.write_text(textwrap.dedent("""\
        from langgraph.graph import StateGraph, START, END
        from langgraph.types import Command
        OUTSIDE = "outside"
        def router(state):
            dest = "alpha" if state else "beta"
            return dest
        def node(state):
            return Command(goto=OUTSIDE)
        g = StateGraph(dict)
        g.add_node("router", router)
        g.add_node("node", node)
        g.add_conditional_edges("router", router)
        g.add_edge(START, "node")
    """))
    edges = set(extract.from_langgraph(str(script)))
    assert ("router", "alpha") in edges
    assert ("router", "beta") in edges
    assert ("node", "outside") not in edges

    broken = tmp_path / "broken.ipynb"
    broken.write_text(json.dumps({
        "cells": [
            {"cell_type": "code", "source": ["def node(state):\n", "    dest = 'sink'\n", "    return Command(goto=dest)\n"]},
            {"cell_type": "code", "source": ["this is not valid python $$$ \n"]},
            {"cell_type": "code", "source": [
                "from langgraph.graph import StateGraph, START\n",
                "from langgraph.types import Command\n",
                "g = StateGraph(dict)\n",
                "g.add_node('node', node)\n",
                "g.add_edge(START, 'node')\n",
            ]},
        ]
    }))
    edges = set(extract.from_langgraph(str(broken)))
    assert ("node", "sink") in edges
    assert ("START", "node") in edges


def test_compose_stacks_namespace_shared_service_names(tmp_path):
    api = tmp_path / "api"
    worker = tmp_path / "worker"
    solo = tmp_path / "solo"
    for directory in (api, worker, solo):
        directory.mkdir()
    (api / "compose.yaml").write_text(
        "name: shop\nservices:\n  web:\n    depends_on: [db]\n  db:\n    image: postgres\n"
    )
    (worker / "compose.yaml").write_text(
        "name: shop\nservices:\n  web:\n    depends_on: [cache]\n  cache:\n    image: redis\n"
    )
    (solo / "compose.yaml").write_text(
        "services:\n  only:\n    depends_on: [redis]\n  redis:\n    image: redis\n"
    )
    edges, sources = extract.from_directory(str(tmp_path), include="docker-compose")
    found = {tuple(edge) for edge in edges}
    assert ("shop/api/web", "shop/api/db") in found or ("shop/api/web", "db") in found
    # Both stacks are named shop, so the directory keeps the webs apart.
    assert ("shop/api/web", "db") in found
    assert ("shop/worker/web", "cache") in found
    assert ("only", "redis") in found
    assert sources["docker-compose"] == 3
    assert not any(src == "web" or dst == "web" for src, dst in found)


def test_pyproject_dynamic_file_dependencies(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests>=2\n-r extra.txt\n")
    (tmp_path / "extra.txt").write_text("flask\n")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n")
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent("""\
        [project]
        name = "demo"
        dynamic = ["dependencies", "optional-dependencies"]

        [tool.setuptools.dynamic]
        dependencies = { file = ["requirements.txt"] }

        [tool.setuptools.dynamic.optional-dependencies]
        dev = { file = "requirements-dev.txt" }
    """))
    edges = set(extract.from_pyproject(str(tmp_path / "pyproject.toml")))
    assert edges == {("demo", "requests"), ("demo", "flask"), ("demo", "pytest")}

    hatch = tmp_path / "hatch"
    hatch.mkdir()
    (hatch / "reqs.txt").write_text("httpx\n")
    (hatch / "dev.txt").write_text("pytest\n")
    (hatch / "pyproject.toml").write_text(textwrap.dedent("""\
        [project]
        name = "boxed"
        dynamic = ["version", "dependencies"]
        dependencies = ["should-not-be-here"]

        [tool.hatch.metadata.hooks.requirements-txt]
        files = ["reqs.txt"]

        [tool.hatch.metadata.hooks.requirements-txt.optional-dependencies]
        dev = ["dev.txt"]
    """))
    edges = set(extract.from_pyproject(str(hatch / "pyproject.toml")))
    # optional-dependencies is not dynamic, so dev.txt is not an edge.
    assert ("boxed", "httpx") in edges
    assert ("boxed", "pytest") not in edges
    assert ("boxed", "should-not-be-here") in edges


def test_quickstart_compose_has_five_services():
    compose = Path(__file__).resolve().parents[1] / "examples" / "docker-compose.yml"
    text = compose.read_text()
    # The examples README says this file is a 5-service topology.
    assert text.count("image:") + text.count("build:") >= 5
    edges = extract.from_docker_compose(str(compose))
    assert {tuple(e) for e in edges} == {
        ("gateway", "web"),
        ("web", "api"),
        ("api", "db"),
        ("api", "cache"),
    }
