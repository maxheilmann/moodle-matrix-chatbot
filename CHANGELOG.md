# Changelog

Release notes for `moodle_llm_chat`. Everything here is taken from the git history; the short
hashes point at the commits. The project has no git tags yet, so a "release" is the version in
`pyproject.toml` at that point in time.

## Unreleased

### Changed

- **Internal, no change for users or the API:** the three largest files were split by
  responsibility. `index/store.py` (2,180 lines) is now the package `index/store/` with one
  repository per area (`store.sites.add_site(...)`, `store.chat.events(...)`, ...); the plugin's
  `chat.js` (1,722 lines) is a controller plus nine parts (`turn`, `questions`, `toolcards`,
  `links`, `conversations`, `quota`, `citations`, `layout`, `util`); the live event log left
  `ChatService` (`chat/events.py`) and the embedding loop left `Pipeline`
  (`ingest/embedding.py`). The websocket and REST formats, the database schema and everything
  sealed in it are unchanged. See `docs/REFACTORING.md` (R5, R7 to R9).

### Added

- The API reference (`/docs`, `/redoc`, `/openapi.json`) now documents every operation:
  descriptions, written summaries, the two credential kinds (site secret and chat token), the
  401, 403 and route-specific error responses, typed response bodies, and a description for
  every parameter and field. The chat websocket protocol is part of the introduction.
  `tests/unit/test_openapi_docs.py` keeps it that way.
- CI job "Plugin JavaScript — unit tests" runs the plugin JavaScript tests on Node 24 (129 now; new tests for `util`, `links` and the token budget).

## 0.2.0 — Moodle-native rebuild (2026-11-10 to 2026-11-18)

Since the rebuild started: 27 `feat`, 19 `fix`, 5 `build`, 2 `test`, 1 `perf`, 1 `docs`,
1 `style` and 1 `chore` commits.

### Breaking changes

- The Matrix bot, PII tokenisation, the Nuxt web chat, the crawler/cron path, the one-shot RAG
  pipeline, eval/export and the `specs/` directory were removed (`b9fe9a95`: 166 files,
  +714 / −37,906 lines). Tests are now the specification.
- The package was renamed `bsbot` → `moodle_llm_chat` and the environment prefix `BSBOT_` →
  `MLC_`.
- Locations sent to the plugin (tool cards, citations) now carry Moodle ids
  (`[{kind, id, name}]`) instead of the model's virtual paths (`fe6a02e4`). Plugin and API
  must be updated together.

### Added

- **Storage and security:** encryption at rest with SQLCipher, wrapped data keys and AES-GCM
  (`cead5616`); site-scoped encrypted store, site authentication and the ingest API
  (`a2c53ef5`).
- **Ingestion:** staged processing pipeline with persisted jobs (`5f410e1f`); a synthetic demo
  dataset generator (`09888497`).
- **Providers:** provider factory, caching embedder and vision OCR (`740c49c7`); opt-in
  `SEND_DIMENSIONS` for OpenAI-compatible embedding servers (`e5f45393`).
- **Agent:** access scope, scoped search, tool framework and agent runner (`310e6398`).
- **Chat service:** websocket chat, encrypted history and checkpoints, token quotas
  (`82dffe3c`); one stored event per streamed run and pruning of old checkpoints (`2ed8e0b3`);
  conversation titles from a utility model (`a0fd24f1`).
- **Moodle plugin `local_llmchat`:** content source and chat UI (`903352f7`); chat UI rebuilt on
  Moodle templates (`4caebbd3`); readable tool-call details (`4add5f30`); links from tool cards
  to what the user can open (`3932be2e`); chat block and consistent sizing (`9263312e`);
  display blocks for tool cards (`e1236e62`, `170cb1ff`); thinking blocks collapsed and
  grouped with tool cards (`487f7b26`, `8f2b3139`); grouped citations with source cards
  (`d84c8ba0`); answers rendered with markdown-it (`cc890fc8`); token budget ring rendered on
  the server (`00d0b25c`).
- **Tests:** browser tests on a real Moodle with plugin and API (`1f9f513f`).
- **Docs:** setup guide for plugin and API (`3ec30961`).

### Changed

- Only the conversation list is rendered on the server; the chat is always rendered by the
  client. The PHP ports of the markdown and tool-card renderers were dropped
  (`aa04c691`: 23 files, +149 / −1,972 lines).
- A reopened conversation's card links are resolved in one request (`1ced5549`, `perf`).

### Fixed

- A fresh token is sent with answer and continue frames (`756ae5cb`).
- Interaction wire format aligned with the plugin (`d19a6ad5`); user bubbles no longer
  duplicated (`fffa3f78`); `url` modules sent as kind `external` (`a88a029a`).
- Section placement stays current and rate-limited fetches are retried (`bf25ddb5`).
- Reasoning from OpenAI-compatible servers, OpenRouter included, is shown (`8d19c631`).
- Dead space under the full-page chat (`07bac473`); tool card spacing (`63e52055`); narrow
  spaces in answers (`221df06b`).
- Sub-path Moodle installations pass the origin check (`3ec30961`).
- The plugin's ESLint, stylelint and phpcs checks pass in CI (`fa997072`, `e2a2b16b`,
  `bcb7ae03`, `d8a78226`).

## 0.1.0 — Matrix chatbot "bsbot" (2026-08-21 to 2026-11-09)

First version, a chatbot for the Berufsschule grounded in Moodle content that answered through
Matrix. `pyproject.toml` carried version `0.1.0` until the rebuild. The history holds 47 merged
pull requests (#1–#47), among them Matrix refresh fixes, per-user rate limiting, PII masking,
RAG improvements and benchmarks, a Nuxt web chat, and the split into api and cron services
(#31). Most of this code was removed in 0.2.0.
