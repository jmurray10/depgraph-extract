"""Tests for the AI agent framework extractors.

The extractors are pure AST parsers — they never import the framework
being analyzed. So these tests run without langgraph / crewai / autogen
installed.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from archgraph import extract


FIXTURES = Path(__file__).parent / "fixtures"


# ---------- LangGraph -----------------------------------------------------

class TestLangGraph:
    def test_parses_add_edge_and_conditional_and_entry_finish(self):
        edges = extract.from_langgraph(str(FIXTURES / "langgraph_app.py"))
        edges_set = {tuple(e) for e in edges}
        assert ("START", "planner") in edges_set
        assert ("planner", "researcher") in edges_set
        # conditional edges (router maps to writer & researcher)
        assert ("researcher", "writer") in edges_set
        assert ("researcher", "researcher") not in edges_set  # self-loops dropped
        assert ("writer", "critic") in edges_set
        assert ("critic", "END") in edges_set

    def test_handles_inline_string_edges(self, tmp_path):
        # Smoke test on a minimal graph defined inline.
        f = tmp_path / "g.py"
        f.write_text(textwrap.dedent("""
            from langgraph.graph import StateGraph
            g = StateGraph(dict)
            g.add_edge("a", "b")
            g.add_edge("b", "c")
        """))
        edges = extract.from_langgraph(str(f))
        assert {tuple(e) for e in edges} == {("a", "b"), ("b", "c")}

    def test_conditional_without_path_map_emits_no_edges(self, tmp_path):
        f = tmp_path / "g.py"
        f.write_text(textwrap.dedent("""
            from langgraph.graph import StateGraph
            g = StateGraph(dict)
            g.add_edge("a", "b")
            # No path_map dict, so we can't statically know targets:
            g.add_conditional_edges("b", some_router_fn)
        """))
        edges = extract.from_langgraph(str(f))
        assert {tuple(e) for e in edges} == {("a", "b")}


# ---------- CrewAI --------------------------------------------------------

class TestCrewAI:
    def test_parses_agent_to_task_and_context_and_manager(self):
        edges = extract.from_crewai(str(FIXTURES / "crewai_app.py"))
        edges_set = {tuple(e) for e in edges}

        # agent -> task assignments
        assert ("researcher", "research_task") in edges_set
        assert ("writer", "draft_task") in edges_set
        assert ("editor", "edit_task") in edges_set

        # context dependencies (other_task -> this_task)
        assert ("research_task", "draft_task") in edges_set
        assert ("draft_task", "edit_task") in edges_set

        # manager fan-out to crew members
        assert ("manager", "researcher") in edges_set
        assert ("manager", "writer") in edges_set
        assert ("manager", "editor") in edges_set

    def test_no_manager_no_fanout(self, tmp_path):
        f = tmp_path / "c.py"
        f.write_text(textwrap.dedent("""
            from crewai import Agent, Task, Crew
            r = Agent(role='r')
            w = Agent(role='w')
            t1 = Task(description='do', agent=r)
            t2 = Task(description='write', agent=w, context=[t1])
            crew = Crew(agents=[r, w], tasks=[t1, t2])
        """))
        edges = extract.from_crewai(str(f))
        edges_set = {tuple(e) for e in edges}
        assert ("r", "t1") in edges_set
        assert ("w", "t2") in edges_set
        assert ("t1", "t2") in edges_set
        # No manager -> no fan-out
        assert not any(s.startswith("manager") for s, _t in edges_set)


# ---------- AutoGen -------------------------------------------------------

class TestAutoGen:
    def test_parses_groupchat_with_manager_as_star(self):
        edges = extract.from_autogen(str(FIXTURES / "autogen_app.py"))
        edges_set = {tuple(e) for e in edges}
        # Manager -> each agent (star, not all-pairs, since manager is explicit)
        assert ("manager", "planner") in edges_set
        assert ("manager", "coder") in edges_set
        assert ("manager", "reviewer") in edges_set
        # agent->agent should NOT appear (the manager mediates)
        assert ("planner", "coder") not in edges_set
        # initiate_chat
        assert ("user_proxy", "manager") in edges_set

    def test_modern_round_robin_group_chat(self, tmp_path):
        # autogen-agentchat 0.4+: RoundRobinGroupChat([a, b, c])
        f = tmp_path / "rr.py"
        f.write_text(textwrap.dedent("""
            from autogen_agentchat.teams import RoundRobinGroupChat
            team = RoundRobinGroupChat([alpha, beta, gamma])
        """))
        edges = extract.from_autogen(str(f))
        # Round-robin chain: 3 agents -> 3 edges (a->b, b->c, c->a)
        assert {tuple(e) for e in edges} == {("alpha", "beta"), ("beta", "gamma"), ("gamma", "alpha")}

    def test_modern_selector_group_chat(self, tmp_path):
        f = tmp_path / "sel.py"
        f.write_text(textwrap.dedent("""
            from autogen_agentchat.teams import SelectorGroupChat
            team = SelectorGroupChat([planner, coder, reviewer], model_client=mc)
        """))
        edges = extract.from_autogen(str(f))
        # Selector can route between any pair -> fully connected (6 edges)
        assert len({tuple(e) for e in edges}) == 6

    def test_modern_swarm(self, tmp_path):
        f = tmp_path / "sw.py"
        f.write_text(textwrap.dedent("""
            from autogen_agentchat.teams import Swarm
            swarm = Swarm([researcher, writer, editor])
        """))
        edges = extract.from_autogen(str(f))
        assert len({tuple(e) for e in edges}) == 6

    def test_groupchat_without_manager_is_fully_connected(self, tmp_path):
        f = tmp_path / "ag.py"
        f.write_text(textwrap.dedent("""
            import autogen
            a = autogen.AssistantAgent(name='a')
            b = autogen.AssistantAgent(name='b')
            c = autogen.AssistantAgent(name='c')
            gc = autogen.GroupChat(agents=[a, b, c])
        """))
        edges = extract.from_autogen(str(f))
        edges_set = {tuple(e) for e in edges}
        # Fully connected: 3 nodes -> 6 directed edges (no self-loops)
        assert len(edges_set) == 6
        for s in ("a", "b", "c"):
            for t in ("a", "b", "c"):
                if s != t:
                    assert (s, t) in edges_set


# ---------- from_directory auto-detect ------------------------------------

class TestFromDirectoryAutoDetect:
    def test_picks_up_langgraph_file(self, tmp_path):
        shutil.copy(FIXTURES / "langgraph_app.py", tmp_path / "graph.py")
        edges, sources = extract.from_directory(str(tmp_path))
        assert "langgraph" in sources
        assert ("planner", "researcher") in {tuple(e) for e in edges}

    def test_picks_up_crewai_file(self, tmp_path):
        shutil.copy(FIXTURES / "crewai_app.py", tmp_path / "crew.py")
        edges, sources = extract.from_directory(str(tmp_path))
        assert "crewai" in sources
        assert ("researcher", "research_task") in {tuple(e) for e in edges}

    def test_picks_up_autogen_file(self, tmp_path):
        shutil.copy(FIXTURES / "autogen_app.py", tmp_path / "agents.py")
        edges, sources = extract.from_directory(str(tmp_path))
        assert "autogen" in sources
        assert ("manager", "planner") in {tuple(e) for e in edges}

    def test_skips_files_without_framework_imports(self, tmp_path):
        # A regular Python file without langgraph/crewai/autogen imports
        # must not be parsed by the AI extractors.
        (tmp_path / "regular.py").write_text(textwrap.dedent("""
            def add(a, b):
                return a + b
        """))
        _edges, sources = extract.from_directory(str(tmp_path))
        assert "langgraph" not in sources
        assert "crewai" not in sources
        assert "autogen" not in sources


# ---------- Robustness ----------------------------------------------------

class TestRobustness:
    def test_syntactically_invalid_python_raises(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("this is not python )(\n")
        with pytest.raises(SyntaxError):
            extract.from_langgraph(str(f))

    def test_invalid_file_does_not_break_from_directory(self, tmp_path):
        # A file that imports langgraph but is malformed should be silently
        # skipped by the from_directory auto-detect (it catches Exception).
        (tmp_path / "bad.py").write_text("from langgraph.graph import (\n")  # syntax error
        edges, sources = extract.from_directory(str(tmp_path))
        # Should not raise, and should report no langgraph edges.
        assert sources.get("langgraph", 0) == 0
        assert isinstance(edges, list)
