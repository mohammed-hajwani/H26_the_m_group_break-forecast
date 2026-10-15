import textwrap
from pathlib import Path

import pytest

from break_forecast import capsule, manifests, pypi, usage
from break_forecast.provenance import missing_provenance, observed, record


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, "pyproject.toml", """
        [project]
        name = "demo"
        requires-python = ">=3.9"
        dependencies = ["pydantic>=2", "requests"]
    """)
    write(tmp_path, "requirements.txt", """
        pydantic==1.10.13
        requests==2.31.0
        six  # never imported
        # PyYAML==5.1
    """)
    write(tmp_path, "requirements-dev.txt", "pytest\n")
    write(tmp_path, "Makefile", "setup:\n\tpip install -r requirements.txt -r requirements-dev.txt\ntest:\n\tpytest\n")
    write(tmp_path, "demo/__init__.py", "")
    write(tmp_path, "demo/core.py", """
        import requests
        from requests.utils import _parse_content_type_header
        from pydantic import validator
        import certifi
        from . import helpers

        _orig = requests.Session.request
        requests.Session.request = lambda *a, **k: _orig(*a, **k)

        def get(url):
            return requests.get(url, verify=certifi.where())
    """)
    write(tmp_path, "demo/helpers.py", "import json\n")
    write(tmp_path, "tests/test_core.py", "import pytest\nimport demo.core\n")
    return tmp_path


def test_provenance_rejects_unknown_tag():
    with pytest.raises(ValueError):
        record("guessed", x=1)  # type: ignore[arg-type]


def test_missing_provenance_finds_untagged_records():
    cap = {"schema_version": "1", "meta": observed(a=1), "dependencies": [observed(name="x"), {"name": "y"}]}
    assert missing_provenance(cap) == ["dependencies[1]"]


def test_manifests_effective_files_and_drift(repo: Path):
    m = manifests.collect(repo)
    assert m.effective_files == ["requirements.txt", "requirements-dev.txt"]
    kinds = {(d["kind"], d.get("dependency")) for d in manifests.drift(m)}
    assert ("spec_mismatch", "pydantic") in kinds
    assert ("missing_in_manifest", "six") in kinds
    assert ("requires_python_wider_than_targets", None) in kinds
    mismatch = next(d for d in manifests.drift(m) if d.get("dependency") == "pydantic")
    assert mismatch["conflicting"] is True
    assert [c["name"] for c in m.commented] == ["pyyaml"]
    assert any("pytest" in c["text"] for c in m.test_commands)


def test_requires_python_excluding_targets_is_flagged(tmp_path: Path):
    write(tmp_path, "pyproject.toml", '[project]\nname="x"\nrequires-python="<3.12"\n')
    kinds = [d["kind"] for d in manifests.drift(manifests.collect(tmp_path))]
    assert "requires_python_excludes_targets" in kinds


def test_usage_scan_finds_private_api_monkeypatch_and_skips_local(repo: Path):
    scan = usage.scan(repo)
    mods = scan.third_party_modules()
    assert {"requests", "pydantic", "certifi", "pytest"} <= mods
    assert "demo" not in mods and "json" not in mods
    apis = {(u.api, u.kind) for u in scan.usages}
    assert ("requests.utils._parse_content_type_header", "from_import") in apis
    assert ("requests.Session.request", "monkeypatch") in apis
    assert ("requests.get", "call") in apis
    assert any(u.private for u in scan.usages)
    # A call must not also be reported as a bare attribute read on the same line.
    keys = [(u.file, u.line, u.api) for u in scan.usages if u.kind in ("call", "attribute")]
    assert len(keys) == len(set(keys))
    assert all(u.in_tests for u in scan.usages if u.module == "pytest")


@pytest.mark.parametrize(
    "filename,py,expected",
    [
        ("pkg-1.0-py3-none-any.whl", "3.13", "wheel"),
        ("pkg-1.0-cp312-cp312-win_amd64.whl", "3.13", "none"),
        ("pkg-1.0-cp312-cp312-win_amd64.whl", "3.12", "wheel"),
        ("pkg-1.0-cp38-abi3-manylinux_2_17_x86_64.whl", "3.13", "wheel"),
    ],
)
def test_wheel_support(filename, py, expected):
    assert pypi.wheel_support([{"filename": filename, "packagetype": "bdist_wheel"}], py) == expected


def test_wheel_support_sdist_only():
    files = [{"filename": "pkg-1.0.tar.gz", "packagetype": "sdist"}]
    assert pypi.wheel_support(files, "3.13") == "sdist-only"


def test_capsule_validation_catches_missing_fields():
    stage1 = {"manifests": observed(), "dependencies": [observed(name="x", classification=["unused"])],
              "manifest_drift": [], "import_map": [], "risk_indicators": [], "usage_locations": []}
    cap = capsule.build(stage1, {"kind": "local", "path": "."}, ["inspect"])
    assert capsule.validate(cap) == []
    cap["risk_indicators"].append({"dependency": "x", "indicator": "y", "detail": "z"})
    assert capsule.validate(cap)
