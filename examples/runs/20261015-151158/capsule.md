# Dependency Exit Capsule: https://github.com/rishrek/LinkWatch

_Generated 2026-10-15T10:36:11+00:00 by break-forecast 0.1.0. Tags: 🔍 observed · ▶️ executed · 🤖 AI-generated._

**Problem (🔍):** 15 dependencies inspected; 3 frozen; 5 upgradable; 2 unused; 1 undeclared; 3 manifest drift findings.

## Dependencies (🔍)

| Dependency | Spec | Latest | Last release | Classification |
|---|---|---|---|---|
| appdirs | ==1.4.4 | 1.4.4 | 2020-05-11 | frozen |
| chardet | ==5.2.0 | 7.6.0 | 2026-08-14 | upgradable |
| click | >=7 | 8.5.0 | 2026-08-26 | current_or_unknown |
| idna | ==2.7 | 3.20 | 2026-09-17 | unused |
| jinja2 | ==2.11.3 | 3.1.6 | 2025-03-05 | upgradable |
| nose | ==1.3.7 | 1.3.7 | 2015-06-02 | frozen |
| pydantic | ==1.10.13 | 2.14.1 | 2026-10-11 | upgradable |
| pytest | (any) | 9.1.1 | 2026-06-19 | current_or_unknown |
| python-dateutil | (any) | 2.9.0.post0 | 2024-03-01 | current_or_unknown |
| requests | ==2.31.0 | 2.34.2 | 2026-05-14 | upgradable |
| rich | (any) | 15.0.0 | 2026-04-12 | current_or_unknown |
| setuptools | ==75.8.0 | 84.0.0 | 2026-08-08 | upgradable |
| six | (any) | 1.17.0 | 2024-12-04 | unused |
| toml | ==0.10.2 | 0.10.2 | 2020-11-01 | frozen |
| certifi | - | - | - | undeclared |

## Baseline (▶️ executed)

Install: `uv pip install -r requirements.txt -r requirements-dev.txt` · pins reproducible as written: **False**

| Python | Attempt | Outcome | Passed/failed/error | Notes |
|---|---|---|---|---|
| 3.12 | as_written | fail | 9/1/0 |  |
| 3.12 | reconstructed (reference) | pass | 10/0/0 | certifi<2023-05-23, charset-normalizer<2023-05-23, markupsafe<2021-02-01, typing-extensions<2023-09-28, urllib3<2023-05-23 |
| 3.12 | strict (-W error::DeprecationWarning) | import-fail | 0/0/0 | ImportError while loading conftest 'C:\istehack\runs\20261015-151158\work\target\tests\conftest.py'. / DeprecationWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html |
| 3.13 | as_written | fail | 9/1/0 |  |
| 3.13 | reconstructed (reference) | pass | 10/0/0 | certifi<2023-05-23, charset-normalizer<2023-05-23, markupsafe<2021-02-01, typing-extensions<2023-09-28, urllib3<2023-05-23 |
| 3.13 | strict (-W error::DeprecationWarning) | import-fail | 0/0/0 | ImportError while loading conftest 'C:\istehack\runs\20261015-151158\work\target\tests\conftest.py'. / DeprecationWarning: Failing to pass a value to the 'type_params' parameter of 'typing._eval_type' is deprecated, as it leads to incorrect behaviour when calling typing._eval_type on a stringified annotation that references a PEP 695 type parameter. It will be disallowed in Python 3.15. |

## Risk indicators (🔍)

| Dependency | Indicator | Detail |
|---|---|---|
| appdirs | no_newer_version | Pinned 1.4.4 is already the latest release |
| appdirs | frozen | no release for 6.43 years (last 2020-05-11) |
| chardet | outdated_pin | Pinned 5.2.0, latest 7.6.0 (2 major versions behind) |
| click | unpinned | Spec >=7 - resolved version decided at install time |
| idna | outdated_pin | Pinned 2.7, latest 3.20 (1 major versions behind) |
| idna | unused | Declared but never imported |
| jinja2 | outdated_pin | Pinned 2.11.3, latest 3.1.6 (1 major versions behind) |
| nose | no_newer_version | Pinned 1.3.7 is already the latest release |
| nose | frozen | no release for 11.37 years (last 2015-06-02) |
| pydantic | outdated_pin | Pinned 1.10.13, latest 2.14.1 (1 major versions behind) |
| pytest | unpinned | Spec (any) - resolved version decided at install time |
| python-dateutil | stale | Last release 2.62 years ago |
| python-dateutil | unpinned | Spec (any) - resolved version decided at install time |
| requests | outdated_pin | Pinned 2.31.0, latest 2.34.2 |
| requests | private_api_use | Uses private API: requests.utils._parse_content_type_header |
| requests | monkeypatch | Assigns to attributes of this package at runtime: requests.Session.request |
| requests | wide_usage | Used in 3 source files |
| rich | unpinned | Spec (any) - resolved version decided at install time |
| setuptools | outdated_pin | Pinned 75.8.0, latest 84.0.0 (9 major versions behind) |
| six | unpinned | Spec (any) - resolved version decided at install time |
| six | unused | Declared but never imported |
| toml | no_newer_version | Pinned 0.10.2 is already the latest release |
| toml | frozen | no release for 5.95 years (last 2020-11-01) |
| certifi | undeclared | Imported as `certifi` but not declared in any manifest (distribution found via pypi_name_match) |
| pydantic | manifest_drift | Conflicting specs - pyproject.toml: >=2; requirements.txt: ==1.10.13 |
| (project) | manifest_drift | pyproject.toml requires-python >=3.10 claims support beyond tested targets |
| pyyaml | manifest_drift | Commented-out requirement 'PyYAML==5.1' |

