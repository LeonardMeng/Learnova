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

## Progress
- [x] Milestone 1: Connect to Notion
- [x] Milestone 2: User home and data layer
- [x] Milestone 3: Read content