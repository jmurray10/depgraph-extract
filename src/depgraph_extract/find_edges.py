"""Programmatic edge discovery — scan a directory, get an edge list.

``find_edges()`` runs the deterministic infrastructure scan first
(:func:`depgraph_extract.extract.from_directory`). That path is pure and local: it
reads recognized files and returns edges, and **nothing leaves your machine**.

An optional LLM fallback (``llm_fallback=True``) handles repos with no
recognized infrastructure files by sending file contents to Anthropic or
Google for extraction. It is **off by default** precisely because it egresses
data — you opt in explicitly, per call.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from . import extract


_LLM_SYSTEM = """You extract directed service-dependency edges from architecture artifacts.
Output ONLY a JSON object with a single key "edges": a list of [source, target] pairs.
Use the exact node names that appear in the source. Drop self-loops. No prose.
"""

_MAX_FILE_BYTES = 60_000  # per file, to control LLM context cost
_MAX_TOTAL_BYTES = 250_000  # across all files in a single LLM call


def find_edges(
    path: str = ".",
    *,
    llm_fallback: bool = False,
    provider: str = "claude",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    max_nodes: Optional[int] = None,
) -> tuple[list[tuple[str, str]], dict[str, int], list[str]]:
    """Discover directed dependency edges in ``path``.

    Returns ``(edges, sources, log)``:

    - ``edges`` — list of ``(source, target)`` tuples
    - ``sources`` — mapping of source name (e.g. ``"docker-compose"``) to edge count
    - ``log`` — human-readable steps taken (extraction path, LLM fallback, truncation)

    Strategy:

    1. Try :func:`depgraph_extract.extract.from_directory`. If any edges are found,
       return them (no LLM call, no data egress).
    2. Only if ``llm_fallback=True`` and the deterministic scan found nothing,
       read recognized text files under ``path`` (capped at ``_MAX_TOTAL_BYTES``)
       and ask the chosen LLM provider for an edge list. This sends file
       contents off your machine — hence opt-in.

    Args:
        path: Directory to scan.
        llm_fallback: If ``True``, fall back to an LLM when the deterministic
            scan finds nothing. Sends file contents to the provider. Default
            ``False`` (deterministic-only, no egress).
        provider: ``"claude"`` (Anthropic) or ``"gemini"`` (Google). Only used
            when ``llm_fallback`` is triggered.
        model: Override the default model for the chosen provider.
        api_key: Override the env var for the LLM provider key.
        max_nodes: If set, prune the result to at most this many nodes
            (BFS from the most-cited node), so it fits a known size budget.

    Raises:
        RuntimeError: if the LLM fallback is triggered but the SDK for that
            provider is not installed, or the API key is missing.
        ValueError: if ``provider`` is unknown.
    """
    log: list[str] = []

    # Step 1 — deterministic scan (pure, local, no egress)
    edges, sources = extract.from_directory(path)
    if edges:
        log.append(f"deterministic scan found {len(edges)} edges from {sources}")
        edges = [tuple(e) for e in edges]
        if max_nodes is not None:
            edges = _prune_to_max_nodes(edges, max_nodes, log)
        return edges, sources, log

    if not llm_fallback:
        log.append(
            "deterministic scan returned no edges; llm_fallback=False, so stopping "
            "(pass llm_fallback=True to send file contents to an LLM)"
        )
        return [], {}, log

    log.append("deterministic scan returned no edges; falling back to LLM extraction")

    # Step 2 — LLM fallback (opt-in; egresses file contents)
    files = _gather_files(path, log)
    if not files:
        log.append("no readable text files found; nothing to extract")
        return [], {}, log

    if provider == "claude":
        raw_edges = _extract_with_claude(files, model, api_key, log)
        sources = {"claude-llm": len(raw_edges)}
    elif provider == "gemini":
        raw_edges = _extract_with_gemini(files, model, api_key, log)
        sources = {"gemini-llm": len(raw_edges)}
    else:
        raise ValueError(f"Unknown provider: {provider!r} (expected 'claude' or 'gemini')")

    edges = [(str(s), str(t)) for s, t in raw_edges if s != t]
    if max_nodes is not None:
        edges = _prune_to_max_nodes(edges, max_nodes, log)
    return edges, sources, log


# --- Helpers ---------------------------------------------------------------


_SUPPORTED_GLOBS = (
    "**/docker-compose.yml",
    "**/docker-compose.yaml",
    "**/compose.yml",
    "**/compose.yaml",
    "**/k8s/*.yaml",
    "**/kubernetes/*.yaml",
    "**/manifests/*.yaml",
    "**/deploy/*.yaml",
    "**/deployments/*.yaml",
    "**/.github/workflows/*.yml",
    "**/.github/workflows/*.yaml",
    "**/*.tf",
    "**/package.json",
    "**/pyproject.toml",
    "**/Pipfile",
    "**/requirements.txt",
)


def _gather_files(path: str, log: list[str]) -> list[tuple[str, str]]:
    """Collect (relative_path, content) pairs for LLM context, with budget caps."""
    root = Path(path)
    seen: set[Path] = set()
    out: list[tuple[str, str]] = []
    total = 0
    for pattern in _SUPPORTED_GLOBS:
        for fp in root.glob(pattern):
            if fp in seen or not fp.is_file():
                continue
            seen.add(fp)
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(content) > _MAX_FILE_BYTES:
                content = content[:_MAX_FILE_BYTES]
                log.append(f"truncated {fp.relative_to(root)} to {_MAX_FILE_BYTES} bytes")
            if total + len(content) > _MAX_TOTAL_BYTES:
                log.append(
                    f"hit total byte cap ({_MAX_TOTAL_BYTES}); skipping remaining files"
                )
                return out
            total += len(content)
            try:
                rel = str(fp.relative_to(root))
            except ValueError:
                rel = str(fp)
            out.append((rel, content))
    log.append(f"gathered {len(out)} files, {total} bytes for LLM context")
    return out


def _build_prompt(files: list[tuple[str, str]]) -> str:
    parts = ["Files in this repo:\n"]
    for rel, content in files:
        parts.append(f"\n--- {rel} ---\n{content}")
    parts.append("\n\nReturn only the JSON object: {\"edges\": [[\"a\", \"b\"], ...]}.")
    return "".join(parts)


def _parse_edges_from_text(text: str) -> list[list[str]]:
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise RuntimeError("LLM response did not contain a JSON object")
    payload = json.loads(match.group())
    edges = payload.get("edges", [])
    return [list(e) for e in edges if isinstance(e, (list, tuple)) and len(e) >= 2]


def _extract_with_claude(
    files: list[tuple[str, str]],
    model: Optional[str],
    api_key: Optional[str],
    log: list[str],
) -> list[list[str]]:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "Claude provider requires `anthropic`. Install with: pip install anthropic"
        ) from e
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set and no api_key argument provided")

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model or "claude-sonnet-4-6",
        max_tokens=4096,
        system=_LLM_SYSTEM,
        messages=[{"role": "user", "content": _build_prompt(files)}],
    )
    text = msg.content[0].text
    log.append(f"claude returned {len(text)} chars; stop_reason={msg.stop_reason}")
    return _parse_edges_from_text(text)


def _extract_with_gemini(
    files: list[tuple[str, str]],
    model: Optional[str],
    api_key: Optional[str],
    log: list[str],
) -> list[list[str]]:
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            "Gemini provider requires `google-genai`. Install with: pip install google-genai"
        ) from e
    key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not set and no api_key argument provided")

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model or "gemini-2.5-flash",
        contents=_LLM_SYSTEM + "\n\n" + _build_prompt(files),
    )
    text = resp.text or ""
    log.append(f"gemini returned {len(text)} chars")
    return _parse_edges_from_text(text)


def _prune_to_max_nodes(
    edges: list[tuple[str, str]],
    max_nodes: int,
    log: list[str],
) -> list[tuple[str, str]]:
    """Keep edges that touch the top-N most-cited nodes (BFS from the busiest)."""
    if max_nodes <= 0:
        return edges
    nodes = {n for e in edges for n in e}
    if len(nodes) <= max_nodes:
        return edges

    cite_count: Counter[str] = Counter()
    for s, t in edges:
        cite_count[s] += 1
        cite_count[t] += 1
    if not cite_count:
        return edges

    # Build undirected adjacency for BFS.
    adj: dict[str, set[str]] = {}
    for s, t in edges:
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)

    seed = cite_count.most_common(1)[0][0]
    keep: set[str] = {seed}
    frontier = [seed]
    while frontier and len(keep) < max_nodes:
        # Visit neighbors in order of citation count (busiest first).
        next_frontier: list[str] = []
        candidates = sorted(
            (n for f in frontier for n in adj.get(f, ()) if n not in keep),
            key=lambda n: -cite_count[n],
        )
        for n in candidates:
            if len(keep) >= max_nodes:
                break
            keep.add(n)
            next_frontier.append(n)
        frontier = next_frontier

    pruned = [(s, t) for s, t in edges if s in keep and t in keep]
    log.append(
        f"pruned to {len(keep)}/{len(nodes)} nodes via BFS from '{seed}', "
        f"{len(pruned)}/{len(edges)} edges retained"
    )
    return pruned
