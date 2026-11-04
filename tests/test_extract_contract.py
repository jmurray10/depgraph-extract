"""Contract tests for README claims that are checkable without a network.

The public-repo corpus lives in ``corpus/`` and is scored separately. These
tests lock the generic parser behavior those claims depend on.
"""
from __future__ import annotations

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
