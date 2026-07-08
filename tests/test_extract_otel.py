"""Tests for depgraph_extract.extract.from_otel_traces.

Covers all three trace formats (OTLP / Jaeger / Zipkin) with fixtures that
encode the same logical topology: frontend -> auth -> db. Same-service spans
are dropped; only cross-service edges should appear.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from depgraph_extract import extract


FIXTURES = Path(__file__).parent / "fixtures"


class TestOtlp:
    def test_parses_otlp_resource_spans(self):
        edges = extract.from_otel_traces(str(FIXTURES / "trace_otlp.json"))
        edges_set = {tuple(e) for e in edges}
        assert ("frontend", "auth") in edges_set
        assert ("auth", "db") in edges_set
        # Intra-service spans (frontend->frontend, auth->auth) must be dropped
        assert ("frontend", "frontend") not in edges_set
        assert ("auth", "auth") not in edges_set

    def test_otlp_handles_legacy_instrumentation_library_spans(self, tmp_path):
        # Older OTLP exports use `instrumentationLibrarySpans` instead of `scopeSpans`.
        data = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "a"}}]},
                    "instrumentationLibrarySpans": [{"spans": [{"spanId": "x1", "parentSpanId": ""}]}],
                },
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "b"}}]},
                    "instrumentationLibrarySpans": [{"spans": [{"spanId": "x2", "parentSpanId": "x1"}]}],
                },
            ]
        }
        f = tmp_path / "legacy.json"
        f.write_text(json.dumps(data))
        edges = extract.from_otel_traces(str(f))
        assert ("a", "b") in {tuple(e) for e in edges}


class TestJaeger:
    def test_parses_jaeger_processes(self):
        edges = extract.from_otel_traces(str(FIXTURES / "trace_jaeger.json"))
        edges_set = {tuple(e) for e in edges}
        assert ("frontend", "auth") in edges_set
        assert ("auth", "db") in edges_set
        # Same-process spans (s1 -> s2 both in p1/frontend) drop out
        assert ("frontend", "frontend") not in edges_set


class TestZipkin:
    def test_parses_zipkin_local_endpoint(self):
        edges = extract.from_otel_traces(str(FIXTURES / "trace_zipkin.json"))
        edges_set = {tuple(e) for e in edges}
        assert ("frontend", "auth") in edges_set
        assert ("auth", "db") in edges_set


class TestFormatErrors:
    def test_unrecognized_format_raises(self, tmp_path):
        f = tmp_path / "weird.json"
        f.write_text(json.dumps({"some": "other shape"}))
        with pytest.raises(ValueError, match="not recognized"):
            extract.from_otel_traces(str(f))


class TestFromDirectoryPicksUpTraces:
    def test_traces_json_at_root(self, tmp_path):
        # Drop a Zipkin trace at the root; from_directory should pick it up.
        (tmp_path / "traces.json").write_text((FIXTURES / "trace_zipkin.json").read_text())
        edges, sources = extract.from_directory(str(tmp_path))
        assert "otel-traces" in sources
        assert ("frontend", "auth") in {tuple(e) for e in edges}

    def test_traces_directory(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()
        (traces_dir / "morning.json").write_text((FIXTURES / "trace_zipkin.json").read_text())
        edges, sources = extract.from_directory(str(tmp_path))
        assert "otel-traces" in sources
