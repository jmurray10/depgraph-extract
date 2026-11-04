"""The corpus scorer runs offline against a tiny manifest. No git, no network."""
from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path


def _load_scorer():
    path = Path(__file__).resolve().parents[1] / "corpus" / "score.py"
    spec = importlib.util.spec_from_file_location("corpus_score", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_score_matches_label_and_is_deterministic(tmp_path):
    score = _load_scorer()
    repo = tmp_path / "mini"
    repo.mkdir()
    (repo / "compose.yaml").write_text(textwrap.dedent("""\
        services:
          web:
            depends_on: [db]
          db:
            image: postgres
    """))
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "mini.json").write_text(json.dumps({"edges": [["web", "db"]]}))
    manifest = {
        "repos": [{
            "id": "mini",
            "split": "tuning",
            "url": "https://example.invalid/mini.git",
            "sha": "0" * 40,
            "head": "0" * 40,
        }],
        "cases": [{
            "id": "mini-compose",
            "repo": "mini",
            "source": "docker-compose",
            "parser": "docker-compose",
            "path": "compose.yaml",
            "label": "mini.json",
        }],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    # Point the checkout at the fixture directly by planting a fake git dir
    # is unnecessary: score_split only fetches when .git is missing and clone
    # is true. With clone false and no .git it would try to clone. Call
    # score_case instead.
    row = score.score_case(manifest["cases"][0], repo, labels)
    assert row["deterministic"] is True
    assert row["precision"] == 1
    assert row["recall"] == 1
    assert row["edges"] == 1
