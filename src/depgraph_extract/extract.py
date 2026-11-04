"""Extract edges from infrastructure and code files.

Supported formats:
    - docker-compose.yml (depends_on, links)
    - Kubernetes YAML (Service, Deployment, Ingress selectors)
    - GitHub Actions workflows (job needs)
    - Terraform (resource references)
    - Python imports (module dependency graph)
    - package.json (Node.js dependencies, monorepo workspaces)
    - pyproject.toml (PEP 621, dependency-groups, Poetry) and requirements.txt
    - Automatic directory scanning
"""

from __future__ import annotations

import ast
import glob
import json
import os
import re
from pathlib import Path
from typing import Any


def _load_yaml(path: str) -> Any:
    """Load a YAML file. Tries pyyaml, falls back to basic parsing."""
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for infrastructure parsing. "
            "Install it with: pip install pyyaml"
        )
    with open(path) as f:
        return list(yaml.safe_load_all(f))


def _load_single_yaml(path: str) -> Any:
    """Load a single-document YAML file."""
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for infrastructure parsing. "
            "Install it with: pip install pyyaml"
        )
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Docker Compose
# ---------------------------------------------------------------------------

def from_docker_compose(path: str = "docker-compose.yml") -> list[tuple[str, str]]:
    """Extract service dependency edges from a Docker Compose file.

    Parses ``depends_on`` and ``links`` to build a directed graph of
    service dependencies.

    Args:
        path: Path to docker-compose.yml (or docker-compose.yaml).

    Returns:
        List of (source, target) edge tuples.

    Example::

        edges = depgraph_extract.extract.from_docker_compose("docker-compose.yml")
    """
    data = _load_single_yaml(path)
    if not data or "services" not in data:
        raise ValueError(f"No 'services' key found in {path}")

    services = data["services"]
    edges: list[tuple[str, str]] = []

    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue

        # depends_on can be a list or a dict
        depends = svc_config.get("depends_on", [])
        if isinstance(depends, dict):
            depends = list(depends.keys())
        elif isinstance(depends, str):
            depends = [depends]
        for dep in depends:
            edges.append((svc_name, dep))

        # links
        links = svc_config.get("links", [])
        for link in links:
            target = link.split(":")[0]  # "db:database" -> "db"
            if target != svc_name:
                edges.append((svc_name, target))

    return _dedupe(edges)


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------

def from_kubernetes(path: str = ".") -> list[tuple[str, str]]:
    """Extract service dependency edges from Kubernetes YAML manifests.

    Scans for Deployment/Service/Ingress resources and infers edges from
    label selectors, service references, and ingress backends. A metadata
    name used by more than one object is qualified (``Service/frontend``,
    ``Deployment/frontend``) so that pair is not dropped as a self-loop.

    Args:
        path: Path to a YAML file or a directory containing YAML files.

    Returns:
        List of (source, target) edge tuples.

    Example::

        edges = depgraph_extract.extract.from_kubernetes("k8s/")
    """
    if os.path.isdir(path):
        files = (
            glob.glob(os.path.join(path, "**/*.yml"), recursive=True)
            + glob.glob(os.path.join(path, "**/*.yaml"), recursive=True)
        )
    else:
        files = [path]

    resources: list[dict] = []
    for f in files:
        try:
            docs = _load_yaml(f)
            for doc in docs:
                if isinstance(doc, dict) and doc.get("kind"):
                    resources.append(doc)
        except Exception:
            continue

    # Index resources by kind
    services: dict[str, dict] = {}
    deployments: dict[str, dict] = {}
    ingresses: list[dict] = []

    for res in resources:
        kind = res.get("kind", "")
        name = res.get("metadata", {}).get("name", "")
        if kind == "Service":
            services[name] = res
        elif kind in ("Deployment", "StatefulSet", "DaemonSet"):
            deployments[name] = res
        elif kind == "Ingress":
            ingresses.append(res)

    # A Service and its Deployment often share a metadata name. Qualify any
    # name that belongs to more than one object so that link is
    # ``Service/frontend -> Deployment/frontend`` instead of a dropped self-loop.
    # Names that appear once stay bare.
    named: list[tuple[str, str]] = []
    for svc_name in services:
        named.append(("Service", svc_name))
    for dep_name, dep in deployments.items():
        named.append((dep.get("kind") or "Deployment", dep_name))
    for ing in ingresses:
        named.append(("Ingress", ing.get("metadata", {}).get("name", "ingress")))
    name_counts: dict[str, int] = {}
    for _kind, name in named:
        name_counts[name] = name_counts.get(name, 0) + 1

    def _k8s_label(kind: str, name: str) -> str:
        if name_counts.get(name, 0) > 1:
            return f"{kind}/{name}"
        return name

    edges: list[tuple[str, str]] = []

    # Service -> workload via selector matching
    for svc_name, svc in services.items():
        svc_selector = svc.get("spec", {}).get("selector", {})
        if not svc_selector:
            continue
        src = _k8s_label("Service", svc_name)
        for dep_name, dep in deployments.items():
            dep_labels = (
                dep.get("spec", {})
                .get("template", {})
                .get("metadata", {})
                .get("labels", {})
            )
            if _selectors_match(svc_selector, dep_labels):
                dst = _k8s_label(dep.get("kind") or "Deployment", dep_name)
                edges.append((src, dst))

    # Ingress -> Service via backend references
    for ing in ingresses:
        ing_name = ing.get("metadata", {}).get("name", "ingress")
        src = _k8s_label("Ingress", ing_name)
        rules = ing.get("spec", {}).get("rules", [])
        for rule in rules:
            paths = rule.get("http", {}).get("paths", [])
            for p in paths:
                backend = p.get("backend", {})
                svc_ref = backend.get("service", {}).get("name") or backend.get("serviceName")
                if svc_ref:
                    edges.append((src, _k8s_label("Service", svc_ref)))

    # Env var references between deployments (SERVICE_HOST patterns)
    for dep_name, dep in deployments.items():
        containers = (
            dep.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        dep_kind = dep.get("kind") or "Deployment"
        for container in containers:
            for env in container.get("env", []):
                val = env.get("value", "")
                if isinstance(val, str):
                    for svc_name in services:
                        if svc_name in val and svc_name != dep_name:
                            edges.append((
                                _k8s_label(dep_kind, dep_name),
                                _k8s_label("Service", svc_name),
                            ))

    return _dedupe(edges)


def _selectors_match(selector: dict, labels: dict) -> bool:
    """Check if all selector key-value pairs exist in labels."""
    if not selector or not labels:
        return False
    return all(labels.get(k) == v for k, v in selector.items())


# ---------------------------------------------------------------------------
# GitHub Actions
# ---------------------------------------------------------------------------

def from_github_actions(path: str = ".github/workflows") -> list[tuple[str, str]]:
    """Extract job dependency edges from GitHub Actions workflow files.

    Parses ``needs`` fields to build a directed graph of CI/CD job
    dependencies.

    Args:
        path: Path to a workflow YAML file or the workflows directory.

    Returns:
        List of (source, target) edge tuples.

    Example::

        edges = depgraph_extract.extract.from_github_actions()
    """
    if os.path.isdir(path):
        files = glob.glob(os.path.join(path, "*.yml")) + glob.glob(
            os.path.join(path, "*.yaml")
        )
    else:
        files = [path]

    edges: list[tuple[str, str]] = []

    for f in files:
        try:
            data = _load_single_yaml(f)
        except Exception:
            continue
        if not data or not isinstance(data, dict):
            continue

        workflow_name = data.get("name", Path(f).stem)
        jobs = data.get("jobs", {})

        for job_name, job_config in jobs.items():
            if not isinstance(job_config, dict):
                continue
            needs = job_config.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]

            qualified = f"{workflow_name}/{job_name}"
            if needs:
                for dep in needs:
                    edges.append((f"{workflow_name}/{dep}", qualified))
            else:
                # Root job triggered by workflow
                edges.append((workflow_name, qualified))

    return _dedupe(edges)


# ---------------------------------------------------------------------------
# Terraform
# ---------------------------------------------------------------------------

def from_terraform(path: str = ".") -> list[tuple[str, str]]:
    """Extract resource dependency edges from Terraform files.

    Parses ``resource`` and ``module`` blocks. A block depends on every
    ``type.name`` resource or ``module.name`` output it references. Each
    block body stops at its own closing brace, so later blocks are not
    attributed to the previous resource.

    Node labels are the resource or module name (``web``, ``main``). When
    that name is shared, the label is the full address (``aws_vpc.this``,
    ``module.vpc``).

    Args:
        path: Path to a .tf file or a directory containing .tf files.

    Returns:
        List of (source, target) edge tuples.

    Example::

        edges = depgraph_extract.extract.from_terraform("infra/")
    """
    if os.path.isdir(path):
        files = glob.glob(os.path.join(path, "**/*.tf"), recursive=True)
    else:
        files = [path]

    # `type.name` or `module.name`, optionally indexed, then an attribute.
    # e.g. `aws_subnet.this[0].id`, `module.vpc.vpc_id`.
    ref_pattern = re.compile(r"((?:module\.)?\w+\.\w+)(?:\[[^\]]*\])?\.")

    addresses: dict[str, str] = {}  # full address -> short label
    blocks: list[tuple[str, str]] = []  # (address, body)

    for f in files:
        try:
            with open(f) as fh:
                content = fh.read()
        except Exception:
            continue
        for address, short, body in _tf_blocks(content):
            addresses[address] = short
            blocks.append((address, body))

    name_counts: dict[str, int] = {}
    for short in addresses.values():
        name_counts[short] = name_counts.get(short, 0) + 1
    for full, short in list(addresses.items()):
        if name_counts[short] > 1:
            addresses[full] = full

    edges: list[tuple[str, str]] = []
    for address, body in blocks:
        if address not in addresses:
            continue
        src_label = addresses[address]
        for ref_match in ref_pattern.finditer(body):
            ref = ref_match.group(1)
            if ref in addresses and ref != address:
                edges.append((src_label, addresses[ref]))

    return _dedupe(edges)


