# Decisions

- 2026-11-25: Ollama support deferred; v1 models are OpenAI, Anthropic, Google and DeepSeek for now.
- 2026-11-25: Possible duplicate knowledge points are merged by hand with `learnova kp merge <point> --into <existing>`; the review report prints the command. Not in the spec command table; review stays non-interactive.
- 2026-11-25: Mastery is always recomputed by replaying a point's attempts in time order. `kp merge` moves links in Milestone 7; Milestone 9 must extend it to move attempts and recompute mastery (with a test).
- 2026-11-25: Added `learnova kp list [--workspace]` and `learnova kp importance <point> <1-5>` (not in the spec command table) so points can be seen and importance changed before `status` exists.
- 2026-11-25: Weights use fractional days since the last quiz. A point never quizzed counts as `new_point_days` (default 7, in config.toml `[weights]`) so new points are not starved by long-unreviewed ones.
- 2026-11-25: Milestone 10 (`check-latest`) moved to version 2; version 1 ends at Milestone 9.
- 2026-11-25: Version 2 scope and order: (1) multiple choice + true/false, graded locally; (2) FastAPI on 127.0.0.1, no auth; (3) React + Vite + TypeScript frontend; (4) check-latest. Folder source and Ollama deferred. See "Version 2" in SPEC.md.
- 2026-11-25: Quiz questions are stored in `issued_questions` until answered or skipped (open ones expire after 7 days); the CLI and the API both use it. The API listens on 127.0.0.1 only and, having no auth, checks the Host header, requires JSON POST bodies and limits CORS to `[api] allowed_origins`.
- 2026-11-25: Web frontend in `frontend/` (React + Vite + TypeScript, React Router, TanStack Query, react-markdown). TypeScript is pinned to 5.x because openapi-typescript needs its compiler API (TypeScript 7 has none). `learnova serve` serves `frontend/dist` when it exists. Report lists are ordered by the date and run number in the file name, not file times.
- 2026-11-25: Version 2 ends at V2.3; `check-latest` deferred again, to a later version.
- 2026-11-25: `.learnovaignore` hides pages instead of deleting them (`notes.ignored`, migration 0008), so un-ignoring restores links to knowledge points without a new review. A page deleted in Notion while hidden is still deleted.
