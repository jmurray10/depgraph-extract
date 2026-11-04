"""Score depgraph-extract on a pinned public corpus.

Labels live in ``corpus/labels``. Parsers do not special-case a repo, path,
or node name. ``--clone`` needs a network; the default CI job does not run
this script.

Exit status is 0 when every selected case runs, repeats itself exactly, and
every *tuning* label matches with precision 1 and recall 1. Held-out labels
are scored and reported the same way and do not change the exit status, so a
miss stays visible instead of being patched against the held-out repos.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from depgraph_extract import extract  # noqa: E402


PARSERS = {
    "docker-compose": extract.from_docker_compose,
    "kubernetes": extract.from_kubernetes,
    "terraform": extract.from_terraform,
    "cloudformation": extract.from_cloudformation,
    "aws-cdk": extract.from_aws_cdk,
    "pulumi": extract.from_pulumi,
    "github-actions": extract.from_github_actions,
    "python-imports": extract.from_python_imports,
    "package-json": extract.from_package_json,
    "package-json-workspaces": extract.from_package_json_workspaces,
    "pyproject": extract.from_pyproject,
    "requirements": extract.from_requirements,
    "langgraph": extract.from_langgraph,
    "crewai": extract.from_crewai,
    "autogen": extract.from_autogen,
    "otel-traces": extract.from_otel_traces,
}


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.check_call(cmd, cwd=cwd)


def ensure_checkout(repo: dict, dest: Path, sha: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        _run(["git", "clone", "--filter=blob:none", "--sparse", repo["url"], str(dest)])
    _run(["git", "fetch", "--depth", "1", "origin", sha], cwd=dest)
    _run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=dest)
    sparse = repo.get("sparse") or []
    if sparse:
        _run(["git", "sparse-checkout", "set", "--skip-checks", *sparse], cwd=dest)


def _pairs(edges) -> list[list[str]]:
    return [[src, dst] for src, dst in edges]


def _apply_dirname(edges: list, dirname: str) -> set[tuple[str, str]]:
    out = set()
    for src, dst in edges:
        out.add((src.replace("{{dirname}}", dirname), dst.replace("{{dirname}}", dirname)))
    return out


def _precision_recall(pred: set[tuple[str, str]], gold: set[tuple[str, str]]) -> tuple[float, float]:
    if not pred and not gold:
        return 1.0, 1.0
    overlap = len(pred & gold)
    precision = overlap / len(pred) if pred else (1.0 if not gold else 0.0)
    recall = overlap / len(gold) if gold else 1.0
    return precision, recall


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text())


def score_case(case: dict, checkout: Path, labels_dir: Path) -> dict:
    parser = PARSERS[case["parser"]]
    target = checkout / case["path"]
    row_base = {
        "id": case["id"],
        "source": case["source"],
        "parser": case["parser"],
        "path": case["path"],
        "edges": 0,
        "deterministic": False,
        "labeled": False,
    }
    if not target.exists():
        row_base["error"] = f"missing path {target}"
        return row_base
    try:
        first = parser(str(target))
        second = parser(str(target))
    except Exception as exc:
        row_base["error"] = f"{type(exc).__name__}: {exc}"
        return row_base
    pred = {(src, dst) for src, dst in first}
    row = {
        "id": case["id"],
        "source": case["source"],
        "parser": case["parser"],
        "path": case["path"],
        "edges": len(first),
        "deterministic": _pairs(first) == _pairs(second),
        "labeled": False,
    }
    label_name = case.get("label")
    if label_name:
        label = json.loads((labels_dir / label_name).read_text())
        gold = _apply_dirname(label["edges"], target.parent.name)
        precision, recall = _precision_recall(pred, gold)
        row.update({
            "labeled": True,
            "precision": precision,
            "recall": recall,
            "missing": sorted(list(gold - pred)),
            "extra": sorted(list(pred - gold)),
        })
    return row


def score_split(
    manifest: dict,
    checkout_root: Path,
    labels_dir: Path,
    split: str,
    clone: bool,
    sha_field: str = "sha",
) -> list[dict]:
    rows = []
    repos = {repo["id"]: repo for repo in manifest["repos"] if split in {"all", repo["split"]}}
    for repo in repos.values():
        dest = checkout_root / repo["split"] / repo["id"]
        sha = repo[sha_field]
        if clone or not (dest / ".git").exists():
            ensure_checkout(repo, dest, sha)
        else:
            # Trust an existing checkout only when it is the requested commit.
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=dest, text=True).strip()
            if head != sha:
                ensure_checkout(repo, dest, sha)
        for case in manifest["cases"]:
            if case["repo"] != repo["id"]:
                continue
            row = score_case(case, dest, labels_dir)
            row["split"] = repo["split"]
            row["sha"] = sha
            rows.append(row)
    return rows


def drift_rows(manifest: dict, checkout_root: Path, labels_dir: Path, clone: bool) -> list[dict]:
    """Edge-count delta between the pinned SHA and the recorded HEAD SHA."""
    out = []
    seen = set()
    for case in manifest["cases"]:
        repo = next(r for r in manifest["repos"] if r["id"] == case["repo"])
        if repo["sha"] == repo["head"]:
            key = (repo["id"], case["id"])
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "id": case["id"],
                "repo": repo["id"],
                "pin_sha": repo["sha"],
                "head_sha": repo["head"],
                "pin_edges": None,
                "head_edges": None,
                "delta": 0,
                "note": "pin is HEAD",
            })
            continue
        pin_dest = checkout_root / repo["split"] / repo["id"]
        head_dest = checkout_root / "head" / repo["id"]
        if clone or not (pin_dest / ".git").exists():
            ensure_checkout(repo, pin_dest, repo["sha"])
        if clone or not (head_dest / ".git").exists():
            ensure_checkout(repo, head_dest, repo["head"])
        pin = score_case(case, pin_dest, labels_dir)
        head = score_case(case, head_dest, labels_dir)
        out.append({
            "id": case["id"],
            "repo": repo["id"],
            "split": repo["split"],
            "pin_sha": repo["sha"],
            "head_sha": repo["head"],
            "pin_edges": pin["edges"],
            "head_edges": head["edges"],
            "delta": head["edges"] - pin["edges"],
            "pin_precision": pin.get("precision"),
            "pin_recall": pin.get("recall"),
            "head_precision": head.get("precision"),
            "head_recall": head.get("recall"),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "corpus" / "manifest.json")
    parser.add_argument("--labels", type=Path, default=ROOT / "corpus" / "labels")
    parser.add_argument("--checkout-root", type=Path, default=Path("/tmp/corpus"))
    parser.add_argument("--split", choices=["tuning", "held-out", "all"], default="all")
    parser.add_argument("--clone", action="store_true")
    parser.add_argument("--drift", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    manifest = _load_manifest(args.manifest)
    rows = score_split(manifest, args.checkout_root, args.labels, args.split, args.clone)
    report = {"cases": rows, "drift": []}
    if args.drift:
        report["drift"] = drift_rows(manifest, args.checkout_root, args.labels, args.clone)

    failures = []
    for row in rows:
        status = "ok"
        if row.get("error"):
            status = "error"
            if row["split"] == "tuning":
                failures.append(row["id"])
        elif not row["deterministic"]:
            status = "nondeterministic"
            if row["split"] == "tuning":
                failures.append(row["id"])
        elif row["labeled"] and row["split"] == "tuning" and (row["precision"] != 1 or row["recall"] != 1):
            status = "mismatch"
            failures.append(row["id"])
        elif row.get("error"):
            status = "error"
        prec = f"{row['precision']:.2f}" if row["labeled"] else "-"
        rec = f"{row['recall']:.2f}" if row["labeled"] else "-"
        print(
            f"{status:14} {row['split']:8} {row['source']:22} {row['id']:32} "
            f"edges={row['edges']:4} P={prec} R={rec}"
        )
        if row.get("error"):
            print(f"  error {row['error']}")
        if row.get("missing"):
            print(f"  missing {row['missing']}")
        if row.get("extra"):
            print(f"  extra {row['extra']}")
    if args.drift:
        print("\nDrift (HEAD edges - pin edges):")
        for row in report["drift"]:
            print(
                f"  {row['id']:32} {row.get('pin_edges')} -> {row.get('head_edges')} "
                f"delta={row['delta']} {row.get('note', '')}"
            )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
