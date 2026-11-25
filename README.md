# Learnova

An AI teaching assistant for your study notes. Learnova reads your notes from Notion, describes
the images in them, extracts knowledge points, and flags factual errors, unclear explanations and
missing topics. It only reads Notion and never writes back; every action starts from the command
line.

The full design is in [docs/SPEC.md](docs/SPEC.md); changed decisions are in
[docs/decisions.md](docs/decisions.md).

## Setup

1. Install dependencies: `uv sync` (needs [uv](https://docs.astral.sh/uv/); Python 3.11+ is
   downloaded if missing).
2. Create a Notion integration, put its secret in `.env` as `NOTION_API_KEY`, and add the
   integration to your page in Notion under **••• → Connections**. Access passes down to every
   sub-page, so sharing one top-level page is enough.
3. Put your model API key in `.env` too (see `.env.example` for the standard names).
4. Tell Learnova which models to use in `~/.learnova/config.toml` (created on first run):

   ```toml
   [models]
   main = "openai:gpt-6.1-terra"     # provider:model; providers: openai, anthropic, google_genai, deepseek
   vision = "openai:gpt-6.1-terra"   # describes images; may differ from main

   [models.api_key_env]              # only if a key is not under its standard name
   openai = "MODEL_API_KEY"

   [weights]                         # optional: how quiz questions are drawn
   epsilon = 0.1                     # mastered points keep at least this share of weight
   new_point_days = 7                # never-quizzed points count as this many days unreviewed

   [mastery]                         # optional
   alpha = 0.3                       # mastery = moving average of score / 5

   [mastery.type_weights]            # optional: how much one answer moves mastery
   short_answer = 1.0
   multiple_choice = 0.6
   true_false = 0.3                  # lowest: guessing gets 50%
   ```

Learnova reads secrets from environment variables. To load `.env` and run from any directory,
an alias is handy:

```bash
alias lv='uv run --project ~/WorkSpace/Learnova --env-file ~/WorkSpace/Learnova/.env learnova'
```

The examples below use `learnova`; with the alias, type `lv` instead.

## Typical workflow

```bash
learnova workspace new interview --notion <page-url> --path ~/notes/interview
cd ~/notes/interview
learnova sync          # read notes from Notion, download and describe images
learnova review        # extract knowledge points and write a report to reports/
# fix the problems in Notion, then:
learnova sync && learnova review    # only the notes you changed are reviewed again
learnova quiz          # from anywhere: questions drawn from all your workspaces
learnova status        # mastery and your weakest points
```

## Which workspace a command uses

Commands that work inside a workspace (`sync`, `review`) use, in order: the workspace named with
`--workspace`, the workspace whose `learnova.toml` is in the current directory or a parent
directory, or the default set with `workspace use`.

## Commands

### `learnova ping PAGE`

Checks the Notion connection by listing every sub-page under a page. PAGE is a page URL or ID.
If Notion can't see the page, add your integration under the page's **Connections**.

```bash
learnova ping "https://app.notion.com/p/Interview-Questions-3e6135e3ecf04389846ce4126df126b0"
```

### `learnova workspace new NAME --notion PAGE [--path DIR]`

Creates a workspace for one Notion page: writes `learnova.toml` into DIR (default: the current
directory; created if missing) and registers it in `~/.learnova`. Checks first that Notion can see
the page. Your first workspace becomes the default.

```bash
learnova workspace new interview --notion <page-url> --path ~/notes/interview
```

### `learnova workspace list`

Lists all workspaces with their path, Notion page and status. ★ marks the default, → the one a
command run from here would use; a moved or deleted directory shows as **not found**.

```bash
learnova workspace list
```

### `learnova workspace use NAME`

Sets the default workspace, used when you run a command outside any workspace directory.

```bash
learnova workspace use interview
```

### `learnova workspace remove NAME`

Unregisters a workspace. Its directory, its Notion page and your knowledge points are left as
they are.

```bash
learnova workspace remove interview
```

### `learnova sync [--workspace NAME] [--full] [--no-describe]`

Reads the workspace's Notion page and all its sub-pages into `cache/` as Markdown, downloads
images into `images/`, and reports what was added (`+`), changed (`~`), renamed or moved (`>`)
and deleted (`-`). Unchanged pages are not downloaded again. New images are then described by the
vision model and the descriptions are written into the notes.

- `--full`: download every page again, e.g. to be sure nothing was missed.
- `--no-describe`: skip the image descriptions (no model calls).

```bash
learnova sync
learnova sync --workspace interview --full
```

### `learnova review [--all] [--workspace NAME]`

Sends each note changed since its last review to the main model, which extracts knowledge points
and flags factual errors, unclear or incomplete explanations, and missing topics. Writes a report
to `reports/<date>-review.md`. Points already found in a note are kept, updated or removed, not
created again; new points that may duplicate an existing one are flagged with a ready-made
`kp merge` command. Your notes are never changed.

- `--all`: review every note, not only changed ones.

```bash
learnova review
learnova review --all
```

### `learnova quiz [--count N] [--workspace NAME] [--type short|mc|tf]`

Asks N questions (default 5) on knowledge points drawn by weight: important, weakly mastered and
long-unquizzed points come up most, mastered ones now and then. Draws from all workspaces unless
`--workspace` is given. The model writes each question from your notes. Each answer updates the
point's mastery.

- `--type short` (default): answer in your own words, finishing with an empty line. The model
  grades it from 0 to 5 (one decimal) using the levels in
  [docs/grading-rubric.md](docs/grading-rubric.md); edit that file to change how answers are
  graded.
- `--type mc`: multiple choice, answer A–D. `--type tf`: true/false, answer `t` or `f`. These are
  checked locally (5 if right, 0 if wrong, no model call) and move mastery less than short
  answers, true/false least (see `[mastery.type_weights]`).

Type `s` to skip a question or `q` to stop.

```bash
learnova quiz
learnova quiz --count 10 --workspace interview
learnova quiz --type mc
```

### `learnova status [--workspace NAME] [--weakest N]`

Shows how many points you have and have quizzed, average mastery and when you last quizzed,
overall and per workspace, then your N weakest points (default 10).

```bash
learnova status
learnova status -w interview --weakest 20
```

### `learnova kp list [--workspace NAME]`

Lists your knowledge points, most important first, with the notes they come from. Points belong
to you, not to a workspace; `--workspace` shows only points from that workspace.

```bash
learnova kp list
learnova kp list -w interview
```

### `learnova kp importance POINT VALUE`

Sets a point's importance from 1 (trivia) to 5 (core idea). Reviews never change it afterwards.
POINT is the point's name (any case) or an ID of at least 8 characters.

```bash
learnova kp importance "Redis persistence: RDB vs AOF" 5
```

### `learnova kp merge POINT --into EXISTING`

Merges a duplicate point into another: the kept point gets all of the duplicate's notes and quiz
attempts, its mastery is recomputed from all of them, and the duplicate is removed. The review report prints this command under each possible duplicate.

```bash
learnova kp merge "GD" --into "Gradient descent"
```

### `learnova ask QUESTION [--model PROVIDER:MODEL] [--vision]`

Debugging aid: asks a model one question and prints the reply, time taken and tokens, to check
that a model is set up. `--model` tries another model without editing the config; `--vision`
asks the configured vision model.

```bash
learnova ask "hello"
learnova ask "hello" --model anthropic:<model-name>
```

## Files Learnova creates

```
~/.learnova/            config.toml (database URL, models) and learnova.db
<workspace>/
├── learnova.toml       marks the directory as a workspace
├── cache/              one Markdown file per note
├── images/             downloaded images, named by content hash
└── reports/            review reports
```

## Version 2 candidates

`check-latest` (look for recent changes to a knowledge point), Ollama, a FastAPI layer, Postgres,
a local-folder source, more question types and webhooks. See [docs/SPEC.md](docs/SPEC.md) and
[docs/decisions.md](docs/decisions.md).

## Development

```bash
uv run pytest          # tests (no network or model calls)
uv run ruff check .    # lint
```
