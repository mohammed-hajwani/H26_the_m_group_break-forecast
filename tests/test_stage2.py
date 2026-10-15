import textwrap
from pathlib import Path

from break_forecast import baseline, testrun
from break_forecast.runctx import CmdResult

COMPILE_OUTPUT = textwrap.dedent("""\
    jinja2==2.11.3
        # via -r requirements.txt
    markupsafe==3.0.3
        # via jinja2
    requests==2.31.0
        # via -r requirements.txt
    urllib3==2.8.0
        # via requests
    rich==15.0.0
        # via -r requirements.txt
    pygments==2.20.0
        # via rich
    typing-extensions==4.16.0
        # via
        #   pydantic
        #   rich
    pydantic==1.10.13
        # via -r requirements.txt
    deep==1.0
        # via markupsafe
""")


def test_parse_compile_reads_single_and_multi_parents():
    pins, parents = baseline.parse_compile(COMPILE_OUTPUT)
    assert pins["markupsafe"] == "3.0.3"
    assert parents["markupsafe"] == ["jinja2"]
    assert parents["typing-extensions"] == ["pydantic", "rich"]
    assert parents["jinja2"] == ["-r requirements.txt"]


def test_transitive_cutoffs_follow_pinned_parents():
    pins, parents = baseline.parse_compile(COMPILE_OUTPUT)
    direct = {"jinja2", "requests", "rich", "pydantic"}
    dates = {"jinja2": "2021-02-01", "requests": "2023-05-23", "pydantic": "2023-09-28"}  # rich unpinned
    cutoffs = baseline.transitive_cutoffs(pins, parents, direct, dates)
    assert cutoffs["markupsafe"] == "2021-02-01"
    assert cutoffs["urllib3"] == "2023-05-23"
    assert cutoffs["deep"] == "2021-02-01"          # inherits through markupsafe
    assert "pygments" not in cutoffs                # parent rich is unpinned
    assert "typing-extensions" not in cutoffs       # one parent (rich) is unpinned
    assert not set(cutoffs) & direct


def test_install_sources_follow_effective_files():
    stage1 = {"manifests": {"effective_files": ["requirements.txt", "requirements-dev.txt"],
                            "install_commands": [{"installs_project": False}]}}
    assert baseline.install_args(baseline.install_sources(stage1)) == [
        "-r", "requirements.txt", "-r", "requirements-dev.txt"]
    stage1["manifests"]["effective_files"] = ["pyproject.toml"]
    assert baseline.install_args(baseline.install_sources(stage1)) == ["-e", "."]
    stage1["manifests"]["install_commands"] = [{"installs_project": True, "project_extras": ["test", "dev"]}]
    assert baseline.install_args(baseline.install_sources(stage1)) == ["-e", ".[dev,test]"]  # sorted
    assert baseline._project_extras(".[test,dev]") == ["test", "dev"]


def test_ci_install_command_extras_are_detected(tmp_path: Path):
    from break_forecast import manifests

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("steps:\n  - run: pip install -e .[test,docs]\n  - run: python -m pytest\n", "utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["six"]\n', encoding="utf-8")
    m = manifests.collect(tmp_path)
    [cmd] = m.install_commands
    assert cmd["installs_project"] and cmd["project_extras"] == ["docs", "test"]
    assert m.effective_files == ["pyproject.toml"]
    assert any("pytest" in c["text"] for c in m.test_commands)


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
  <testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py" line="1" time="0.1"/>
  <testcase classname="tests.test_a" name="test_bad" file="tests/test_a.py" line="5" time="0.1">
    <failure message="ImportError: cannot import name 'soft_unicode'">trace</failure></testcase>
  <testcase classname="tests.test_b" name="test_skip" file="tests/test_b.py" line="1" time="0">
    <skipped message="skip"/></testcase>
</testsuite></testsuites>"""


def _res(rc: int, stdout: str = "", stderr: str = "") -> CmdResult:
    return CmdResult(["pytest"], rc, stdout, stderr, 1.0)


def test_parse_junit_outcomes(tmp_path: Path):
    junit = tmp_path / "r.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    run = testrun.parse_result(_res(1), junit, "normal")
    assert run.outcome == "fail"
    assert run.tests == {"tests/test_a.py::test_ok": "passed", "tests/test_a.py::test_bad": "failed",
                         "tests/test_b.py::test_skip": "skipped"}
    assert "soft_unicode" in run.failures["tests/test_a.py::test_bad"]


def test_conftest_import_error_is_import_fail(tmp_path: Path):
    stderr = ("ImportError while loading conftest 'tests/conftest.py'.\n"
              "linkwatch/resources.py:1: in <module>\n    import pkg_resources\n"
              "E   DeprecationWarning: pkg_resources is deprecated as an API.\n")
    run = testrun.parse_result(_res(4, stderr=stderr), tmp_path / "missing.xml", "strict")
    assert run.outcome == "import-fail"
    assert "pkg_resources is deprecated" in run.error_excerpt
    assert "while loading conftest" in run.error_excerpt


def test_warnings_are_parsed_and_deduplicated():
    stdout = ("== warnings summary ==\n"
              "linkwatch/resources.py:1\n"
              "  C:/x/linkwatch/resources.py:1: DeprecationWarning: pkg_resources is deprecated\n"
              "  C:/x/linkwatch/resources.py:1: DeprecationWarning: pkg_resources is deprecated\n")
    assert testrun.parse_warnings(stdout) == [
        {"category": "DeprecationWarning", "message": "pkg_resources is deprecated",
         "head": "pkg_resources is deprecated", "location": "resources.py:1"}]


def test_multiline_warning_keeps_continuation_lines():
    stdout = ("== warnings summary ==\n"
              "tests/test_config.py::test_sites_file_loads\n"
              "  C:/x/pydantic/_internal/_config.py:383: UserWarning: Valid config keys have changed in V2:\n"
              "  * 'anystr_strip_whitespace' has been renamed to 'str_strip_whitespace'\n"
              "    warnings.warn(message, UserWarning)\n"
              "\n-- Docs: https://docs.pytest.org\n")
    [w] = testrun.parse_warnings(stdout)
    assert w["head"] == "Valid config keys have changed in V2:"
    assert "anystr_strip_whitespace" in w["message"]


def test_diff_tests_ignores_path_separator_differences():
    ref = testrun.TestRun("normal", "pass", tests={"tests\\test_a.py::t": "passed"})
    new = testrun.TestRun("normal", "pass", tests={"tests/test_a.py::t": "passed"})
    assert testrun.diff_tests(ref, new) == {"newly_failing": [], "newly_passing": [], "missing": [], "added": []}


def test_diff_tests():
    ref = testrun.TestRun("normal", "pass", tests={"a": "passed", "b": "passed", "c": "failed"})
    new = testrun.TestRun("normal", "fail", tests={"a": "passed", "b": "failed", "c": "passed", "d": "passed"})
    assert testrun.diff_tests(ref, new) == {"newly_failing": ["b"], "newly_passing": ["c"],
                                            "missing": [], "added": ["d"]}
