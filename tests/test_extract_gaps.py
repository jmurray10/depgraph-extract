"""Regressions for extractor gaps found on outside repos (crewAI-examples,
open_deep_research, terraform-aws-vpc)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from depgraph_extract import extract

FIX = Path(__file__).parent / "fixtures"


def test_crewai_decorator_sequential_crew():
    edges = set(extract.from_crewai(str(FIX / "crew_decorator" / "crew.py")))
    assert {("planner", "plan_task"), ("scout", "scout_task"), ("compiler", "compile_task")} <= edges
    assert {("plan_task", "scout_task"), ("scout_task", "compile_task")} <= edges  # sequential
    assert ("plan_task", "compile_task") in edges  # YAML context


def test_crewai_hierarchical_manager(tmp_path):
    f = tmp_path / "crew.py"
    f.write_text(
        "from crewai import Agent, Crew, Process, Task\n"
        "@CrewBase\nclass C:\n"
        "    @agent\n    def mgr(self): return Agent()\n"
        "    @agent\n    def a(self): return Agent()\n"
        "    @task\n    def t1(self): return Task(agent=self.a())\n"
        "    @task\n    def t2(self): return Task(agent=self.a())\n"
        "    @crew\n    def crew(self): return Crew(agents=self.agents, tasks=self.tasks,"
        " process=Process.hierarchical, manager_agent=self.mgr())\n"
    )
    edges = set(extract.from_crewai(str(f)))
    assert ("mgr", "a") in edges
    assert ("t1", "t2") not in edges  # no sequential chain in hierarchical mode

    f.write_text(
        "from crewai import Agent, Crew, Task\n"
        "class C:\n"
        "    @agent\n    def mgr(self): return Agent()\n"
        "    @agent\n    def worker(self): return Agent()\n"
        "    @task\n    def only(self): return Task(agent=self.worker())\n"
        "    @crew\n    def crew(self):\n"
        "        return Crew(agents=self.agents, tasks=self.tasks,"
        " process='hierarchical', manager_agent=self.mgr())\n"
    )
    edges = set(extract.from_crewai(str(f)))
    assert ("mgr", "worker") in edges
    assert ("worker", "only") in edges
    assert all(src != "only" for src, _dst in edges)


def test_crewai_redefined_task_keeps_first_slot_and_last_body(tmp_path):
    # crewAI-examples stock_analysis defines financial_analysis twice.
    # The class dict keeps the first position and the last body.
    f = tmp_path / "crew.py"
    f.write_text(textwrap.dedent("""
        from crewai import Agent, Crew, Process, Task
        class C:
            @agent
            def financial_agent(self):
                return Agent()
            @task
            def financial_analysis(self):
                return Task(agent=self.financial_agent())
            @agent
            def research_analyst_agent(self):
                return Agent()
            @task
            def research(self):
                return Task(agent=self.research_analyst_agent())
            @agent
            def financial_analyst_agent(self):
                return Agent()
            @task
            def financial_analysis(self):
                return Task(agent=self.financial_analyst_agent())
            @task
            def filings_analysis(self):
                return Task(agent=self.financial_analyst_agent())
            @crew
            def crew(self):
                return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)
    """))
    edges = set(extract.from_crewai(str(f)))
    assert ("financial_analyst_agent", "financial_analysis") in edges
    assert ("financial_agent", "financial_analysis") not in edges
    assert ("financial_analysis", "research") in edges
    assert ("research", "filings_analysis") in edges
    assert ("research", "financial_analysis") not in edges


def test_crewai_yaml_agent_custom_path_and_string_context(tmp_path):
    (tmp_path / "tasks.yml").write_text(textwrap.dedent("""
        alpha:
          description: a
          agent: planner
        beta:
          description: b
          agent: writer
        gamma:
          description: c
          agent: planner
          context: alpha
    """))
    f = tmp_path / "crew.py"
    f.write_text(textwrap.dedent("""
        from crewai import Agent, Crew, Task
        class C:
            tasks_config = "tasks.yml"
            @agent
            def planner(self):
                return Agent()
            @agent
            def writer(self):
                return Agent()
            @task
            def alpha(self):
                return Task(config=self.tasks_config["alpha"])
            @task
            def beta(self):
                return Task(config=self.tasks_config["beta"])
            @task
            def gamma(self):
                return Task(config=self.tasks_config["gamma"])
            @crew
            def crew(self):
                return Crew(agents=self.agents, tasks=self.tasks, process="sequential")
    """))
    edges = set(extract.from_crewai(str(f)))
    assert ("planner", "alpha") in edges
    assert ("writer", "beta") in edges
    assert ("planner", "gamma") in edges
    assert ("alpha", "beta") in edges  # sequential
    assert ("beta", "gamma") in edges
    assert ("alpha", "gamma") in edges  # YAML context, not just the chain


def test_langgraph_command_goto_and_annotations():
    edges = set(extract.from_langgraph(str(FIX / "langgraph_command.py")))
    assert ("clarify", "write_brief") in edges
    assert ("clarify", "END") in edges
    assert ("write_brief", "research") in edges
    assert ("supervisor", "supervisor_tools") in edges
    assert ("supervisor_tools", "supervisor") in edges
    # "__end__" in the Literal annotation is the END sentinel, then rewritten
    # to the node the subgraph is mounted on.
    assert ("supervisor_tools", "__end__") not in edges
    assert not any("__end__" in n or "__start__" in n for e in edges for n in e)


def test_langgraph_multiple_builders_keep_separate_start_end():
    edges = set(extract.from_langgraph(str(FIX / "langgraph_command.py")))
    # top-level graph keeps bare START/END
    assert ("START", "clarify") in edges and ("report", "END") in edges
    # mounted subgraph: entry/exit wired to the parent node, not the global START/END
    assert ("research", "supervisor") in edges
    assert ("supervisor_tools", "research") in edges
    assert ("START", "supervisor") not in edges
    # unmounted extra builder gets its own scoped START/END
    assert ("START[worker]", "worker") in edges and ("worker", "END[worker]") in edges
    assert ("START", "worker") not in edges


def test_langgraph_earlier_function_builder_does_not_steal_start(tmp_path):
    f = tmp_path / "g.py"
    f.write_text(textwrap.dedent("""
        from langgraph.graph import StateGraph, START, END
        def make():
            inner = StateGraph(dict)
            inner.add_edge(START, "a")
            inner.add_edge("a", END)
        outer = StateGraph(dict)
        outer.add_edge(START, "b")
        outer.add_edge("b", END)
    """))
    edges = set(extract.from_langgraph(str(f)))
    assert ("START", "b") in edges and ("b", "END") in edges
    assert ("START[inner]", "a") in edges and ("a", "END[inner]") in edges
    assert ("START", "a") not in edges


def test_langgraph_ignores_dynamic_goto_and_nested_helpers(tmp_path):
    f = tmp_path / "g.py"
    f.write_text(textwrap.dedent("""
        from langgraph.types import Command
        from langgraph.graph import StateGraph, START, END
        def outer(state):
            def helper(state):
                return Command(goto="from_helper")
            nxt = "nope"
            return Command(goto=nxt)
        def real(state):
            return Command(goto="real_next")
        g = StateGraph(dict)
        g.add_node("outer", outer)
        g.add_node("real", real)
        g.add_edge(START, "outer")
        g.add_edge("real_next", END)
    """))
    edges = set(extract.from_langgraph(str(f)))
    assert ("real", "real_next") in edges
    assert ("outer", "from_helper") not in edges
    assert ("outer", "nxt") not in edges
    assert ("outer", "nope") not in edges


def test_langgraph_string_annotation_and_keyword_add_node(tmp_path):
    f = tmp_path / "g.py"
    f.write_text(textwrap.dedent('''
        from typing import Literal
        from langgraph.types import Command
        from langgraph.graph import StateGraph, START, END
        def node(state) -> "Command[Literal['next', '__end__']]":
            return Command(goto="next")
        g = StateGraph(dict)
        g.add_node(node="node", action=node)
        g.add_edge(START, "node")
        g.add_edge("next", END)
    '''))
    edges = set(extract.from_langgraph(str(f)))
    assert ("node", "next") in edges
    assert ("node", "END") in edges
    assert ("START", "node") in edges


def test_langgraph_inline_compile_mounts_subgraph(tmp_path):
    f = tmp_path / "g.py"
    f.write_text(textwrap.dedent("""
        from langgraph.graph import StateGraph, START, END
        sub = StateGraph(dict)
        sub.add_node("a", lambda s: s)
        sub.add_edge(START, "a")
        sub.add_edge("a", END)
        main = StateGraph(dict)
        main.add_node("child", sub.compile())
        main.add_edge(START, "child")
        main.add_edge("child", END)
    """))
    edges = set(extract.from_langgraph(str(f)))
    assert ("child", "a") in edges
    assert ("a", "child") in edges
    assert ("START", "child") in edges
    assert ("child", "END") in edges
    assert ("START", "a") not in edges


def test_terraform_same_name_resources_do_not_collapse(tmp_path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_vpc" "this" {\n  cidr_block = "10.0.0.0/16"\n}\n'
        'resource "aws_internet_gateway" "this" {\n  vpc_id = aws_vpc.this.id\n}\n'
        'resource "aws_subnet" "public" {\n  vpc_id = aws_vpc.this.id\n}\n'
        'resource "aws_route_table" "public" {\n  vpc_id = aws_vpc.this.id\n'
        '  gateway_id = aws_internet_gateway.this.id\n}\n'
    )
    edges = set(extract.from_terraform(str(tmp_path)))
    assert ("aws_internet_gateway.this", "aws_vpc.this") in edges
    assert ("aws_route_table.public", "aws_internet_gateway.this") in edges
    assert not any("this" == n for e in edges for n in e)


def test_terraform_unique_names_stay_short(tmp_path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_vpc" "main" {}\n'
        'resource "aws_subnet" "public" { vpc_id = aws_vpc.main.id }\n'
        'resource "aws_instance" "web" { subnet_id = aws_subnet.public.id }\n'
        'resource "aws_eip" "web" { instance = aws_instance.web.id }\n'
    )
    edges = set(extract.from_terraform(str(tmp_path)))
    assert ("public", "main") in edges
    assert ("aws_instance.web", "public") in edges
    assert ("aws_eip.web", "aws_instance.web") in edges
    assert not any(n == "web" for e in edges for n in e)


def test_from_directory_exclude_and_include(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    depends_on: [db]\n  db:\n    image: postgres\n"
    )
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "on: push\njobs:\n  build:\n    runs-on: x\n    steps: []\n"
        "  deploy:\n    needs: build\n    runs-on: x\n    steps: []\n"
    )
    edges, sources = extract.from_directory(str(tmp_path))
    assert "github-actions" in sources  # default behavior unchanged
    assert "docker-compose" in sources
    edges, sources = extract.from_directory(str(tmp_path), exclude={"github-actions"})
    assert sources == {"docker-compose": 1} and edges == [("web", "db")]
    edges, sources = extract.from_directory(str(tmp_path), include={"github-actions"})
    assert set(sources) == {"github-actions"}
    edges, sources = extract.from_directory(str(tmp_path), include="docker-compose")
    assert sources == {"docker-compose": 1} and edges == [("web", "db")]
    edges, sources = extract.from_directory(
        str(tmp_path), include=["docker-compose", "github-actions"], exclude="github-actions",
    )
    assert sources == {"docker-compose": 1}
    with pytest.raises(ValueError, match="github_actions"):
        extract.from_directory(str(tmp_path), exclude={"github_actions"})
