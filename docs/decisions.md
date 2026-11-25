# Decisions

- 2026-11-25: Ollama support deferred; v1 models are OpenAI, Anthropic, Google and DeepSeek for now.
- 2026-11-25: Possible duplicate knowledge points are merged by hand with `learnova kp merge <point> --into <existing>`; the review report prints the command. Not in the spec command table; review stays non-interactive.
- 2026-11-25: Mastery is always recomputed by replaying a point's attempts in time order. `kp merge` moves links in Milestone 7; Milestone 9 must extend it to move attempts and recompute mastery (with a test).
- 2026-11-25: Added `learnova kp list [--workspace]` and `learnova kp importance <point> <1-5>` (not in the spec command table) so points can be seen and importance changed before `status` exists.
