# Static analysis in the pipeline

Evidence for the Lernfeld 11a field "Qualitätssicherung": static analysis is run automatically
in CI (`.github/workflows/ci.yml`) and what each tool checks is written down here.

## Python (job "Python — lint, types, unit tests")

| Tool | Command | What it checks | Configuration |
|---|---|---|---|
| ruff (lint) | `ruff check .` | Rule sets `E`/`W` (pycodestyle), `F` (pyflakes: unused imports and names), `I` (import order), `UP` (outdated syntax), `B` (likely bugs), `SIM` (simplifiable code), `RUF` (ruff-specific) | `[tool.ruff.lint]` in `pyproject.toml`, line length 100 |
| ruff (format) | `ruff format --check .` | Whitespace, wrapping and quoting; independent of the lint run | `[tool.ruff]` |
| mypy | `mypy` | Types in `src/moodle_llm_chat`. Every function must be fully annotated (`disallow_untyped_defs`); useless `# type: ignore` is an error (`warn_unused_ignores`) | `[tool.mypy]` |

Deliberate exceptions, each with a reason in the config: `B008` (typer uses function calls in
argument defaults), `RUF001` to `RUF003` (German text and typographic characters are
intentional), and `E501` for the long table rows of the demo course material.

## Plugin JavaScript (job "Plugin JavaScript — unit tests")

`node --test moodle/local_llmchat/tests/js/*.test.mjs` runs the unit tests of the chat's
browser code (`amd/src`: frames, markdown rendering, tool-card descriptions, activity
grouping) under Node's built-in test runner. It needs Node 22.15 or newer and no
`npm install`; CI uses Node 24.  Expected outputs live in `tests/fixtures/*_vectors.json`.

## API reference

The OpenAPI document served at `/docs` (Swagger UI), `/redoc` and `/openapi.json` is generated
from the routes. `tests/unit/test_openapi_docs.py` checks it in the unit-test job: every
operation has a description and a written summary, declares its credentials and its 401 and
403 responses, documents its route-specific errors, and every parameter and model field has a
description. The websocket protocol, which OpenAPI cannot express, is part of the API
introduction.

## Moodle plugin (job "Moodle plugin")

Run through `moodle-plugin-ci` on each supported Moodle and PHP version:

| Step | What it checks |
|---|---|
| `phplint` | PHP syntax |
| `phpcs --max-warnings 0` | Moodle coding style; no warnings allowed |
| `phpdoc --max-warnings 0` | Documentation comments; no warnings allowed |
| `validate` | Plugin metadata (`version.php` and similar) |
| `savepoints` | Upgrade steps in `db/upgrade.php` |
| `mustache` | Template syntax |
| `grunt --max-lint-warnings 0` | JavaScript (ESLint) and CSS (stylelint) lint; no warnings allowed |

An additional step compares the chat-token test vectors of the API and the plugin byte for
byte (`cmp`), so the two sides cannot drift apart unnoticed.

## Results

Run locally on the current branch with the CI commands, Python 3.14:

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 152 files already formatted |
| `mypy` | No issues found in 67 source files |
| Unit tests with coverage | 824 passed, 94 % coverage |
| Integration tests | 39 passed |
| Plugin JavaScript tests | 112 passed (Node 24) |

The plugin checks run only in CI, because they need a Moodle installation.