## Usage locations (🔍)

| File:line | API | Kind |
|---|---|---|
| linkwatch/cache.py:4 | `appdirs.user_cache_dir` | from_import |
| linkwatch/cache.py:10 | `appdirs.user_cache_dir` | call |
| linkwatch/checker.py:4 | `certifi` | import |
| linkwatch/checker.py:5 | `requests` | import |
| linkwatch/checker.py:6 | `requests.Session` | from_import |
| linkwatch/checker.py:13 | `requests.Session` | call |
| linkwatch/checker.py:15 | `certifi.where` | call |
| linkwatch/checker.py:35 | `requests.exceptions.ConnectionError` | attribute |
| linkwatch/checker.py:38 | `requests.exceptions.Timeout` | attribute |
| linkwatch/cli.py:1 | `click` | import |
| linkwatch/cli.py:2 | `rich.console.Console` | from_import |
| linkwatch/cli.py:3 | `rich.live.Live` | from_import |
| linkwatch/cli.py:4 | `rich.table.Table` | from_import |
| linkwatch/cli.py:9 | `rich.console.Console` | call |
| linkwatch/cli.py:27 | `click.group` | call |
| linkwatch/cli.py:28 | `click.version_option` | call |
| linkwatch/cli.py:34 | `click.option` | call |
| linkwatch/cli.py:51 | `click.option` | call |
| linkwatch/cli.py:53 | `click.option` | call |
| linkwatch/cli.py:13 | `rich.table.Table` | call |
| linkwatch/cli.py:62 | `click.echo` | call |
| linkwatch/cli.py:68 | `click.echo` | call |
| linkwatch/cli.py:69 | `click.echo` | call |
| linkwatch/cli.py:40 | `rich.live.Live` | call |
| linkwatch/cli.py:60 | `click.ClickException` | call |
| linkwatch/config.py:3 | `toml` | import |
| linkwatch/config.py:4 | `pydantic.BaseModel` | from_import |
| linkwatch/config.py:4 | `pydantic.validator` | from_import |
| linkwatch/config.py:14 | `pydantic.validator` | call |
| linkwatch/config.py:20 | `pydantic.validator` | call |
| linkwatch/config.py:37 | `toml.load` | call |
| linkwatch/dates.py:1 | `dateutil.parser` | from_import |
| linkwatch/dates.py:8 | `dateutil.parser.parse` | call |
| linkwatch/encoding.py:4 | `chardet` | import |
| linkwatch/encoding.py:5 | `requests.utils._parse_content_type_header` | from_import |
| linkwatch/encoding.py:11 | `requests.utils._parse_content_type_header` | call |
| linkwatch/encoding.py:15 | `chardet.detect` | call |
| linkwatch/http_patch.py:1 | `requests` | import |
| linkwatch/http_patch.py:14 | `requests.Session.request` | monkeypatch |
| linkwatch/http_patch.py:5 | `requests.Session.request` | attribute |
| linkwatch/report.py:3 | `jinja2.Environment` | from_import |
| linkwatch/report.py:3 | `jinja2.FileSystemLoader` | from_import |
| linkwatch/report.py:3 | `jinja2.select_autoescape` | from_import |
| linkwatch/report.py:9 | `jinja2.Environment` | call |
| linkwatch/report.py:10 | `jinja2.FileSystemLoader` | call |
| linkwatch/report.py:11 | `jinja2.select_autoescape` | call |
| linkwatch/resources.py:1 | `pkg_resources` | import |
| linkwatch/resources.py:7 | `pkg_resources.resource_filename` | call |

## Limitations (🔍)

- **pins on Python 3.12**: Pins as written are not reproducible: tests/test_report.py::test_report_renders_all_sites: ImportError: cannot import name 'soft_unicode' from 'markupsafe' (C:\istehack\runs\20261015-151158\envs\baseline-3.12-as_written\Lib\site-packages\markupsafe\__init__.py)
- **pins on Python 3.13**: Pins as written are not reproducible: tests/test_report.py::test_report_renders_all_sites: ImportError: cannot import name 'soft_unicode' from 'markupsafe' (C:\istehack\runs\20261015-151158\envs\baseline-3.13-as_written\Lib\site-packages\markupsafe\__init__.py)
- **patch for upgrade:pydantic@2.14.1**: tests pass on every target Python, but differential probes show behaviour changes (see behavioral_diffs); review before adopting
- **patch for replace:toml**: tests pass on every target Python, but differential probes show behaviour changes (see behavioral_diffs); review before adopting
- **exit plan (all changes together)**: verified_tests: behaviour_preserved
- **upgrade:chardet@7.6.0**: model judged this not fixable by a source change: No source code change can fix this; the issue must be resolved by adjusting the dependency pins in the manifest to ensure compatible versions of urllib3 and chardet are installed together.
- **all-latest**: model judged this not fixable by a source change: The issue is an environment/installation problem. The environment needs 'setuptools' installed or the test runner needs to be updated to use 'importlib.metadata' instead of 'pkg_resources'.
- **probe jinja2**: not usable, so not run: syntax error line 28: unterminated string literal (detected at line 28)
- **probe requests**: not usable, so not run: syntax error line 57: unexpected character after line continuation character
