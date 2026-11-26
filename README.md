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

   [api]                             # optional: browser pages allowed to call `learnova serve`
   allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
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

## Leaving pages out: `.learnovaignore`

Put a `.learnovaignore` file in the workspace folder (`workspace new` creates a commented one) to
leave sub-pages out of the workspace. One pattern per line; matching ignores case:

```gitignore
Behaviour Questions        # a page with this title, at any depth, and everything under it
Draft*                     # * and ? work within a title
Database / Old notes       # a path of titles, counted from the workspace's top page
**/Scratch                 # ** matches any number of levels
3e6135e3ecf04389846ce4126df126b0   # a page by its Notion ID or URL (survives renames)
!Draft – final             # ! brings a page back; the last matching line wins
```

(Comments go on their own lines in the real file.) Ignored pages aren't read from Notion. Pages
that were already synced are hidden, not deleted: `sync` shows them as `× ignored`, and review,
quiz and status leave them out. Remove the line and run `sync` to bring them back (`↩ restored`)
with their knowledge points. A page inside an ignored page can't be brought back with `!`; ignore
its siblings instead. Titles that contain `/` can only be matched by their page ID.

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

Sends each note changed since its last review to the main model, which extracts knowledge points,
flags factual errors, unclear or incomplete explanations and missing topics, and checks which of
the note's open issues are now fixed. Writes a report to `reports/<date>-review.md`: this run's
summary, the five open issues to fix first, what was fixed, the other open issues by note, and
possible duplicate points. Points already found in a note are kept, updated or removed, not
created again; new points that may duplicate an existing one are flagged with a ready-made
`kp merge` command. Your notes are never changed.

- `--all`: review every note, not only changed ones.

```bash
learnova review
learnova review --all
```

### `learnova quiz [--count N] [--workspace NAME | --point POINT … | --topic TOPIC] [--type short|mc|tf]`

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

