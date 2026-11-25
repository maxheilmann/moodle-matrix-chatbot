# Refactoring and design patterns

Evidence for the Lernfeld 11a field "Refactoring methodisch vorbereiten und umsetzen". Every
number below was measured on the repository (git history, `git grep`, `wc -l`); the commands
are at the end so they can be repeated.

## 1. Improvements: 4 done, 2 defined

| # | Improvement | Status | Commit / PR | Pattern or principle |
|---|---|---|---|---|
| R1 | `Fetcher` depends on a small `FetchCache` protocol instead of the concrete `Store` | **Done** | `615c733b` | Dependency inversion (port) |
| R2 | Split into separate api and cron services, only the api opens the database | **Done** (superseded by R3) | PR #31 | Service separation |
| R3 | Strip Matrix, PII, Nuxt, crawler and specs; config built around pluggable providers | **Done** | `b9fe9a95`, `740c49c7` | Factory and strategy for providers |
| R4 | Remove the PHP copy of the markdown and tool-card renderers; the client renders everything | **Done** | `aa04c691` (PR #64) | Single source of truth |
| R5 | Split `index/store.py` into one repository per area | Defined | — | Repository |
| R6 | Narrow the 14 modules that import the concrete `Store` to protocols | Defined | — | Dependency inversion (extend R1) |

Why each one was done:

- **R1.** `cron` should fetch and extract without opening the database. The commit message
  states the goal: "only `api` should ever open the SQLite file directly". The existing
  `Store` already satisfied the protocol, so behaviour did not change.
- **R2.** Built on R1. 35 files changed. The cron path was later removed in R3, so this
  refactoring is history rather than part of today's architecture.
- **R3.** Moved the project from a Matrix bot to a Moodle plugin with an API. Everything not
  needed was removed first, then rebuilt on encrypted storage.
- **R4.** The chat page was first rendered on the server with PHP ports of the client's
  markdown and tool-card logic, kept identical by shared test vectors (`43f7d5f3`). The next
  day this was removed, because two implementations of the same rendering had to be kept in
  step. Only the conversation list is rendered on the server now.

## 2. Measurements (indicator and metric)

| Refactoring | Indicator | Before | After |
|---|---|---|---|
| R1 | Files that import the concrete `Store` in `ingest/fetcher.py` | 1 | 0 |
| R1 | Size of the change | — | 5 files, +273 / −146 lines |
| R3 | Lines of code in the repository | — | 166 files, +714 / −37,906 lines |
| R4 | Lines in the rendering path | — | PR #64: 25 files, +152 / −2,184 lines |
| R4 | PHP unit tests for the duplicated renderers | `conversation_view_test`, `markdown_test`, `tool_display_test` (about 670 lines) | removed with the code |

State of the code today, for the defined improvements:

| Indicator | Value |
|---|---|
| Lines in `index/store.py` | 2,180 (next largest file: 555) |
| Methods in `Store` | 127 |
| Modules that import the concrete `Store` | 14 |
| Protocols used as ports | 8 |
| Source lines / test lines | 11,028 / 11,930 |
| Tests | 805 unit and 39 integration, 94 % coverage |

Targets for R5 and R6 would be: no file above about 600 lines, and fewer than 5 modules
importing the concrete `Store`. These are targets, not results.

## 3. Comparison with design patterns

These exist in the code today:

| Pattern | Where | Related refactoring |
|---|---|---|
| Port via `Protocol` (dependency inversion) | `KeyStore`, `AccessChecker`, `QueryEmbedder`, `Extractor`, `OCRProvider`, `TranscriptApi`, `EmbeddingCache`, `OCRCache` | R1, R6 |
| Registry | `ToolRegistry` and `CitationRegistry`; adding a tool is one module plus one `register` line | — |
| Middleware chain | `SiteCORS` (HTTP) and `_WrapUp` (agent step and token limits) | — |
| Factory | `create_app`, `default_registry`, the LLM provider factory | R3 |
| Observer and outbox | Moodle plugin: event observer writes outbox rows, a scheduled task drains them | — |
| Circuit breaker | Live access check: fail closed, open after 3 failures | — |
| Repository (too large) | `Store`, one class for sites, documents, conversations and quotas | R5 |

## 4. Critical assessment (rewrite in your own words)

Facts to build the assessment on:

- The `Protocol` ports work where they were applied: the fetcher, embedder, OCR and extractors
  can be tested with small fakes. But 14 modules still depend on the concrete `Store`, so
  R1's idea has not been carried through.
- `Store` is the biggest weakness: 2,180 lines and 127 methods in one class, about four
  times the next file.
- R4 was added one day and removed the next (`43f7d5f3`, then `aa04c691`). The lesson is to
  check whether two copies of one piece of logic are really needed before building the second.
- R2 shows the risk of architecture refactorings: it was superseded by the rebuild.

## 5. How to reproduce the numbers

```bash
git show --shortstat 615c733b b9fe9a95 aa04c691
git diff --shortstat 47abafed^1 47abafed                      # PR #64
git show 615c733b^:src/bsbot/ingest/fetcher.py | grep "import.*Store"
git grep -l -E "index\.store import|index import .*Store" -- src | wc -l
git grep -E "^class [A-Za-z_]+\(Protocol" -- src | wc -l
wc -l src/moodle_llm_chat/index/store.py
```