def _tf_blocks(content: str):
    """Yield ``(address, short_name, body)`` for resource and module blocks.

    The body is the text between the header's opening brace and its matching
    close, so a later ``module`` is not part of the previous ``resource``.
    """
    header = re.compile(
        r'(resource|module)\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{'
    )
    pos = 0
    while True:
        match = header.search(content, pos)
        if not match:
            break
        kind, first, second = match.group(1), match.group(2), match.group(3)
        if kind == "resource" and second:
            address, short = f"{first}.{second}", second
        elif kind == "module":
            address, short = f"module.{first}", first
        else:
            pos = match.end()
            continue
        body_start = match.end()
        end = _tf_block_end(content, body_start)
        yield address, short, content[body_start:end]
        pos = end + 1


def _tf_skip_heredoc(content: str, start: int) -> int:
    """Return the index just after a ``<<MARKER`` / ``<<-MARKER`` body."""
    i = start + 2
    n = len(content)
    if i < n and content[i] == "-":
        i += 1
    quote = ""
    if i < n and content[i] in {'"', "'"}:
        quote = content[i]
        i += 1
    marker_start = i
    while i < n and (content[i].isalnum() or content[i] == "_"):
        i += 1
    marker = content[marker_start:i]
    if not marker:
        return start + 2
    if quote and i < n and content[i] == quote:
        i += 1
    line = content.find("\n", i)
    if line < 0:
        return n
    line += 1
    while line < n:
        line_end = content.find("\n", line)
        if line_end < 0:
            line_end = n
        if content[line:line_end].strip() == marker:
            return line_end + 1 if line_end < n else n
        line = line_end + 1
    return n