`--point` (repeatable) and `--topic` limit the quiz to those points; a topic stands for every
point under it, shortcuts included (topics themselves aren't asked). Points are still drawn by
weight, and when there are fewer points than questions they are asked again (never the same one
twice in a row if there is another). They can't be combined with `--workspace`. In the web app,
tick points in the Knowledge tree and press "Practice selected", or use "Practice this topic".

```bash
learnova quiz
learnova quiz --count 10 --workspace interview
learnova quiz --type mc
learnova quiz --topic "Redis persistence" -n 8
learnova quiz --point "RDB snapshots" --point "AOF log"
```

### `learnova status [--workspace NAME] [--weakest N]`

Shows how many points you have and have quizzed, average mastery and when you last quizzed,
overall and per workspace, then your N weakest points (default 10).

```bash
learnova status
learnova status -w interview --weakest 20
```

### `learnova issues [--workspace NAME] [--kind KIND] [--status STATUS]`

Lists the problems reviews found, most important first:
`priority = kind weight × importance × (1 + (1 − mastery))`, so a wrong fact about an important
point you haven't mastered comes first. Kinds: `factual_error`, `unclear`, `missing`. Shows open
issues unless `--status` is `fixed`, `dismissed`, `obsolete` or `all`. The weights are under
`[issues]` in `config.toml` (`factual_error = 3`, `unclear = 2`, `missing = 1`).

Issues are kept between reviews: re-reviewing a changed note closes the issues it no longer has
and doesn't raise the same issue twice, even when the model words or quotes it a little
differently. A note whose text hasn't changed keeps its issues as they are, even with
`review --all`. Change one by hand with its id (first 8+ characters):

```bash
learnova issues
learnova issues fix 3fa9c1d2       # you fixed it (a later review would also notice)
learnova issues dismiss 3fa9c1d2   # not a problem: reviews won't raise it again
learnova issues reopen 3fa9c1d2
```

Each issue links to the paragraph it quotes in Notion: the id in `learnova issues` is a link
(in terminals that support links, such as iTerm2 or VS Code's), and reports have an
"Open in Notion" link. Issues without a matching paragraph (and missing topics) link to the page.
How closely a quote must match a paragraph is `anchor_threshold` under `[issues]` (default 0.6).

Issues of a deleted note become `obsolete`; issues of an ignored note are hidden with it. After
upgrading to this version, run `learnova sync --full` (to record each note's paragraphs for the
links) and then `learnova review --all` once so issues from earlier reviews are
recorded (until then they exist only in old report files).

### `learnova kp list [--workspace NAME] [--flat]`

Lists your knowledge points as a tree, with the notes they come from. Each point sits under its
parent, with the most important first. A point with points under it is a **topic**: it shows its
mastery (the importance-weighted average of the quizzed points under it, e.g. `62% · 4 of 7
quizzed`) and is not quizzed itself. "↗ also under" marks a shortcut. Points belong to you, not
to a workspace; `--workspace` shows only points from that workspace (and the topics above them).
`--flat` gives one list, most important first.

```bash
learnova kp list
learnova kp list -w interview
learnova kp list --flat
```

### `learnova kp move POINT --under PARENT` / `--top`, `kp link`, `kp unlink`

Arrange points by hand. Every point lives under at most one parent; `link` also shows it under up
to 2 other parents (shortcuts). Loops and trees deeper than 4 levels are refused with a message.

```bash
learnova kp move "RDB snapshots" --under "Redis persistence"
learnova kp move "Redis persistence" --top
learnova kp link "Eviction" --also-under "Caching"
learnova kp unlink "Eviction" --from "Caching"
```

A review can also put a new point under one of your topics or the note's other points.

### `learnova kp organize [--workspace NAME] [--dry-run] [--yes]`

Asks the model to organize your points: new topics, moves and shortcuts. Every change is checked
against the rules above; the ones that break them are marked refused. The proposal is printed and
written to `reports/<date>-organize.md` (in the workspace with `--workspace`, otherwise in
`~/.learnova/reports/`). Then you confirm once, and all the changes that can be applied are
applied together. `--dry-run` only writes the proposal; `--yes` applies without asking. With
`--workspace`, only that workspace's points are moved; topics from other workspaces can still
be their parents.

```bash
learnova kp organize -w interview --dry-run
learnova kp organize
```

### `learnova kp importance POINT VALUE`

Sets a point's importance from 1 (trivia) to 5 (core idea). Reviews never change it afterwards.
POINT is the point's name (any case) or an ID of at least 8 characters.

```bash
learnova kp importance "Redis persistence: RDB vs AOF" 5
```

### `learnova kp merge POINT --into EXISTING`

Merges a duplicate point into another: the kept point gets all of the duplicate's notes and quiz
attempts, its mastery is recomputed from all of them, and the duplicate is removed. Points under
the duplicate move to the kept point; the kept point stays where it is (or takes the duplicate's
place if it was at the top level). The review report prints this command under each possible
duplicate.

```bash
learnova kp merge "GD" --into "Gradient descent"
```

### `learnova serve [--port 8000]`

Starts the HTTP API on `127.0.0.1` (this computer only; there is no authentication). Open
`http://127.0.0.1:8000/docs` to see and try every endpoint. The API calls the same code as the
commands above, so an answer given over HTTP is recorded exactly like one given in the terminal.

A quiz takes two requests per question: `POST /api/quiz/questions` with
`{"type": "short" | "mc" | "tf", "workspace": null, "exclude": [point ids asked so far]}` returns
the question (never its answer), and `POST /api/quiz/questions/{id}/answer` with
`{"answer": "..."}` grades it. Questions stay open until answered or skipped
(`POST .../skip`); unanswered ones are removed after 7 days. Sync and review
(`POST /api/workspaces/{name}/sync`, `.../review`) wait until they finish.

To protect it while it runs without a password, the API only accepts requests addressed to
`127.0.0.1` or `localhost`, only accepts POST bodies sent as JSON, and only lets browser pages
from the origins in `[api] allowed_origins` (default: the Vite dev server) call it.

```bash
learnova serve
curl http://127.0.0.1:8000/api/status
```

## Web app

A browser interface on top of the API, in `frontend/` (React + Vite + TypeScript). A sidebar
(with a search box for points) opens the pages; each page you open gets a tab above the page,
and the tabs are remembered in the browser.

- **Knowledge › Overview** (the first page): points, average mastery, open issues and last quiz;
  the tree of points (topics open and close; ↗ shortcuts jump to the point's own place); and the
  selected point's details: where it lives, its notes, open issues, quiz answers with a score
  trend, and controls for importance, "Move to…" and shortcuts. The sidebar search filters the
  points and shows where each one lives and "also in: …".
- **Knowledge › All points**: one list, sortable by mastery, importance, last quiz or issues.
- **Workspaces › Quiz**: short answer, multiple choice or true/false; answer with the keyboard
  (A–D, T/F, Ctrl/⌘+Enter); a reload continues the current question.
- **Workspaces › History**: coming soon.
- **Workspaces › Issues**: open issues by priority, with a Notion link, Fix and Dismiss.
- **Workspaces › Sync & review**: the Sync and Review buttons for a workspace.
- **Workspaces › one entry per workspace**: its Notion page tree with the pages' icons; a page
  shows its knowledge points and issues.
- **Workspaces › Web imports**: the sites imported with `learnova web import`.
- **Reports › All issues**: every issue in any status (Reopen a fixed or dismissed one), and the
  workspace's report files below.

Pages with a workspace picker show one workspace or all of them; the choice is kept in the URL.
Page icons appear after the next `learnova sync`.

One-time setup (needs Node 20+):

```bash
cd frontend
npm ci
npm run build          # writes frontend/dist, which `learnova serve` serves
```

Then `learnova serve` and open http://127.0.0.1:8000.

Or start everything with one script from the project folder (it installs the web app's packages
on first run and reads `.env`; Ctrl+C stops it all):

```bash
scripts/dev.sh           # API + web app with live reload: open http://localhost:5173
scripts/dev.sh --built   # build the web app, then serve it all on http://127.0.0.1:8000
```

The first form is for working on the frontend: edits to `frontend/src` show up in the browser
straight away, and `/api` is forwarded to the API on port 8000. After changing the API, run `npm run gen:api` to regenerate the TypeScript
types; a Python test fails until you do. Frontend tests: `npm test`; type check:
`npm run typecheck`.

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

`check-latest` (look for recent changes to a knowledge point), Ollama, Postgres,
a local-folder source, more question types and webhooks. See [docs/SPEC.md](docs/SPEC.md) and
[docs/decisions.md](docs/decisions.md).

## Development

```bash
uv run pytest          # tests (no network or model calls)
uv run ruff check .    # lint
cd frontend && npm test && npm run typecheck   # web app
```
