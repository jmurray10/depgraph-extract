"""Tests for archgraph.dedupe_edges.

Covers the four normalization modes, alias overrides, self-loop dropping,
and accepting all the edge formats the SDK supports (tuple/list/dict).
"""

from __future__ import annotations

import pytest

from archgraph import dedupe_edges


class TestExactDedupe:
    def test_drops_exact_duplicates(self):
        edges = [("a", "b"), ("a", "b"), ("b", "c")]
        assert dedupe_edges(edges) == [("a", "b"), ("b", "c")]

    def test_drops_self_loops_by_default(self):
        edges = [("a", "a"), ("a", "b"), ("c", "c")]
        assert dedupe_edges(edges) == [("a", "b")]

    def test_keeps_self_loops_when_disabled(self):
        edges = [("a", "a"), ("a", "b")]
        assert dedupe_edges(edges, drop_self_loops=False) == [("a", "a"), ("a", "b")]


class TestNormalizeSnake:
    def test_camel_case_to_snake(self):
        edges = [("AuthService", "Db"), ("auth_service", "db")]
        assert dedupe_edges(edges, normalize="snake") == [("auth_service", "db")]

    def test_dashes_become_underscores(self):
        edges = [("auth-svc", "db"), ("auth_svc", "db")]
        # Dashes are converted to underscores; both edges become identical.
        assert dedupe_edges(edges, normalize="snake") == [("auth_svc", "db")]

    def test_spaces_become_underscores(self):
        edges = [("Auth Service", "Db"), ("auth_service", "db")]
        assert dedupe_edges(edges, normalize="snake") == [("auth_service", "db")]

    def test_consecutive_separators_collapse(self):
        edges = [("auth--svc", "db"), ("auth-svc", "db")]
        assert dedupe_edges(edges, normalize="snake") == [("auth_svc", "db")]


class TestNormalizeLower:
    def test_lowercases_only(self):
        edges = [("Frontend", "AUTH"), ("frontend", "auth")]
        assert dedupe_edges(edges, normalize="lower") == [("frontend", "auth")]

    def test_lower_does_not_split_camel_case(self):
        edges = [("AuthService", "DB")]
        # `lower` does NOT split CamelCase; that's `snake`'s job.
        assert dedupe_edges(edges, normalize="lower") == [("authservice", "db")]


class TestNormalizeKebab:
    def test_underscores_become_dashes(self):
        edges = [("auth_service", "Db"), ("AuthService", "db")]
        assert dedupe_edges(edges, normalize="kebab") == [("auth-service", "db")]


class TestAliases:
    def test_aliases_applied_after_normalization(self):
        edges = [("auth-svc", "db"), ("authentication", "db")]
        out = dedupe_edges(
            edges,
            normalize="snake",
            aliases={"auth_svc": "auth", "authentication": "auth"},
        )
        assert out == [("auth", "db")]

    def test_aliases_alone_without_normalize(self):
        edges = [("auth-svc", "db"), ("auth", "db")]
        out = dedupe_edges(edges, aliases={"auth-svc": "auth"})
        assert out == [("auth", "db")]


class TestEdgeFormats:
    def test_accepts_tuples(self):
        assert dedupe_edges([("a", "b")]) == [("a", "b")]

    def test_accepts_lists(self):
        assert dedupe_edges([["a", "b"]]) == [("a", "b")]

    def test_accepts_dicts(self):
        assert dedupe_edges([{"source": "a", "target": "b"}]) == [("a", "b")]

    def test_accepts_dict_alt_keys(self):
        assert dedupe_edges([{"src": "a", "tgt": "b"}]) == [("a", "b")]
        assert dedupe_edges([{"from": "a", "to": "b"}]) == [("a", "b")]

    def test_accepts_weighted_tuples(self):
        # Third element (weight) is ignored — we dedupe on (source, target).
        out = dedupe_edges([("a", "b", 1.0), ("a", "b", 2.5)])
        assert out == [("a", "b")]

    def test_rejects_unrecognized_format(self):
        with pytest.raises(ValueError, match="Unrecognized edge format"):
            dedupe_edges(["not an edge"])


class TestRealWorldBlend:
    def test_combining_compose_and_traces(self):
        # Compose typically uses lowercase-with-dashes; traces often use CamelCase.
        compose_edges = [("frontend", "auth-svc"), ("auth-svc", "db")]
        trace_edges = [("Frontend", "AuthSvc"), ("AuthSvc", "Db"), ("AuthSvc", "Cache")]
        blended = dedupe_edges(compose_edges + trace_edges, normalize="snake")
        # Should have 4 unique edges (frontend->auth_svc, auth_svc->db, auth_svc->cache)
        # not 5, because the first two are duplicates after normalization.
        edges_set = {tuple(e) for e in blended}
        assert ("frontend", "auth_svc") in edges_set
        assert ("auth_svc", "db") in edges_set
        assert ("auth_svc", "cache") in edges_set
        assert len(blended) == 3


class TestInvalidNormalize:
    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="normalize must be"):
            dedupe_edges([("a", "b")], normalize="screaming-snake")
