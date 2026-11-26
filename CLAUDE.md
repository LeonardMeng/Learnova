# Learnova

The full spec is in docs/SPEC.md. Read it before starting any task.

## Working rules
- Work on ONE milestone at a time. When it is done, stop and tell me how to verify it.
- Before writing code for a milestone, show me a short plan and wait for approval.
- Explain each new file in 2–3 sentences. I know Python but I'm new to Notion API, LangChain and SQLAlchemy.
- Never read .env. Secrets come from environment variables.
- Do not commit or push; I do that myself.
- Do not create ADRs or extra docs. Record a changed decision in docs/decisions.md in one or two lines.
- Do not add Co-Authored-By or "Generated with Claude Code" to anything.

## Commands
- Run tests: uv run pytest
- Lint: uv run ruff check .
- Web app (in frontend/): npm test, npm run typecheck, npm run build; npm run gen:api after API changes

## Progress
- [x] Milestone 1: Connect to Notion
- [x] Milestone 2: User home and data layer
- [x] Milestone 3: Read content
- [x] Milestone 4: Change detection
- [x] Milestone 5: Connect models
- [x] Milestone 6: Images
- [x] Milestone 7: Gap review
- [x] Milestone 8: Weighting algorithm
- [x] Milestone 9: Quiz and grading
- [ ] Milestone 10: Latest knowledge (`check-latest`): moved to version 2

Version 2 (see "Version 2" in docs/SPEC.md):
- [x] V2.1: Multiple choice and true/false
- [x] V2.2: HTTP API (FastAPI)
- [x] V2.3: Web frontend
- [ ] check-latest: deferred beyond version 2

Version 2.1:
- [x] .learnovaignore

Version 2.2 (see "Version 2.2" in docs/SPEC.md):
- [x] Milestone 1: Issues in the database, and a new report
- [x] Milestone 2: Block anchors
- [x] Milestone 3: Knowledge hierarchy
- [x] Milestone 4: Knowledge base as the first page; Issues page
- [ ] Milestone 5: Quiz on chosen points or a topic
- [ ] Milestone 6: Issues as Notion comments (optional, off by default)

Version 2.3:
- [x] `web import` (one-time, into Notion) and `web check` (diff report against the local cache)

Version 2.4 (not started; after Version 2.2):
- [ ] Bookmark questions, add questions by hand, guided workspace setup