def _tf_block_end(content: str, start: int) -> int:
    """Index of the closing brace for a block whose body starts at ``start``."""
    depth = 1
    i = start if isinstance(start, int) else 0
    n = len(content)
    while i < n and depth:
        ch = content[i]
        if ch == "#":
            nl = content.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < n and content[i + 1] == "/":
            nl = content.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < n and content[i + 1] == "*":
            end = content.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if content.startswith("<<", i):
            i = _tf_skip_heredoc(content, i)
            continue
        if ch in {'"', "'"}:
            quote = ch
            i += 1
            while i < n:
                if content[i] == "\\" and quote == '"':
                    i += 2
                    continue
                if content[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


# ---------------------------------------------------------------------------
# Python imports
# ---------------------------------------------------------------------------

def _import_modules(tree: ast.AST) -> list[str]:
    """Module names mentioned by ``import`` / ``from`` statements.

    Relative imports keep their leading dots (``".scaffold"``, ``"..typing"``).
    ``from . import foo`` is recorded as ``".foo"`` so a sibling submodule is
    visible. Imported symbols (``from pkg.mod import name``) are not modules.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            dots = "." * (node.level or 0)
            if node.module:
                found.append(dots + node.module)
            else:
                for alias in node.names:
                    if alias.name and alias.name != "*":
                        found.append(f"{dots}{alias.name}")
    return found


def _resolve_relative(src_mod: str, is_package: bool, imported: str) -> str | None:
    """Resolve ``from .foo`` / ``from ..bar`` against ``src_mod``'s package."""
    level = 0
    while level < len(imported) and imported[level] == ".":
        level += 1
    rest = imported[level:]
    parts = src_mod.split(".") if src_mod else []
    package = parts if is_package else parts[:-1]
    # level 1 is the current package. A module scanned at the package root
    # has an empty package prefix (`app.py` -> ``app``), and ``from .x`` is
    # still the sibling ``x``.
    if level - 1 > len(package):
        return None
    base = package[: len(package) - (level - 1)]
    if rest:
        return ".".join([*base, *rest.split(".")]) if base else rest
    return ".".join(base) if base else None


def _import_candidates(imported: str, src_mod: str, is_package: bool) -> list[list[str]]:
    if imported.startswith("."):
        resolved = _resolve_relative(src_mod, is_package, imported)
        if not resolved:
            return []
        return [resolved.split(".")]
    parts = imported.split(".")
    candidates = [parts]
    if len(parts) > 1:
        candidates.append(parts[1:])
    return candidates


def from_python_imports(path: str = ".", *, depth: int | None = None) -> list[tuple[str, str]]:
    """Extract module dependency edges from Python import statements.

    Scans ``.py`` files and builds a directed graph of which modules import
    which. Only includes edges between modules that exist in the scanned
    directory (ignores stdlib and third-party imports).

    Args:
        path: Path to a Python file or directory of Python files.
        depth: How aggressively to roll up module names to service boundaries.

            - ``None`` (default): use the last component of each module path.
              ``myapp.auth.user`` -> ``user``. Backwards-compatible.
            - ``1``: keep only the top-level package. ``myapp.auth.user`` ->
              ``myapp``. Edges between submodules of the same top-level package
              collapse to self-loops and are dropped. Useful for monorepos
              where the top-level dirs are services.
            - ``2``: first two components. ``services.auth.user`` ->
              ``services.auth``. Useful when services live one level down,
              e.g. ``services/auth/``, ``services/payments/``.
            - ``N`` for any N: first N components.

    Returns:
        List of (importer, imported) edge tuples using rolled-up module names.

    Example::

        # Default: short names
        edges = depgraph_extract.extract.from_python_imports("src/")

        # Service-boundary rollup for a monorepo with services/<svc>/...
        edges = depgraph_extract.extract.from_python_imports(".", depth=2)

    """
    if os.path.isfile(path):
        files = [path]
        base = os.path.dirname(os.path.abspath(path))
    else:
        files = glob.glob(os.path.join(path, "**/*.py"), recursive=True)
        base = os.path.abspath(path)

    # Build set of local module names from file paths.
    # A directory scan rooted on a package (it contains ``__init__.py``)
    # names that file after the directory and prefixes siblings, so
    # ``from .core`` resolves to ``<pkg>.core``.
    pkg_root = ""
    if os.path.isdir(path) and os.path.isfile(os.path.join(base, "__init__.py")):
        pkg_root = os.path.basename(base.rstrip(os.sep))
    local_modules: set[str] = set()
    file_to_module: dict[str, str] = {}
    for f in files:
        rel = os.path.relpath(f, base).replace(os.sep, "/")
        if rel == "__init__.py" and pkg_root:
            mod = pkg_root
        elif rel.endswith("/__init__.py"):
            mod = rel[:-12].replace("/", ".")
            if pkg_root:
                mod = f"{pkg_root}.{mod}" if mod else pkg_root
        elif rel.endswith(".py"):
            mod = rel[:-3].replace("/", ".")
            if pkg_root:
                mod = f"{pkg_root}.{mod}"
        else:
            continue
        # Skip hidden/test/venv directories
        parts = mod.split(".")
        if any(p.startswith(".") or p == "__pycache__" for p in parts):
            continue
        local_modules.add(mod)
        # Also add each parent package
        for i in range(1, len(parts)):
            local_modules.add(".".join(parts[:i]))
        file_to_module[f] = mod

    edges: list[tuple[str, str]] = []
    for f, src_mod in file_to_module.items():
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except Exception:
            continue

        try:
            tree = ast.parse(content, filename=f)
        except SyntaxError:
            continue
        is_pkg = f.endswith("__init__.py")
        for imported in _import_modules(tree):
            if not imported:
                continue
            # Absolute imports try the full name, then a name with the first
            # component stripped (scanning inside `myapp/` while the source says
            # `from myapp.models.user`). Relative imports (`from .foo`, `from ..bar`)
            # resolve against the importing module's package.
            target = None
            for candidate_parts in _import_candidates(imported, src_mod, is_pkg):
                for i in range(len(candidate_parts), 0, -1):
                    candidate = ".".join(candidate_parts[:i])
                    if candidate in local_modules and candidate != src_mod:
                        target = candidate
                        break
                if target:
                    break
            if target:
                if depth is None:
                    # Default: short names (last component) for readability
                    src_short = src_mod.split(".")[-1]
                    dst_short = target.split(".")[-1]
                    # If short names collide, use longer qualified names
                    if src_short == dst_short:
                        src_short = src_mod
                        dst_short = target
                    edges.append((src_short, dst_short))
                else:
                    # Roll up to first `depth` components.
                    src_roll = ".".join(src_mod.split(".")[:depth])
                    dst_roll = ".".join(target.split(".")[:depth])
                    edges.append((src_roll, dst_roll))

    # _dedupe drops self-loops (which depth rollup naturally creates within
    # a single top-level package) and exact duplicates.
    return _dedupe(edges)


# ---------------------------------------------------------------------------
# package.json (Node.js)
# ---------------------------------------------------------------------------

def from_package_json(path: str = "package.json") -> list[tuple[str, str]]:
    """Extract dependency edges from a package.json file.

    Parses ``dependencies``, ``devDependencies``, and ``peerDependencies``.
    The project name is the source node, each dependency is a target.

    Args:
        path: Path to package.json.

    Returns:
        List of (project, dependency) edge tuples.

    Example::

        edges = depgraph_extract.extract.from_package_json("package.json")
    """
    import json as _json

    with open(path) as f:
        data = _json.load(f)

    project_name = data.get("name", "project")
    edges: list[tuple[str, str]] = []

    for dep_key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(dep_key, {})
        if not isinstance(deps, dict):
            continue
        for dep_name in deps:
            edges.append((project_name, dep_name))

    return _dedupe(edges)


def from_package_json_workspaces(path: str = ".") -> list[tuple[str, str]]:
    """Extract inter-package dependency edges from a monorepo with workspaces.

    Scans all package.json files in the workspace and finds edges between
    local packages (where one workspace package depends on another).

    Args:
        path: Root directory of the monorepo.

    Returns:
        List of (package, dependency) edge tuples between local packages.

    Example::

        edges = depgraph_extract.extract.from_package_json_workspaces(".")
    """
    import json as _json

    pkg_files = glob.glob(os.path.join(path, "**/package.json"), recursive=True)

    # Build index of local package names
    local_packages: dict[str, str] = {}  # name -> path
    package_data: dict[str, dict] = {}  # name -> parsed json

    for pf in pkg_files:
        # Skip node_modules
        if "node_modules" in pf:
            continue
        try:
            with open(pf) as f:
                data = _json.load(f)
        except Exception:
            continue
        name = data.get("name")
        if name:
            local_packages[name] = pf
            package_data[name] = data

    # Find edges between local packages
    edges: list[tuple[str, str]] = []
    for pkg_name, data in package_data.items():
        for dep_key in ("dependencies", "devDependencies", "peerDependencies"):
            deps = data.get(dep_key, {})
            if not isinstance(deps, dict):
                continue
            for dep_name in deps:
                if dep_name in local_packages and dep_name != pkg_name:
                    edges.append((pkg_name, dep_name))

    return _dedupe(edges)


_DIST_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def _dist_name(spec: str) -> str | None:
    """Package name at the start of a PEP 508 requirement specifier."""
    text = spec.strip().strip("\"'")
    if not text or text.startswith(("-", ".", "#")):
        return None
    match = _DIST_NAME_RE.match(text)
    if not match:
        return None
    name = match.group(0)
    if name.lower() in {"python"}:
        return None
    return name


def _toml_code(line: str) -> str:
    """Drop a TOML comment that is not inside a quoted string."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote == '"':
                if i + 1 < len(line):
                    out.append(line[i + 1])
                    i += 2
                    continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def _toml_strings(text: str) -> list[str]:
    """String literals in a TOML snippet, basic and literal, single line."""
    found: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch not in {"'", '"'}:
            i += 1
            continue
        if text.startswith(ch * 3, i):
            end = text.find(ch * 3, i + 3)
            if end == -1:
                break
            found.append(text[i + 3:end])
            i = end + 3
            continue
        end = i + 1
        buf: list[str] = []
        while end < len(text) and text[end] != ch:
            if text[end] == "\\" and ch == '"':
                end += 2
                continue
            buf.append(text[end])
            end += 1
        if end >= len(text):
            break
        found.append("".join(buf) if ch == '"' else text[i + 1:end])
        i = end + 1
    return found


def from_pyproject(path: str = "pyproject.toml") -> list[tuple[str, str]]:
    """Extract package dependency edges from a ``pyproject.toml``.

    Reads the project name and requirement lists from:

    - ``[project] dependencies`` and ``[project.optional-dependencies]`` (PEP 621)
    - ``[dependency-groups]`` (PEP 735)
    - ``[tool.poetry.dependencies]`` and ``[tool.poetry.group.*.dependencies]``

    Each requirement becomes ``project -> distribution``. The ``python``
    interpreter bound is not a package edge. ``dynamic = ["dependencies"]``
    is not resolved — there is nothing static to read.

    Args:
        path: Path to ``pyproject.toml``.

    Returns:
        Deduped ``(project, dependency)`` edges. An empty list when the file
        declares no static requirements.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    project: str | None = None
    deps: list[str] = []
    section = ""
    collecting: list[str] | None = None
    poetry_deps = False

    def _close_array() -> None:
        nonlocal collecting
        collecting = None

    for raw in text.splitlines():
        line = _toml_code(raw)
        if not line:
            continue
        if line.startswith("[") and line.endswith("]") and collecting is None:
            section = line[1:-1].strip().strip('"').strip("'")
            poetry_deps = (
                section == "tool.poetry.dependencies"
                or (
                    section.startswith("tool.poetry.group.")
                    and section.endswith(".dependencies")
                )
            )
            continue
        if collecting is not None:
            collecting.append(line)
            if "]" in line:
                blob = " ".join(collecting)
                deps.extend(_dist_name(item) or "" for item in _toml_strings(blob))
                _close_array()
            continue
        if section in {"project", "tool.poetry"} and "=" in line:
            name_key, name_value = line.split("=", 1)
            if name_key.strip().strip('"').strip("'") == "name":
                strings = _toml_strings(name_value)
                if strings:
                    project = strings[0]
                continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().strip('"').strip("'")
        value = value.strip()
        array_keys = section in {
            "project.optional-dependencies",
            "dependency-groups",
        } or (section == "project" and key == "dependencies")
        if array_keys and value.startswith("["):
            if "]" in value:
                deps.extend(_dist_name(item) or "" for item in _toml_strings(value))
            else:
                collecting = [value]
            continue
        if poetry_deps and key.lower() != "python":
            name = _dist_name(key)
            if name:
                deps.append(name)

    if not project:
        project = Path(path).parent.name
    edges = [(project, dep) for dep in deps if dep]
    return _dedupe(edges)


def from_requirements(path: str = "requirements.txt") -> list[tuple[str, str]]:
    """Extract dependency edges from a pip ``requirements.txt``.

    The source node is the parent directory name (the project checkout).
    Each requirement line becomes ``project -> distribution``. Comments,
    options, ``-r`` / ``-c`` includes, and editable self-installs (``.``)
    are skipped. ``-r other.txt`` is followed when it stays inside the
    same directory.

    Args:
        path: Path to a requirements file.

    Returns:
        Deduped ``(project, dependency)`` edges.
    """
    root = os.path.abspath(path)
    project = Path(root).parent.name
    edges: list[tuple[str, str]] = []
    seen_files: set[str] = set()

    def _read(file_path: str) -> None:
        file_path = os.path.abspath(file_path)
        if file_path in seen_files or not os.path.isfile(file_path):
            return
        seen_files.add(file_path)
        try:
            with open(file_path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith(("-r", "--requirement")):
                parts = line.split()
                include = parts[-1]
                _read(os.path.join(os.path.dirname(file_path), include))
                continue
            if line.startswith(("-c", "--constraint", "-e", "--editable", "-f", "--find-links", "--extra-index-url", "--index-url", "-i")):
                spec = line.split(None, 1)[1].strip() if " " in line else ""
                if spec in {".", "./"} or spec.startswith(".[") or spec.startswith("./["):
                    continue
                name = _dist_name(spec)
                if name:
                    edges.append((project, name))
                continue
            if line.startswith("-"):
                continue
            name = _dist_name(line)
            if name:
                edges.append((project, name))

    _read(root)
    return _dedupe(edges)


# ---------------------------------------------------------------------------
# Infrastructure-as-code: CloudFormation, AWS CDK (Python), Pulumi (Python)
# ---------------------------------------------------------------------------


def from_cloudformation(path: str) -> list[tuple[str, str]]:
    """Extract resource dependency edges from a CloudFormation template.

    Edges represent dependency: a resource's ``DependsOn`` list AND any
    intrinsic references (``Ref``, ``Fn::GetAtt``, ``Fn::Sub``) inside its
    Properties produce a ``this_resource -> referenced_resource`` edge.

    Accepts either YAML (``.yaml`` / ``.yml``) or JSON templates. Pass a
    directory to merge edges across all templates within it.

    Args:
        path: Path to a single template OR a directory of templates.

    Returns:
        Deduped list of ``(dependent, dependency)`` edges.

    Example::

        edges = depgraph_extract.extract.from_cloudformation("infra/")
    """
    files: list[str] = []
    is_dir = os.path.isdir(path)
    if is_dir:
        for ext in ("*.yaml", "*.yml", "*.json", "*.template"):
            files.extend(glob.glob(os.path.join(path, "**", ext), recursive=True))
    elif os.path.isfile(path):
        files = [path]

    edges: list[tuple[str, str]] = []
    for fp in files:
        # When scanning a directory we may pick up arbitrary YAML/JSON that isn't
        # CFN — gate on a cheap header probe so we don't load every k8s manifest
        # / service definition / npm config in the tree.
        if is_dir:
            try:
                with open(fp, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(2048)
            except OSError:
                continue
            if (
                "AWSTemplateFormatVersion" not in head
                and '"Resources"' not in head
                and "\nResources:" not in head
                and not head.lstrip().startswith("Resources:")
            ):
                continue
        try:
            doc = _load_cfn_doc(fp)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        resources = doc.get("Resources") or doc.get("resources")
        if not isinstance(resources, dict):
            continue
        for res_name, res_config in resources.items():
            if not isinstance(res_config, dict):
                continue
            # Explicit DependsOn
            dep = res_config.get("DependsOn") or res_config.get("dependsOn")
            if isinstance(dep, str):
                edges.append((res_name, dep))
            elif isinstance(dep, list):
                for d in dep:
                    if isinstance(d, str):
                        edges.append((res_name, d))
            # Implicit references inside Properties
            props = res_config.get("Properties") or res_config.get("properties") or {}
            for ref in _cfn_walk_refs(props, set(resources.keys())):
                if ref != res_name:
                    edges.append((res_name, ref))

    return _dedupe(edges)


def _load_cfn_doc(path: str):
    """Load a CFN template — JSON if .json, else YAML (with intrinsic-tag support)."""
    if path.endswith(".json"):
        import json
        with open(path) as f:
            return json.load(f)
    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML is required to parse CloudFormation templates.")

    # CFN YAML uses short intrinsic tags like `!Ref` and `!GetAtt`. The default
    # SafeLoader chokes on these; we register no-op constructors that yield a
    # tagged dict so the walker can detect them.
    class _CfnLoader(yaml.SafeLoader):
        pass

    def _intrinsic_constructor(tag):
        def _ctor(loader, node):
            if isinstance(node, yaml.ScalarNode):
                return {tag: loader.construct_scalar(node)}
            if isinstance(node, yaml.SequenceNode):
                return {tag: loader.construct_sequence(node, deep=True)}
            if isinstance(node, yaml.MappingNode):
                return {tag: loader.construct_mapping(node, deep=True)}
            return None
        return _ctor

    for short in ("Ref", "GetAtt", "Sub", "Join", "Select", "Split",
                  "FindInMap", "ImportValue", "If", "And", "Or", "Not", "Equals",
                  "Base64", "Cidr", "Transform", "Condition"):
        _CfnLoader.add_constructor(f"!{short}", _intrinsic_constructor(f"Fn::{short}"))
    _CfnLoader.add_constructor("!Ref", _intrinsic_constructor("Ref"))

    with open(path) as f:
        return yaml.load(f, Loader=_CfnLoader)


def _cfn_walk_refs(obj, valid_names: set[str]) -> set[str]:
    """Walk a Properties object and collect resource names referenced via
    Ref / Fn::GetAtt / Fn::Sub. Only names that exist as Resources in the
    template are returned."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "Ref" and isinstance(v, str) and v in valid_names:
                found.add(v)
            elif k == "Fn::GetAtt":
                # Ref form: ["LogicalName", "Attribute"] OR "LogicalName.Attribute"
                if isinstance(v, list) and v and isinstance(v[0], str):
                    if v[0] in valid_names:
                        found.add(v[0])
                elif isinstance(v, str):
                    name = v.split(".")[0]
                    if name in valid_names:
                        found.add(name)
            elif k == "Fn::Sub":
                # `${LogicalName}` or `${LogicalName.Attribute}`
                template = v[0] if isinstance(v, list) and v else v
                if isinstance(template, str):
                    found.update(_extract_sub_refs(template, valid_names))
            else:
                found.update(_cfn_walk_refs(v, valid_names))
    elif isinstance(obj, list):
        for item in obj:
            found.update(_cfn_walk_refs(item, valid_names))
    return found


def _extract_sub_refs(template: str, valid_names: set[str]) -> set[str]:
    """Pull `${Foo}` / `${Foo.Bar}` references out of a Fn::Sub string."""
    out: set[str] = set()
    for m in re.finditer(r"\$\{([A-Za-z][A-Za-z0-9]*)(?:\.[A-Za-z0-9]+)?\}", template):
        name = m.group(1)
        if name in valid_names:
            out.add(name)
    return out


def from_aws_cdk(path: str) -> list[tuple[str, str]]:
    """Extract construct dependency edges from an AWS CDK Python file.

    Recognizes common patterns where a construct receives other constructs
    via keyword arguments:

    - ``fn = aws_lambda.Function(self, "Fn", role=my_role, vpc=my_vpc)``
        emits ``fn -> my_role`` and ``fn -> my_vpc``.
    - Any kwarg whose value is a Name/Attribute referring to a previously
      assigned construct variable produces an edge.

    TypeScript CDK is not supported in this release.

    Args:
        path: Path to a Python CDK app file.

    Returns:
        Deduped list of ``(construct, dependency)`` edges.
    """
    return _python_resource_graph(path, _looks_like_cdk_call)


def from_pulumi(path: str) -> list[tuple[str, str]]:
    """Extract resource dependency edges from a Pulumi Python program.

    Pulumi resource constructors look like CDK constructs but with the
    resource name (a string) as the first positional arg:

    - ``bucket = aws.s3.Bucket("my-bucket", website=site_cfg)``
        emits ``bucket -> site_cfg``.

    The same kwarg-reference rule applies: any kwarg whose value resolves
    to a previously assigned variable becomes an edge.

    Args:
        path: Path to a Pulumi Python file (typically ``__main__.py``).

    Returns:
        Deduped list of edges.
    """
    return _python_resource_graph(path, _looks_like_pulumi_call)


def _name_root(expr: ast.expr) -> str | None:
    cur: ast.expr = expr
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Name):
        return cur.id
    return None


def _construct_refs(expr: ast.expr, construct_vars: set[str]) -> list[str]:
    """Construct variables mentioned in an expression, including ``group.id``."""
    found: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Name) and node.id in construct_vars:
            found.append(node.id)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(expr)
    return found


def _python_resource_graph(
    path: str,
    is_resource_call,
) -> list[tuple[str, str]]:
    """Shared helper for CDK / Pulumi Python parsing.

    Collects ``var = module.Class(...)`` assignments whose call shape matches
    ``is_resource_call``. A construct depends on another when:

    - a constructor keyword mentions that variable (``group.id``, a list of
      them, or a helper call that receives them), or
    - a method on the construct does (``rule.add_target(LambdaFunction(fn))``,
      ``node.add_dependency(other)``).

    The edge is ``var -> referenced_var``.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    construct_vars: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
            continue
        if is_resource_call(node.value):
            construct_vars.add(target.id)

    edges: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Call) and is_resource_call(node.value):
                var_name = target.id
                for kw in node.value.keywords:
                    if not kw.arg:
                        continue
                    for ref in _construct_refs(kw.value, construct_vars):
                        if ref != var_name:
                            edges.append((var_name, ref))
                continue
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = _name_root(node.func)
        if owner not in construct_vars:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            for ref in _construct_refs(arg, construct_vars):
                if ref != owner:
                    edges.append((owner, ref))

    return _dedupe(edges)


def _is_construct_class(func: ast.expr) -> bool:
    """Resource classes are PascalCase. ``path.join`` and ``get_ami`` are not."""
    if isinstance(func, ast.Name):
        return func.id[:1].isupper()
    if isinstance(func, ast.Attribute):
        return func.attr[:1].isupper()
    return False


def _looks_like_cdk_call(call: ast.Call) -> bool:
    """Heuristic: AWS CDK constructs are called with `(scope, id, **kwargs)`.

    The callee may be ``aws_lambda.Function`` or a bare name imported as
    ``from backend.api import API``. The construct id is a string literal.
    """
    if not _is_construct_class(call.func):
        return False
    if len(call.args) < 2:
        return False
    second = call.args[1]
    return isinstance(second, ast.Constant) and isinstance(second.value, str)


def _pulumi_name_arg(expr: ast.expr) -> bool:
    """A Pulumi resource name: a literal, an f-string, or a variable."""
    if isinstance(expr, ast.Constant):
        return isinstance(expr.value, str)
    return isinstance(expr, (ast.JoinedStr, ast.Name))


def _looks_like_pulumi_call(call: ast.Call) -> bool:
    """Heuristic: Pulumi resources are called with `(name, **kwargs)`.

    ``aws.s3.Bucket("web")``, ``aws.s3.Bucket(f"web-{i}")``, and
    ``s3.BucketObject(filename, bucket=bucket.id)`` all count. The name is
    the first positional argument.
    """
    if not _is_construct_class(call.func) or not isinstance(call.func, ast.Attribute):
        return False
    if not call.args:
        return False
    return _pulumi_name_arg(call.args[0])


# ---------------------------------------------------------------------------
# OpenTelemetry traces (OTLP / Jaeger / Zipkin)
# ---------------------------------------------------------------------------

def from_otel_traces(path: str) -> list[tuple[str, str]]:
    """Extract service-level call edges from a trace export (JSON).

    Supports three formats — auto-detected by structure:

    - **OTLP JSON**: `{"resourceSpans": [{"scopeSpans": [{"spans": [...]}]}]}`
      (Default OpenTelemetry Collector / SDK export.)
    - **Jaeger**: `{"data": [{"spans": [...], "processes": {...}}]}`
      (`jaeger-query` HTTP API and `jaeger-cli`), or one trace object
      with ``spans`` and ``processes`` and no ``data`` wrapper.
    - **Zipkin**: a JSON array of spans, or one span object, each with
      ``traceId`` and ``localEndpoint.serviceName`` (Zipkin v2).

    Edges are service-level. A parent span in service A with a child in
    service B produces ``A -> B``. A span's ``remoteEndpoint`` is the other
    side of that call: a client/producer span points at the remote service,
    and a server/consumer span is pointed at by the remote service.
    Same-service spans and self-loops are dropped.

    Args:
        path: Path to the trace JSON file.

    Returns:
        Deduplicated list of ``(source_service, target_service)`` edges.

    Example::

        edges = depgraph_extract.extract.from_otel_traces("traces.json")
    """
    import json

    with open(path) as f:
        data = json.load(f)

    if isinstance(data, dict) and "resourceSpans" in data:
        return _dedupe(_otel_from_otlp(data))
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        return _dedupe(_otel_from_jaeger(data))
    # One Jaeger trace object, the element `data` normally wraps.
    if isinstance(data, dict) and "spans" in data and "processes" in data:
        return _dedupe(_otel_from_jaeger({"data": [data]}))
    zipkin = _zipkin_spans(data)
    if zipkin is not None:
        return _dedupe(_otel_from_zipkin(zipkin))

    raise ValueError(
        "Trace file format not recognized. Expected OTLP "
        "(`resourceSpans`), Jaeger (`data: [{spans, processes}]`), or "
        "Zipkin (one span or an array of spans with `traceId`/`localEndpoint`)."
    )


def _zipkin_spans(data: Any) -> list | None:
    """Zipkin v2 is either a list of spans or one span object."""
    if isinstance(data, list) and data and isinstance(data[0], dict) and "traceId" in data[0]:
        return data
    if isinstance(data, dict) and "traceId" in data and (
        "id" in data or "localEndpoint" in data
    ):
        return [data]
    return None


def _otel_from_otlp(data: dict) -> list[tuple[str, str]]:
    """Parse OTLP JSON: resourceSpans[*].scopeSpans[*].spans[*]."""
    span_to_service: dict[str, str] = {}
    parent_links: list[tuple[str, str]] = []  # (span_id, parent_span_id)

    for rs in data.get("resourceSpans", []):
        service = _otlp_service_name(rs.get("resource", {}))
        if not service:
            continue
        for ss in rs.get("scopeSpans", []) or rs.get("instrumentationLibrarySpans", []):
            for span in ss.get("spans", []):
                span_id = span.get("spanId") or span.get("span_id")
                parent_id = span.get("parentSpanId") or span.get("parent_span_id")
                if not span_id:
                    continue
                span_to_service[span_id] = service
                if parent_id and parent_id != "" and not _is_zero_id(parent_id):
                    parent_links.append((span_id, parent_id))

    edges: list[tuple[str, str]] = []
    for child_id, parent_id in parent_links:
        parent_svc = span_to_service.get(parent_id)
        child_svc = span_to_service.get(child_id)
        if parent_svc and child_svc and parent_svc != child_svc:
            edges.append((parent_svc, child_svc))
    return edges


def _otlp_service_name(resource: dict) -> str | None:
    """Extract `service.name` from an OTLP resource block."""
    for attr in resource.get("attributes", []):
        if attr.get("key") == "service.name":
            value = attr.get("value", {})
            for k in ("stringValue", "string_value"):
                if k in value:
                    return str(value[k])
    return None


def _is_zero_id(span_id: str) -> bool:
    """OTLP encodes 'no parent' as all-zeros hex/base64."""
    if not span_id:
        return True
    s = str(span_id).strip().rstrip("=")
    return all(c in "0A" for c in s) or s == "0"


def _otel_from_jaeger(data: dict) -> list[tuple[str, str]]:
    """Parse Jaeger JSON: data[*].spans[*] with processes lookup."""
    edges: list[tuple[str, str]] = []
    for trace in data.get("data", []):
        processes = trace.get("processes", {})
        span_to_service: dict[str, str] = {}
        for span in trace.get("spans", []):
            sid = span.get("spanID") or span.get("spanId")
            pid = span.get("processID") or span.get("processId")
            proc = processes.get(pid, {})
            svc = proc.get("serviceName")
            if sid and svc:
                span_to_service[sid] = svc
        for span in trace.get("spans", []):
            sid = span.get("spanID") or span.get("spanId")
            child_svc = span_to_service.get(sid)
            if not child_svc:
                continue
            # Jaeger represents parents via "references": [{refType: CHILD_OF, spanID}]
            for ref in span.get("references", []):
                if ref.get("refType") in ("CHILD_OF", "FOLLOWS_FROM"):
                    parent_id = ref.get("spanID") or ref.get("spanId")
                    parent_svc = span_to_service.get(parent_id)
                    if parent_svc and parent_svc != child_svc:
                        edges.append((parent_svc, child_svc))
    return edges


def _otel_from_zipkin(spans: list) -> list[tuple[str, str]]:
    """Parse Zipkin v2 spans: parent/child links and ``remoteEndpoint`` calls."""
    span_to_service: dict[str, str] = {}
    for span in spans:
        sid = span.get("id")
        endpoint = span.get("localEndpoint") or {}
        svc = endpoint.get("serviceName")
        if sid and svc:
            span_to_service[sid] = svc

    edges: list[tuple[str, str]] = []
    for span in spans:
        local = (span.get("localEndpoint") or {}).get("serviceName")
        remote = (span.get("remoteEndpoint") or {}).get("serviceName")
        if isinstance(local, str) and isinstance(remote, str) and local != remote:
            kind = str(span.get("kind") or "").upper()
            # SERVER/CONSUMER spans are the callee; the remote side is the caller.
            if kind in {"SERVER", "CONSUMER"}:
                edges.append((remote, local))
            else:
                edges.append((local, remote))
        sid = span.get("id")
        pid = span.get("parentId")
        if not sid or not pid:
            continue
        parent_svc = span_to_service.get(pid)
        child_svc = span_to_service.get(sid)
        if parent_svc and child_svc and parent_svc != child_svc:
            edges.append((parent_svc, child_svc))
    return edges


# ---------------------------------------------------------------------------
# AI agent frameworks (LangGraph / CrewAI / AutoGen)
# ---------------------------------------------------------------------------
#
# All three parse Python source via `ast` only — they do NOT import the
# framework being analyzed. That means the SDK works on a repo without
# `pip install langgraph` etc.


def _ast_str_arg(node: ast.expr) -> str | None:
    """Resolve an AST node to a string label.

    Handles string constants, bare Name references (use the name),
    Attribute lookups (e.g. `agent.role` -> "agent.role"), and the
    LangGraph sentinels START / END.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        # `START` / `END` from langgraph come through as Name nodes;
        # any other Name is a Python variable we use as a node label.
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


_LG_SENTINELS = {"__end__": "END", "__start__": "START", "END": "END", "START": "START"}
_LG_BUILDERS = {"StateGraph", "MessageGraph", "Graph"}


def _preorder(node: ast.AST):
    """Yield ``node`` and its children in source order."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _assigned_name_value(node: ast.AST) -> tuple[str, ast.expr] | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
        return node.target.id, node.value
    return None


def _call_name(fn: ast.expr) -> str | None:
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _lg_norm(label: str | None) -> str | None:
    if label is None:
        return None
    return _LG_SENTINELS.get(label, label)


def _lg_targets(expr: ast.expr | None) -> list[str]:
    """Literal goto targets: strings, END/START, lists, and conditional expressions.

    A bare variable (``goto=next_node``) is dynamic, so it contributes nothing.
    ``path_map`` values use :func:`_lg_path_targets`, which does keep names.
    """
    if expr is None:
        return []
    # py3.9 may wrap a subscript index in ast.Index.
    if type(expr).__name__ == "Index":
        return _lg_targets(getattr(expr, "value", None))
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        out: list[str] = []
        for elt in expr.elts:
            out.extend(_lg_targets(elt))
        return out
    if isinstance(expr, ast.IfExp):
        return _lg_targets(expr.body) + _lg_targets(expr.orelse)
    if isinstance(expr, ast.Call) and _call_name(expr.func) == "Send" and expr.args:
        # Command(goto=[Send("node", {...})]) routes to that node.
        return _lg_targets(expr.args[0])
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return _lg_targets(expr.left) + _lg_targets(expr.right)
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        norm = _lg_norm(expr.value)
        return [norm] if norm else []
    if isinstance(expr, ast.Name) and expr.id in ("END", "START"):
        return [expr.id]
    if isinstance(expr, ast.Attribute) and expr.attr in ("END", "START"):
        return [expr.attr]
    return []


def _lg_path_targets(expr: ast.expr | None) -> list[str]:
    """Targets in ``add_conditional_edges`` path maps, including node-name variables."""
    if expr is None:
        return []
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        out: list[str] = []
        for elt in expr.elts:
            out.extend(_lg_path_targets(elt))
        return out
    if isinstance(expr, ast.IfExp):
        return _lg_path_targets(expr.body) + _lg_path_targets(expr.orelse)
    if isinstance(expr, ast.Attribute) and expr.attr in ("END", "START"):
        return [expr.attr]
    label = _lg_norm(_ast_str_arg(expr))
    return [label] if label else []


def _lg_annotation_targets(ann: ast.expr | None) -> list[str]:
    """Targets from ``-> Command[Literal["a", "b"]]`` (including stringized annotations)."""
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            parsed = ast.parse(ann.value, mode="eval").body
        except SyntaxError:
            return []
        if not isinstance(parsed, ast.expr):
            return []
        ann = parsed
    if not isinstance(ann, ast.Subscript):
        return []
    if _call_name(ann.value) != "Command":
        return []
    return _lg_type_targets(ann.slice)


def _lg_router_targets(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Static branch targets of a conditional-edge router.

    Uses the return annotation (``Literal["a", "b"]``, including a stringized
    annotation) and ``return "node"`` / ``return END`` constants. A returned
    variable is dynamic and contributes nothing.
    """
    ann: ast.expr | None = fn.returns
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            parsed = ast.parse(ann.value, mode="eval").body
        except SyntaxError:
            parsed = None
        ann = parsed if isinstance(parsed, ast.expr) else None
    found = list(_lg_type_targets(ann))
    stack: list[ast.AST] = list(reversed(fn.body))
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(current, ast.Return) and current.value is not None:
            found.extend(_lg_targets(current.value))
        stack.extend(reversed(list(ast.iter_child_nodes(current))))
    return found


def _lg_type_targets(expr: ast.expr | None) -> list[str]:
    """Targets inside ``Literal[...]``, a union of those, or a bare literal list."""
    if expr is None:
        return []
    if type(expr).__name__ == "Index":
        return _lg_type_targets(getattr(expr, "value", None))
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return _lg_type_targets(expr.left) + _lg_type_targets(expr.right)
    if isinstance(expr, ast.Tuple):
        out: list[str] = []
        for elt in expr.elts:
            out.extend(_lg_type_targets(elt))
        return out
    if isinstance(expr, ast.Subscript) and _call_name(expr.value) == "Literal":
        return _lg_targets(expr.slice)
    return _lg_targets(expr)


def _node_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Map function name -> the def ``add_node`` is most likely to close over.

    A name defined at class or module scope beats a same-named function nested
    inside another function (that inner def does not rebind the outer name).
    Within each bucket the last definition in source order wins. If the name
    exists only as a nested function, that def is used — node functions are
    sometimes declared inside the builder function.
    """
    top: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    nested: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(node: ast.AST, in_function: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            (nested if in_function else top)[node.name] = node
            in_function = True
        for child in ast.iter_child_nodes(node):
            visit(child, in_function)

    visit(tree, False)
    chosen: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = dict(nested)
    chosen.update(top)
    return chosen


def _calls_skipping_nested(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    """Calls in ``fn``'s body, not calls inside a nested function, class, or lambda."""
    stack: list[ast.AST] = list(reversed(fn.body))
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(current, ast.Call):
            yield current
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def from_langgraph(path: str) -> list[tuple[str, str]]:
    """Extract edges from a LangGraph workflow definition.

    Recognized patterns:

    - ``g.add_edge("planner", "researcher")`` / ``g.add_edge(START, "planner")``
    - ``g.add_conditional_edges("planner", router, {"yes": "act", "no": "end"})``
    - ``g.set_entry_point("planner")`` / ``g.set_finish_point("act")``
    - ``Command(goto="next")`` / ``Command(goto=[...])`` returned from a node
      function, and ``-> Command[Literal["a", "b"]]`` return annotations
      (``__end__`` / ``__start__`` normalize to ``END`` / ``START``)
    - several ``StateGraph`` builders in one file. Each keeps its own
      START/END. A builder compiled and mounted as a node of another graph
      (``parent.add_node("x", sub.compile())``) is wired in as
      ``x -> <sub entry>`` and ``<sub exit> -> x``. Any other extra builder
      uses ``START[<builder>]`` / ``END[<builder>]``. The last builder in
      the file that is not mounted keeps the bare ``START`` / ``END`` labels.

    Args:
        path: Path to a Python file defining the graph.

    Returns:
        Deduped list of ``(source, target)`` edges.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    # 1. Builders (`x = StateGraph(...)`) and compiled aliases (`y = x.compile()`),
    # in source order so "last graph" is the last one in the file.
    builders: list[str] = []
    builder_set: set[str] = set()
    compiled: dict[str, str] = {}
    for node in _preorder(tree):
        assigned = _assigned_name_value(node)
        if assigned is None:
            continue
        var, value = assigned
        if not isinstance(value, ast.Call):
            continue
        fname = _call_name(value.func)
        if fname in _LG_BUILDERS:
            builders.append(var)
            builder_set.add(var)
        elif (
            fname == "compile"
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id in builder_set
        ):
            compiled[var] = value.func.value.id

    def _owner(call: ast.Call) -> str | None:
        fn = call.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            return fn.value.id
        return None

    # 2. Raw per-builder edges; node -> function and node -> mounted subgraph.
    raw: list[tuple[str | None, str, str]] = []
    func_to_nodes: dict[str, list[tuple[str | None, str]]] = {}
    mounted: dict[str, tuple[str | None, str]] = {}

    def _mount_target(action: ast.expr | None, owner: str | None, node_name: str) -> None:
        if isinstance(action, ast.Name):
            if action.id in compiled:
                mounted[compiled[action.id]] = (owner, node_name)
            elif action.id in builder_set:
                mounted[action.id] = (owner, node_name)
            else:
                func_to_nodes.setdefault(action.id, []).append((owner, node_name))
        elif (
            isinstance(action, ast.Call)
            and isinstance(action.func, ast.Attribute)
            and action.func.attr == "compile"
            and isinstance(action.func.value, ast.Name)
            and action.func.value.id in builder_set
        ):
            mounted[action.func.value.id] = (owner, node_name)

    functions = _node_functions(tree)
    for node in _preorder(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        owner = _owner(node)

        if method == "add_edge" and len(node.args) >= 2:
            s = _lg_norm(_ast_str_arg(node.args[0]))
            t = _lg_norm(_ast_str_arg(node.args[1]))
            if s and t:
                raw.append((owner, s, t))

        elif method == "add_conditional_edges":
            if not node.args:
                continue
            src = _lg_norm(_ast_str_arg(node.args[0]))
            if not src:
                continue
            mapping_node: ast.expr | None = None
            if len(node.args) >= 3:
                mapping_node = node.args[2]
            else:
                kw = _kwargs(node)
                mapping_node = kw.get("path_map") or kw.get("conditional_edge_mapping")
            if isinstance(mapping_node, ast.Dict):
                for value in mapping_node.values:
                    for target in _lg_path_targets(value):
                        raw.append((owner, src, target))
            elif isinstance(mapping_node, (ast.List, ast.Tuple)):
                for target in _lg_path_targets(mapping_node):
                    raw.append((owner, src, target))
            elif mapping_node is None and len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
                # No path_map: the router's Literal annotation and returned
                # string constants are the static targets.
                router = functions.get(node.args[1].id)
                if router is not None:
                    for target in _lg_router_targets(router):
                        raw.append((owner, src, target))

        elif method == "set_entry_point" and node.args:
            t = _lg_norm(_ast_str_arg(node.args[0]))
            if t:
                raw.append((owner, "START", t))

        elif method == "set_finish_point" and node.args:
            s = _lg_norm(_ast_str_arg(node.args[0]))
            if s:
                raw.append((owner, s, "END"))

        elif method == "add_node":
            kw = _kwargs(node)
            if node.args:
                first = node.args[0]
                action = node.args[1] if len(node.args) > 1 else kw.get("action")
            else:
                first = kw.get("node")
                action = kw.get("action")
            if first is None:
                continue
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                _mount_target(action, owner, first.value)
            elif isinstance(first, ast.Name) and action is None:
                # add_node(fn) uses the function name as the node id.
                _mount_target(first, owner, first.id)
            elif isinstance(first, ast.Name):
                _mount_target(action if action is not None else first, owner, first.id)

    # 3. Command(goto=...) and Command[Literal[...]] on the node functions themselves.
    for fdef in functions.values():
        if fdef.name not in func_to_nodes:
            continue
        targets = list(_lg_annotation_targets(fdef.returns))
        for call in _calls_skipping_nested(fdef):
            if _call_name(call.func) == "Command":
                targets.extend(_lg_targets(_kwargs(call).get("goto")))
        for node_owner, node_name in func_to_nodes[fdef.name]:
            for target in targets:
                raw.append((node_owner, node_name, target))

    # 4. Resolve START/END per builder. A mounted subgraph's START/END become
    # the parent node. With several unmounted builders, only the last-defined
    # one (conventionally the top-level graph) keeps bare labels.
    unmounted = [b for b in builders if b not in mounted]
    top = unmounted[-1] if unmounted else None

    def _resolve(owner: str | None, label: str) -> str:
        if label not in ("START", "END"):
            return label
        if owner in mounted:
            return mounted[owner][1]
        if owner in builder_set and owner != top:
            return f"{label}[{owner}]"
        return label

    edges: list[tuple[str, str]] = []
    for owner, src, dst in raw:
        resolved_src, resolved_dst = _resolve(owner, src), _resolve(owner, dst)
        if resolved_src != resolved_dst:
            edges.append((resolved_src, resolved_dst))
    return _dedupe(edges)


def from_crewai(path: str) -> list[tuple[str, str]]:
    """Extract edges from a CrewAI script.

    Patterns:

    - ``my_task = Task(description=..., agent=researcher)``
        emits ``researcher -> my_task``
    - ``my_task = Task(..., context=[prep_task, lookup_task])``
        emits ``prep_task -> my_task`` and ``lookup_task -> my_task``
    - ``Crew(agents=[a, b], tasks=[t1, t2], manager_agent=mgr)``
        emits ``mgr -> a``, ``mgr -> b`` (manager broadcasts)
    - ``@CrewBase`` classes with ``@agent`` / ``@task`` / ``@crew`` methods
        (the ``crewai create crew`` layout, including ``config/tasks.yaml``).
        Each task's agent emits ``agent -> task``. ``Process.sequential``
        (the default) chains tasks in method order. ``context:`` in YAML or
        ``context=[...]`` on the ``Task`` emits those dependency edges.
        ``Process.hierarchical`` with ``manager_agent`` emits ``manager -> agent``
        and does not add the sequential chain.

    Args:
        path: Path to a Python file using crewai.

    Returns:
        Deduped list of edges. Node names are the Python variable or method
        names from the source file.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    edges: list[tuple[str, str]] = []

    def _is_named_call(call: ast.Call, name: str) -> bool:
        f = call.func
        if isinstance(f, ast.Name) and f.id == name:
            return True
        if isinstance(f, ast.Attribute) and f.attr == name:
            return True
        return False

    for node in ast.walk(tree):
        # Find `task_var = Task(...)` or `task_var = SomeTask(...)`
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                call = node.value
                task_name = target.id
                if _is_named_call(call, "Task"):
                    kwargs = _kwargs(call)
                    if "agent" in kwargs:
                        agent = _ast_str_arg(kwargs["agent"])
                        if agent:
                            edges.append((agent, task_name))
                    if "context" in kwargs and isinstance(kwargs["context"], (ast.List, ast.Tuple)):
                        for ctx in kwargs["context"].elts:
                            ctx_name = _ast_str_arg(ctx)
                            if ctx_name:
                                edges.append((ctx_name, task_name))

        # Find `Crew(agents=[a, b], manager_agent=mgr)` (manager fans out to agents)
        if isinstance(node, ast.Call) and _is_named_call(node, "Crew"):
            kwargs = _kwargs(node)
            agents_list: list[str] = []
            if "agents" in kwargs and isinstance(kwargs["agents"], (ast.List, ast.Tuple)):
                for a in kwargs["agents"].elts:
                    name = _ast_str_arg(a)
                    if name:
                        agents_list.append(name)
            manager = None
            if "manager_agent" in kwargs:
                manager = _ast_str_arg(kwargs["manager_agent"])
            if manager:
                for a in agents_list:
                    if a != manager:
                        edges.append((manager, a))

    edges.extend(_crewai_decorator_edges(tree, path))
    return _dedupe(edges)


def _decorator_names(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for deco in getattr(fn, "decorator_list", []):
        node = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _self_call_name(expr: ast.expr | None) -> str | None:
    """``self.foo()`` / ``self.foo`` / ``foo()`` / ``foo`` -> ``foo``."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "self":
        return expr.attr
    if isinstance(expr, ast.Name):
        return expr.id
    return None


def _subscript_str(node: ast.Subscript) -> str | None:
    """String literal inside ``obj["key"]``. Variable keys are not yaml names."""
    sl: ast.expr = node.slice
    if type(sl).__name__ == "Index":
        inner = getattr(sl, "value", None)
        if not isinstance(inner, ast.expr):
            return None
        sl = inner
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


def _class_string_attr(cls: ast.ClassDef, name: str) -> str | None:
    for stmt in cls.body:
        value: ast.expr | None = None
        target: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            value = stmt.value
            for item in stmt.targets:
                if isinstance(item, ast.Name) and item.id == name:
                    target = item
                    break
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == name:
            target = stmt.target
            value = stmt.value
        if target is not None and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _crewai_task_yaml(path: str, cls: ast.ClassDef) -> dict[str, Any]:
    """Load the class's tasks YAML. Missing or unreadable files yield ``{}``."""
    rel = _class_string_attr(cls, "tasks_config") or "config/tasks.yaml"
    yaml_path = rel if os.path.isabs(rel) else os.path.join(os.path.dirname(path), rel)
    if not os.path.isfile(yaml_path):
        return {}
    try:
        data = _load_single_yaml(yaml_path) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _crewai_decorator_edges(tree: ast.AST, path: str) -> list[tuple[str, str]]:
    """Edges for the ``@CrewBase`` project layout (``crewai create crew``).

    Task order follows the class body: a redefined method keeps its first
    slot and its last body, which is how the class dict behaves. That is the
    order ``CrewBase`` runs ``Process.sequential`` in.
    """
    edges: list[tuple[str, str]] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        task_methods = [m for m in methods if "task" in _decorator_names(m)]
        agent_methods = [m.name for m in methods if "agent" in _decorator_names(m)]
        if not task_methods and not agent_methods:
            continue

        order: list[str] = []
        last_def: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for method in task_methods:
            if method.name not in last_def:
                order.append(method.name)
            last_def[method.name] = method

        explicit_agent: set[str] = set()
        yaml_key_for: dict[str, str] = {}
        for name in order:
            method = last_def[name]
            for call in _calls_skipping_nested(method):
                if _call_name(call.func) != "Task":
                    continue
                kw = _kwargs(call)
                agent = _self_call_name(kw.get("agent"))
                if agent:
                    edges.append((agent, name))
                    explicit_agent.add(name)
                ctx = kw.get("context")
                if isinstance(ctx, (ast.List, ast.Tuple)):
                    for item in ctx.elts:
                        ctx_name = _self_call_name(item)
                        if ctx_name:
                            edges.append((ctx_name, name))
                elif isinstance(ctx, ast.Constant) and isinstance(ctx.value, str):
                    edges.append((ctx.value, name))
                cfg = kw.get("config")
                if isinstance(cfg, ast.Subscript):
                    key = _subscript_str(cfg)
                    if key:
                        yaml_key_for[name] = key

        tasks_yaml = _crewai_task_yaml(path, cls)
        key_to_method = {key: method for method, key in yaml_key_for.items()}
        for key, spec in tasks_yaml.items():
            if not isinstance(spec, dict):
                continue
            task_name = key_to_method.get(key, key if key in last_def else None)
            if task_name is None or task_name not in last_def:
                continue
            if task_name not in explicit_agent:
                agent = spec.get("agent")
                if isinstance(agent, str) and agent:
                    edges.append((agent, task_name))
            context = spec.get("context") or []
            if isinstance(context, str):
                context = [context]
            if isinstance(context, list):
                for item in context:
                    if isinstance(item, str) and item:
                        edges.append((key_to_method.get(item, item), task_name))

        process = "sequential"
        manager: str | None = None
        for method in methods:
            if "crew" not in _decorator_names(method):
                continue
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in _calls_skipping_nested(method):
                if _call_name(call.func) != "Crew":
                    continue
                kw = _kwargs(call)
                proc = kw.get("process")
                if isinstance(proc, ast.Attribute):
                    process = proc.attr
                elif isinstance(proc, ast.Constant) and isinstance(proc.value, str):
                    process = proc.value
                # Last Crew() in the @crew method wins; manager fan-out is not
                # repeated when the method builds the crew more than once.
                found = _self_call_name(kw.get("manager_agent"))
                manager = found
        if manager:
            for agent in agent_methods:
                if agent != manager:
                    edges.append((manager, agent))
        if process.lower() == "sequential":
            for left, right in zip(order, order[1:]):
                edges.append((left, right))
    return edges


def from_autogen(path: str) -> list[tuple[str, str]]:
    """Extract edges from a Microsoft AutoGen / AG2 / autogen-agentchat script.

    Patterns recognized:

    **Legacy AutoGen (autogen-core 0.2.x style):**

    - ``GroupChat(agents=[a, b, c])`` (no explicit manager) → fully connected
    - ``GroupChat(agents=[...])`` + ``GroupChatManager(groupchat=gc)`` → star (manager -> agents)
    - ``user_proxy.initiate_chat(assistant, ...)`` → ``user_proxy -> assistant``

    **Modern AutoGen (autogen-agentchat 0.4+):**

    - ``RoundRobinGroupChat([a, b, c])`` → round-robin chain (a -> b -> c -> a)
    - ``SelectorGroupChat([a, b, c], ...)`` → fully connected (selector picks any)
    - ``Swarm([a, b, c], ...)`` → fully connected
    - ``team.run(task=...)`` / ``team.run_stream(...)`` recognized but emit no edges

    Args:
        path: Path to a Python file using autogen / autogen-agentchat / ag2.

    Returns:
        Deduped list of edges. Node names are Python variable names.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    edges: list[tuple[str, str]] = []

    def _name_or_attr(call: ast.Call, names: set[str]) -> bool:
        f = call.func
        if isinstance(f, ast.Name):
            return f.id in names
        if isinstance(f, ast.Attribute):
            return f.attr in names
        return False

    def _agents_list(call: ast.Call) -> list[str]:
        """Pull the agents list — either positional first arg or `agents=` kwarg."""
        node = None
        if call.args and isinstance(call.args[0], (ast.List, ast.Tuple)):
            node = call.args[0]
        else:
            node = _kwargs(call).get("agents") or _kwargs(call).get("participants")
        if not isinstance(node, (ast.List, ast.Tuple)):
            return []
        return [n for n in (_ast_str_arg(a) for a in node.elts) if n]

    # First pass: collect every group-chat-style construct's agents list.
    # Each entry: (var_name, agents, kind) where kind in
    # {"legacy", "round_robin", "selector", "swarm"}.
    groupchats: dict[str, tuple[list[str], str]] = {}
    LEGACY = {"GroupChat"}
    ROUND_ROBIN = {"RoundRobinGroupChat"}
    SELECTOR = {"SelectorGroupChat", "MagenticOneGroupChat"}
    SWARM = {"Swarm"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if _name_or_attr(call, LEGACY):
            agents = _agents_list(call)
            if agents:
                groupchats[target.id] = (agents, "legacy")
        elif _name_or_attr(call, ROUND_ROBIN):
            agents = _agents_list(call)
            if agents:
                groupchats[target.id] = (agents, "round_robin")
        elif _name_or_attr(call, SELECTOR):
            agents = _agents_list(call)
            if agents:
                groupchats[target.id] = (agents, "selector")
        elif _name_or_attr(call, SWARM):
            agents = _agents_list(call)
            if agents:
                groupchats[target.id] = (agents, "swarm")

    # Pass 2: legacy GroupChatManager → star pattern
    chat_to_manager: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not _name_or_attr(call, {"GroupChatManager"}):
            continue
        kw = _kwargs(call)
        gc_node = kw.get("groupchat") or kw.get("group_chat")
        gc_name = _ast_str_arg(gc_node) if gc_node else None
        if gc_name and gc_name in groupchats:
            chat_to_manager[gc_name] = target.id

    # Emit edges based on chat kind
    for chat, (agents, kind) in groupchats.items():
        if kind == "legacy":
            manager = chat_to_manager.get(chat)
            if manager:
                for a in agents:
                    if a != manager:
                        edges.append((manager, a))
            else:
                for a in agents:
                    for b in agents:
                        if a != b:
                            edges.append((a, b))
        elif kind == "round_robin":
            # Round-robin: each agent addresses the next; last wraps to first.
            for i in range(len(agents)):
                edges.append((agents[i], agents[(i + 1) % len(agents)]))
        elif kind in ("selector", "swarm"):
            # Fully connected — selector or swarm can route between any pair.
            for a in agents:
                for b in agents:
                    if a != b:
                        edges.append((a, b))

    # Second pass: `x.initiate_chat(y, ...)` -> x -> y
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "initiate_chat":
            continue
        caller = _ast_str_arg(node.func.value)
        if not caller or not node.args:
            continue
        recipient = _ast_str_arg(node.args[0])
        if recipient:
            edges.append((caller, recipient))

    return _dedupe(edges)


# ---------------------------------------------------------------------------
# Auto-detect
# ---------------------------------------------------------------------------

_DIRECTORY_SOURCES = frozenset({
    "docker-compose",
    "kubernetes",
    "github-actions",
    "terraform",
    "python-imports",
    "package-json",
    "pyproject",
    "requirements",
    "otel-traces",
    "langgraph",
    "crewai",
    "autogen",
    "aws-cdk",
    "pulumi",
    "cloudformation",
})


def _source_selection(value: Any, param: str) -> set[str] | None:
    """Normalize ``include`` / ``exclude`` to a set of known source labels."""
    if value is None:
        return None
    if isinstance(value, str):
        labels = {value}
    elif isinstance(value, (set, list, tuple, frozenset)):
        labels = set()
        for item in value:
            if not isinstance(item, str):
                raise TypeError(f"{param} entries must be source-name strings, got {type(item).__name__}")
            labels.add(item)
    else:
        raise TypeError(f"{param} must be a source name or a collection of source names")
    unknown = labels - _DIRECTORY_SOURCES
    if unknown:
        known = ", ".join(sorted(_DIRECTORY_SOURCES))
        bad = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown {param} source(s): {bad}. Known sources: {known}.")
    return labels


def from_directory(
    path: str = ".",
    *,
    include: set[str] | list[str] | tuple[str, ...] | str | None = None,
    exclude: set[str] | list[str] | tuple[str, ...] | str | None = None,
) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Scan a directory and extract edges from all recognized infrastructure files.

    Auto-detects docker-compose, Kubernetes manifests, GitHub Actions
    workflows, Terraform, Python imports, and the other parsers in this module.

    Args:
        path: Directory to scan (default: current directory).
        include: If given, only these source labels are scanned.
        exclude: Source labels to skip. Applied after ``include``.

    ``include`` / ``exclude`` take source labels: ``docker-compose``,
    ``kubernetes``, ``github-actions``, ``terraform``, ``python-imports``,
    ``package-json``, ``pyproject``, ``requirements``, ``otel-traces``,
    ``langgraph``, ``crewai``, ``autogen``, ``aws-cdk``, ``pulumi``,
    ``cloudformation``. A single label may be passed as a string. The default
    (both omitted) scans every source.

    Returns:
        Tuple of (edges, sources) where sources maps format name to edge count.

    Raises:
        ValueError: if ``include`` or ``exclude`` names a source this scanner
            does not know.
        TypeError: if a filter is not a string or a collection of strings.

    Example::

        edges, sources = depgraph_extract.extract.from_directory(".")
        edges, sources = depgraph_extract.extract.from_directory(
            ".", exclude={"github-actions", "python-imports"},
        )
    """
    all_edges: list[tuple[str, str]] = []
    sources: dict[str, int] = {}
    chosen = _source_selection(include, "include")
    skipped = _source_selection(exclude, "exclude") or set()

    def _want(label: str) -> bool:
        return (chosen is None or label in chosen) and label not in skipped

    # Docker Compose — any compose file under the tree, not only the root.
    # Stacks in different directories share one edge list; identical service
    # names from unrelated files collapse, the same way Terraform does.
    if _want("docker-compose"):
        compose_names = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
        skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__"}
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for name in filenames:
                if name not in compose_names:
                    continue
                compose_path = os.path.join(dirpath, name)
                try:
                    edges = from_docker_compose(compose_path)
                    if edges:
                        all_edges.extend(edges)
                        sources["docker-compose"] = sources.get("docker-compose", 0) + len(edges)
                except Exception:
                    pass

    # Kubernetes — find any directory containing YAML that declares a k8s
    # resource (`apiVersion` + `kind`), rather than trusting conventional dir
    # names. This catches manifests in non-standard locations (e.g. release/,
    # kubernetes-manifests/). Running once per directory keeps cross-file
    # Service->Deployment selector matching intact.
    if _want("kubernetes"):
        k8s_dirs_found: set[str] = set()
        for ext in ("**/*.yaml", "**/*.yml"):
            for fp in glob.glob(os.path.join(path, ext), recursive=True):
                if any(p in fp for p in ("node_modules", ".venv", "venv", ".git")):
                    continue
                try:
                    head = open(fp, encoding="utf-8", errors="replace").read(2048)
                except OSError:
                    continue
                if "apiVersion:" in head and "kind:" in head:
                    k8s_dirs_found.add(os.path.dirname(fp) or path)
        for d in sorted(k8s_dirs_found):
            try:
                edges = from_kubernetes(d)
                if edges:
                    all_edges.extend(edges)
                    sources["kubernetes"] = sources.get("kubernetes", 0) + len(edges)
            except Exception:
                pass

    # GitHub Actions
    gh_path = os.path.join(path, ".github", "workflows")
    if os.path.isdir(gh_path) and _want("github-actions"):
        try:
            edges = from_github_actions(gh_path)
            if edges:
                all_edges.extend(edges)
                sources["github-actions"] = len(edges)
        except Exception:
            pass

    # Terraform
    tf_files = glob.glob(os.path.join(path, "**/*.tf"), recursive=True) if _want("terraform") else []
    if tf_files:
        try:
            edges = from_terraform(path)
            if edges:
                all_edges.extend(edges)
                sources["terraform"] = len(edges)
        except Exception:
            pass

    # Python imports
    if _want("python-imports"):
        py_files = glob.glob(os.path.join(path, "**/*.py"), recursive=True)
        # Filter out venv/node_modules/hidden dirs
        py_files = [f for f in py_files if not any(
            p in f for p in ["node_modules", ".venv", "venv", "__pycache__", ".git"]
        )]
    else:
        py_files = []
    if py_files:
        try:
            edges = from_python_imports(path)
            if edges:
                all_edges.extend(edges)
                sources["python-imports"] = len(edges)
        except Exception:
            pass

    # pyproject.toml and requirements*.txt
    if _want("pyproject"):
        for fp in glob.glob(os.path.join(path, "**/pyproject.toml"), recursive=True):
            if any(p in fp for p in ("node_modules", ".venv", "venv", ".git")):
                continue
            try:
                edges = from_pyproject(fp)
                if edges:
                    all_edges.extend(edges)
                    sources["pyproject"] = sources.get("pyproject", 0) + len(edges)
            except Exception:
                pass
    if _want("requirements"):
        for fp in glob.glob(os.path.join(path, "**/requirements*.txt"), recursive=True):
            if any(p in fp for p in ("node_modules", ".venv", "venv", ".git")):
                continue
            try:
                edges = from_requirements(fp)
                if edges:
                    all_edges.extend(edges)
                    sources["requirements"] = sources.get("requirements", 0) + len(edges)
            except Exception:
                pass

    # package.json (monorepo workspaces)
    root_pkg = os.path.join(path, "package.json")
    if os.path.isfile(root_pkg) and _want("package-json"):
        try:
            edges = from_package_json_workspaces(path)
            if edges:
                all_edges.extend(edges)
                sources["package-json"] = len(edges)
        except Exception:
            pass

    # OpenTelemetry traces — covers the highest-quality edge source (real
    # runtime calls). Auto-picked up from common filenames at the root.
    if _want("otel-traces"):
        for name in ["traces.json", "otel.json", "otlp.json", "jaeger.json", "zipkin.json"]:
            trace_path = os.path.join(path, name)
            if os.path.isfile(trace_path):
                try:
                    edges = from_otel_traces(trace_path)
                    if edges:
                        all_edges.extend(edges)
                        sources["otel-traces"] = sources.get("otel-traces", 0) + len(edges)
                except Exception:
                    pass
        # Also try a `traces/` directory containing one or more trace files
        traces_dir = os.path.join(path, "traces")
        if os.path.isdir(traces_dir):
            for fname in glob.glob(os.path.join(traces_dir, "*.json")):
                try:
                    edges = from_otel_traces(fname)
                    if edges:
                        all_edges.extend(edges)
                        sources["otel-traces"] = sources.get("otel-traces", 0) + len(edges)
                except Exception:
                    pass

    # AI agent frameworks + AWS CDK + Pulumi — for each file that imports a
    # known framework, run the matching parser on JUST that file (cheaper +
    # more accurate than walking every Python file).
    py_specs = (
        ("langgraph",   ("langgraph",),                          from_langgraph),
        ("crewai",      ("crewai",),                             from_crewai),
        ("autogen",     ("autogen", "autogen_agentchat", "autogen_core", "autogen_ext", "ag2"), from_autogen),
        ("aws-cdk",     ("aws_cdk", "aws_cdk_lib"),              from_aws_cdk),
        ("pulumi",      ("pulumi",),                             from_pulumi),
    )
    py_specs = tuple(spec for spec in py_specs if _want(spec[0]))
    py_files = glob.glob(os.path.join(path, "**/*.py"), recursive=True) if py_specs else []
    py_files = [f for f in py_files if not any(
        p in f for p in ["node_modules", ".venv", "venv", "__pycache__", ".git"]
    )]
    for fpath in py_files:
        try:
            head = open(fpath, encoding="utf-8", errors="replace").read(4096)
        except OSError:
            continue
        for label, prefixes, parser in py_specs:
            if not any(f"import {p}" in head or f"from {p}" in head for p in prefixes):
                continue
            try:
                edges = parser(fpath)
                if edges:
                    all_edges.extend(edges)
                    sources[label] = sources.get(label, 0) + len(edges)
            except Exception:
                pass

    # CloudFormation — pick up *.yaml / *.yml at root or under cloudformation/
    # / templates/. Detection is cheap (the file must contain "Resources:" near
    # the top to be considered).
    cfn_dirs = [path] + [
        os.path.join(path, d) for d in ("cloudformation", "templates", "cfn")
        if os.path.isdir(os.path.join(path, d))
    ]
    seen_cfn: set[str] = set()
    for d in (cfn_dirs if _want("cloudformation") else []):
        if not os.path.isdir(d):
            continue
        for ext in ("*.yaml", "*.yml", "*.json", "*.template"):
            for fp in glob.glob(os.path.join(d, ext)):
                if fp in seen_cfn:
                    continue
                seen_cfn.add(fp)
                try:
                    with open(fp, encoding="utf-8", errors="replace") as fh:
                        head = fh.read(2048)
                except OSError:
                    continue
                # Cheap heuristic to skip non-CFN YAML/JSON
                if "AWSTemplateFormatVersion" not in head and '"Resources"' not in head and "\nResources:" not in head:
                    continue
                try:
                    edges = from_cloudformation(fp)
                    if edges:
                        all_edges.extend(edges)
                        sources["cloudformation"] = sources.get("cloudformation", 0) + len(edges)
                except Exception:
                    pass

    return _dedupe(all_edges), sources


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedupe(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove duplicate edges and self-loops, preserve order."""
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for src, dst in edges:
        if src != dst and (src, dst) not in seen:
            seen.add((src, dst))
            result.append((src, dst))
    return result
