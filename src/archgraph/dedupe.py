"""Edge dedupe + name canonicalization for blending sources.

Combining edges from multiple extractors (compose + traces + Python imports)
typically yields the same logical service under several spellings —
``auth-svc``, ``auth_svc``, ``AuthService``. This module provides a single
helper to normalize names and remove duplicates so a blended graph encodes
cleanly.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _to_snake(name: str) -> str:
    """`AuthService` / `auth-svc` / `auth svc` -> `auth_service` / `auth_svc`."""
    s = _CAMEL_SPLIT_RE.sub("_", name)
    s = re.sub(r"[\s\-/]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower()


def _to_lower(name: str) -> str:
    return name.lower()


def _to_kebab(name: str) -> str:
    s = _to_snake(name)
    return s.replace("_", "-")


_NORMALIZERS = {
    "none": lambda n: n,
    "snake": _to_snake,
    "lower": _to_lower,
    "kebab": _to_kebab,
}


def dedupe_edges(
    edges: Iterable,
    *,
    normalize: str = "none",
    aliases: Optional[dict[str, str]] = None,
    drop_self_loops: bool = True,
) -> list[tuple[str, str]]:
    """Remove duplicate edges and (optionally) canonicalize node names.

    Args:
        edges: Iterable of edges. Accepts ``(s, t)``, ``(s, t, weight)``, lists,
            or dicts with ``source``/``target`` keys.
        normalize: One of:

            - ``"none"`` (default): names unchanged; only exact duplicates merged.
            - ``"snake"``: camelCase + dashes + spaces -> ``snake_case``.
            - ``"lower"``: lowercase only.
            - ``"kebab"``: same as snake but with dashes.

        aliases: Optional explicit map ``{variant: canonical}`` applied AFTER
            normalization. Use this when normalization isn't enough — e.g. you
            have ``auth-svc`` and ``authentication`` that should merge.
        drop_self_loops: If True (default), drop edges where source == target
            after normalization.

    Returns:
        Deduplicated list of ``(source, target)`` tuples in original order.

    Examples::

        # Combine extractor outputs and canonicalize.
        from_compose, _ = se.extract.from_directory(".")
        from_traces = se.extract.from_otel_traces("traces.json")
        all_edges = se.dedupe_edges(
            list(from_compose) + from_traces,
            normalize="snake",
            aliases={"auth_svc": "auth_service"},
        )
        result = se.encode(all_edges)
    """
    if normalize not in _NORMALIZERS:
        raise ValueError(
            f"normalize must be one of {list(_NORMALIZERS)}; got {normalize!r}"
        )
    norm = _NORMALIZERS[normalize]
    alias_map = aliases or {}

    def _canon(node: str) -> str:
        n = norm(str(node))
        return alias_map.get(n, n)

    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for e in edges:
        if isinstance(e, dict):
            s = e.get("source") or e.get("src") or e.get("from")
            t = e.get("target") or e.get("tgt") or e.get("to")
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            s, t = e[0], e[1]
        else:
            raise ValueError(f"Unrecognized edge format: {e!r}")

        if s is None or t is None:
            continue
        cs, ct = _canon(s), _canon(t)
        if drop_self_loops and cs == ct:
            continue
        key = (cs, ct)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
