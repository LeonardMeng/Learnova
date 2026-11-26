# Learnova · Project Spec v0.1

2026-11-24

## Goals and core concepts

Version 1 is a Python command-line tool called Learnova: an AI teaching assistant for your study notes. It works like a harness that goes wherever you go. You can run it from any directory, and it manages the multiple workspaces you create. Each workspace connects to one Notion sub-page. It scans the notes there (including images), points out gaps, quizzes you and grades your answers. Knowledge points and mastery belong to you, the user, not to any single workspace.

- **User home**: `~/.learnova/` holds global config and the database connection. Just as Claude Code remembers which projects you have opened, Learnova remembers which workspaces you have created.
- **Workspace**: maps to one Notion sub-page. It can live anywhere on disk. Workspaces need no parent–child directory relationship; they only need to be registered in the user home. Page structure is organized in Notion; the workspace directory holds only the note cache, images and reports.
- **Source**: an adapter that reads notes. Notion is the first one. A local folder or other note apps can be added later without changing the rest of the code.
- **Knowledge point**: the smallest testable unit, extracted from notes by the AI and owned by the user. One knowledge point can come from several notes across several workspaces. Deleting a workspace does not lose what you have mastered.
- **Data layer abstraction**: upper layers read and write data only through repository interfaces. SQLite now; switching to Postgres later is mostly a config change.
- **Human-driven**: Learnova only reads Notion and never writes back (one exception since V2.3: the one-time `web import`, which writes only under its own target page). Every action is triggered by you from the command line.

## Scope (version 1)

Commands fall into two groups: managing workspaces, and working inside one. A working command run inside a workspace directory uses that workspace. Run anywhere else, it uses the default set with `workspace use`, or the one named with `--workspace`.

| Command | What it does |
| --- | --- |
| `learnova workspace new <name> --notion <page-id>` | Create a workspace in the current directory (or the one given with `--path`) and register it in the user home |
| `learnova workspace list / use / remove` | List all workspaces; set the default; unregister one (does not touch Notion or delete your knowledge points) |
| `learnova sync` | Recursively read all sub-pages, convert them to Markdown in the cache, download images, and report which pages were added, changed or deleted |
| `learnova review` | For changed notes: turn new images into text, extract knowledge points, and flag a. factual errors, b. unclear or incomplete explanations, c. knowledge points the topic should cover but are missing. Writes a local Markdown report. Only flags problems; never fixes them |
| `learnova quiz` | Draw knowledge points by weighted random sampling and ask short-answer questions. Draws from **all workspaces** by default, or from one with `--workspace`. After you answer, the AI scores and comments, then mastery is updated |
| `learnova status` | Show mastery, last review time and the weakest knowledge points |
| `learnova check-latest <knowledge-point>` | Manual: search the web for recent changes to this knowledge point; results go into a report |
| `learnova ask` | Debug: ask the configured model one question to confirm model switching works |

Models are switched in config. Version 1 supports OpenAI, Claude, Gemini, DeepSeek and local models (Ollama). Mastered knowledge points get a lower weight but never zero, so they still come up now and then.

## Out of scope

Version 1 does none of the following, but the code leaves room so they can be added later without starting over.

- **No writing back to Notion, no automatic edits to notes**: only local reports; you make the edits yourself.
- **No review reminders**: questions are drawn by weight only when you run `quiz`; there are no scheduled pushes.
- **Short-answer questions only**: other question types are placeholders in the enum.
- **No other note apps**: the Source interface is defined, but Notion is the only implementation.
- **No real-time listening (webhooks)**: changes are found by comparison on each `sync`; see Key design.
- **No web UI, no HTTP API**: command line only. Core logic is kept separate from the CLI so an API can be layered on later.
- **No video, audio or PDF attachments**: version 1 understands images only; other attachments get a placeholder in the cache.
- **No multi-user support**: version 1 has one local user, but every table carries `user_id` so it can grow into multi-user with Postgres. Version 1 also implements only SQLite, not Postgres; it only guarantees the switch is possible.

## Tech stack

Everything is Python. LangChain is used only for its unified model interface (no agents, no chains) to keep the learning curve small.

| Part | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11+ | The language you know best |
| Package management | uv | One command to create the environment and install dependencies |
| Notion access | Official Python SDK `notion-client` | No hand-built HTTP requests |
| Model calls | LangChain `init_chat_model` | Switch models with one `"provider:model"` string; officially supports OpenAI, Anthropic, Google, DeepSeek and Ollama ([docs](https://docs.langchain.com/oss/python/concepts/providers-and-models.md)) |
| Image recognition | Each provider's multimodal models, through the same LangChain interface | The vision model is configured separately and can differ from the main model |
| Structured output | Pydantic + `with_structured_output` | The AI returns objects such as "list of knowledge points" or "grading result" directly; no text parsing |
| Data layer | SQLAlchemy 2.0 + Alembic; SQLite in version 1 | Switching to Postgres is mostly a change of database URL; Alembic manages schema changes |
| CLI | Typer | Plain functions become commands; supports subcommands such as `workspace new` |
| Config | `.env` for secrets, `~/.learnova/config.toml` for global settings, `learnova.toml` in each workspace | Secrets stay out of Git |
| Testing | pytest | At minimum, test the weighting algorithm, change detection and the repositories |
| Future API | FastAPI | Calls the same core code as the CLI |

Is the model API format universal? Only partly. DeepSeek, Ollama and Gemini offer OpenAI-compatible endpoints, but Claude's and Gemini's native APIs differ, and structured output and web search are written differently for each provider. LangChain hides these differences.

## Key design

The project is split into four layers: entry (CLI, later HTTP API) → services (review, quiz, status, check-latest) → core (sync, weights, llm, storage) → external (NotionSource, later FolderSource; model providers). Upper layers only call lower ones. The eight points below decide how well the project can grow.

The CLI and the future API call the same services. Services know only the Source interface, the model factory and the repositories, never Notion itself.

### 1. Source abstraction

Each Source implements two methods:

- `list_notes()`: returns summaries of all notes: ID, title, hierarchy path, last-edited time.
- `fetch(note_id)`: returns one note's full Markdown.

Notion stores a page as a tree of blocks, so NotionSource needs a small "blocks to Markdown" function. Adding a local folder later just means writing a FolderSource.

### 2. Change detection

Notion can send notifications: its [webhooks](https://developers.notion.com/reference/webhooks-events-delivery) fire when pages are created, changed, deleted or moved (`page.created`, `page.content_updated`, `page.deleted`, `page.moved`). But webhooks need a publicly reachable HTTPS server, and content-change events are batched and may arrive a minute or two late. A CLI that runs only on your machine cannot use them.

So version 1 compares instead. On each `sync`, every note's last-edited time and content hash are compared with the database to find additions, changes and deletions. This relies only on the edit time a Source returns, so it works for a local folder too. Once an API server exists, webhooks can be wired in as a "run sync now" signal.

### 3. Knowledge points and mastery

The database has 8 tables. Knowledge points, attempts and mastery belong to the user; notes and images belong to a workspace.

| Table | Contents |
| --- | --- |
| `users` | Users; version 1 has a single row |
| `workspaces` | Workspace name, Notion page ID, disk path, owner, whether it is the default |
| `notes` | Workspace, Notion ID, title, hierarchy path, last-edited time, content hash |
| `note_images` | Owning note, local file path, image hash, text description from the vision model |
| `knowledge_points` | Owner, name, one-line summary, importance (1–5; the AI sets the initial value, you can change it) |
| `kp_sources` | Which notes a knowledge point comes from; one point can map to many notes across workspaces |
| `attempts` | Each answer: question, question type, your answer, score, AI feedback, time |
| `mastery` | Per knowledge point: mastery (0–1), review count, last review time |

Question type is an enum: `SHORT_ANSWER` (the only one in version 1), `MULTIPLE_CHOICE`, `TRUE_FALSE`, `EXPLAIN`, `CODE`.

Answers are scored 0–5 with one decimal, anchored to the levels in the [grading rubric](grading-rubric.md), which the grading prompt loads at runtime.

### 4. Weighting algorithm (separate module)

The weighting algorithm lives in `scheduler/weights.py` as a pure function that touches neither the database nor a model. It takes one knowledge point's state and the current time and returns a weight, which makes it easy to test and easy to replace. Version 1 formula:

```latex
w = I \times \max(1 - m,\ \varepsilon) \times \left(1 + \ln(1 + d)\right)
```

I is importance (1–5), m is mastery (0–1), d is days since the last review, and ε is a floor (default 0.1). Because of ε, mastered points still get drawn; points not reviewed for a long time slowly gain weight. Questions are drawn by weighted random sampling. The coefficients live in the user home's `config.toml` and can be tuned.

### 5. Checking for the latest knowledge

`check-latest` goes online only when you run it. It queries a search service for recent material on the knowledge point, then has the model compare that with your notes and write up what has changed, with source links. Results go only into the report: no note edits, no mastery changes. The search service is also a swappable interface.

### 6. User home and workspaces

`~/.learnova/` is the user home. It holds only global config and, when using SQLite, the database file. Workspaces are registered in the database's `workspaces` table with their disk paths, so a workspace can live anywhere.

A command first looks for `learnova.toml` in the current directory or its parents. If it finds one, it uses that workspace; otherwise it uses the default. This is the same way Git finds a repository from a subdirectory. If a workspace directory is moved or deleted, `workspace list` marks it "not found" but does not delete your knowledge points.

### 7. Data layer abstraction

Upper layers never write SQL. They call repository interfaces such as `WorkspaceRepository`, `NoteRepository`, `KnowledgeRepository` and `AttemptRepository`. Version 1 implements them with SQLAlchemy 2.0, with the database address given as a URL: `sqlite:///...` now, `postgresql://...` later. Every schema change is an Alembic migration; tables are never altered by hand.

SQLite and Postgres differ in types and concurrency, so from version 1 on, avoid SQLite-only patterns: store all times as timezone-aware UTC and use UUIDs for IDs.

### 8. Image recognition

Image links returned by Notion are temporary and [expire after one hour](https://developers.notion.com/reference/file-object). So `sync` downloads images into the workspace's `images/` directory, names them by content hash, and records them in `note_images`.

Recognition has two steps. First, a vision model writes a text description of each new image, turning formulas, charts and code screenshots into text; descriptions are cached by hash, so each image is recognized only once. Then the description is inserted back into the note's Markdown and passed along with the text to review and question generation. Later steps never need to care whether a model can see images. When an image is the point itself (an architecture diagram, say), the original can also be sent directly to a multimodal model.

All five providers can read images. OpenAI, Claude and Gemini always could; DeepSeek has supported images in its `deepseek-flash` model since September 2026 ([docs](https://api-docs.deepseek.com/guides/vision)); for local Ollama, pick a vision-capable model.

## Directory structure

Code, user home and workspaces live in three separate places. Workspaces have no parent–child directory relationship with each other.

```
learnova/                          # code repository
├── pyproject.toml
├── alembic.ini
├── .env.example                   # template for provider API keys; the real .env stays out of Git
├── README.md
├── CLAUDE.md                      # project notes for the AI
├── src/learnova/
│   ├── cli/
│   │   ├── main.py                # Typer entry point; input and output only
│   │   └── workspace.py           # workspace new / list / use / remove
│   ├── config.py                  # reads .env, config.toml, learnova.toml
│   ├── home.py                    # user home: find ~/.learnova, locate the current workspace
│   ├── models.py                  # Pydantic models: Note, KnowledgePoint, QuestionType...
│   ├── sources/
│   │   ├── base.py                # NoteSource interface
│   │   └── notion.py              # NotionSource + blocks to Markdown + image download
│   ├── sync.py                    # change detection
│   ├── llm/
│   │   ├── factory.py             # wraps init_chat_model; main model vs. vision model
│   │   └── prompts/               # review.md, question.md, grade.md, image.md
│   ├── services/
│   │   ├── images.py              # image -> text description (cached by hash)
│   │   ├── review.py              # gap review
│   │   ├── quiz.py                # questions, grading, mastery updates
│   │   └── latest.py              # latest-knowledge check
│   ├── scheduler/
│   │   └── weights.py             # weighting algorithm (pure function)
│   ├── storage/
│   │   ├── repositories.py        # repository interfaces (the only thing upper layers know)
│   │   ├── sqlalchemy_repos.py    # SQLAlchemy implementation
│   │   ├── tables.py              # table definitions
│   │   └── migrations/            # Alembic migrations
│   └── report.py                  # Markdown report generation
└── tests/
    ├── test_weights.py
    ├── test_sync.py
    └── test_repositories.py

~/.learnova/                       # user home
├── config.toml                    # database URL, default model, vision model, weight coefficients
└── learnova.db                    # SQLite: workspace registry, knowledge points, mastery

anywhere/ml-notes/                 # one workspace (maps to one Notion sub-page)
├── learnova.toml                  # Notion page ID; also marks "this is a workspace"
├── cache/                         # Markdown cache of each note
├── images/                        # downloaded images, named by hash
└── reports/                       # e.g. 2026-11-24-review.md
```

## Milestones

Ten steps. Each ends with something you can run and see. Once it works, `git commit`, then start the next one.

1. **Connect to Notion**: set up the project skeleton; `learnova ping` lists the titles of all sub-pages under your page. Common pitfall: after creating the integration in Notion, you must also add it under the target page's Connections, or the API sees no pages.
2. **User home and data layer**: create `~/.learnova/`, the repository interfaces and the first Alembic migration; `workspace new / list / use` work. Verify by creating one workspace in each of two unrelated directories.
3. **Read content**: the first `sync` converts each note to Markdown in the cache and fills the `notes` table. This step also shows what structure your notes actually have.
4. **Change detection**: edit, add and delete a note in Notion, run `sync` again, and it reports all three correctly.
5. **Connect models**: `learnova ask "hello"` switches among the five providers. Test whichever providers you have keys for first.
6. **Images**: `sync` downloads images; the vision model writes descriptions that are inserted back into the Markdown. Verify on a note with formulas or charts.
7. **Gap review**: `review` extracts knowledge points and produces the first report. This step is mostly prompt tuning.
8. **Weighting algorithm**: write `weights.py` and its tests first, without touching the database or models.
9. **Quiz and grading**: `quiz` + `status`; run the full loop "draw → answer → grade → update mastery" and confirm questions are drawn across workspaces.
10. **Latest knowledge**: `check-latest`. If the first nine steps were tiring, push this to version 2.

Version 2 candidates: FastAPI, Postgres, FolderSource, other question types, webhooks.

## Open questions

- [ ] Are the notes nested sub-pages or a Notion database? Confirm after step 3.
- [ ] Should the same knowledge point in different workspaces (e.g. "gradient descent" in both) be merged automatically? Suggested: no automatic merging in version 1; the report flags "may duplicate an existing knowledge point" and you confirm.
- [ ] Should `review` look only at changed notes (cheaper), or also allow a full re-review? Suggested: changed notes by default, plus an `--all` flag.
- [x] Score on a 0–5 or a 0–100 scale? 0–5 with one decimal; see the [grading rubric](grading-rubric.md).
- [ ] Which provider is the default vision model? With many images, a cheaper model saves a lot.
- [ ] Which search service for `check-latest`? A dedicated search API (such as Tavily), or the model's built-in web search, which is written differently for each provider.
- [ ] Which providers do you currently have API keys for? This decides what step 5 tests first.

## Version 2

2026-11-25 · Version 1 (milestones 1–9) is complete; `check-latest` moved here from version 1.

Version 2 is a minimal step up: more question types, then an HTTP API and a web frontend on top of
the same services, then `check-latest`. Each item below is its own milestone, with a plan
approved before it is built.

1. **Multiple choice and true/false questions.** The model writes the question, the options and
   the correct answer; grading is local, with no model call. Both count less toward mastery than a
   short answer, true/false least (guessing gets 50%); the weights are in `config.toml`. Each
   attempt stores the options shown and the correct answer. Explain, code and mixed sessions wait.
2. **HTTP API (FastAPI).** For personal use only: bound to 127.0.0.1, no authentication. Endpoints
   call the existing services and add no logic of their own. A quiz takes two requests (get a
   question, then submit an answer), so issued questions are stored until they are answered. Long
   operations such as `sync` and `review` run synchronously for now.
3. **Web frontend (React + Vite + TypeScript).** Talks only to the API. Pages: take a quiz, view
   status and mastery, read reports. The pages are planned before they are built.
4. **`check-latest`**, after the frontend.

Deferred beyond this version: the local-folder source, local models (Ollama), the explain and code
question types and mixed sessions, Postgres, webhooks.

## Version 2.1

2026-11-25 · Version 2 ended at V2.3; `check-latest` is deferred to a later version.

1. **`.learnovaignore`.** A file in the workspace folder lists sub-pages to leave out, one pattern
   per line: a page title (any depth, with `*`/`?`), a path of titles from the top page
   (`Database / Old notes`, `**` for any depth), or a Notion page ID or URL; `!` brings a page
   back and the last matching line wins; matching ignores case. Ignored pages and everything under
   them are not read from Notion. Pages that were already synced are kept in the database but
   hidden from review, quiz and status; removing the line restores them, links to knowledge
   points included. `workspace new` writes a commented template.

## Version 2.2

2026-11-26 · Version 2.2 turns review results into a list of issues that open and close, gives
knowledge points a hierarchy, makes the knowledge base the first page of the web app, adds
quizzes on chosen points or topics, and can post issues to Notion as comments. The earlier 2.2
items (bookmarks, manual questions, guided setup) move to Version 2.4; quiz by knowledge point
stays here as milestone 5.

Each milestone below is its own step, with a plan approved before it is built.

### 1. Issues in the database, and a new report

Today issues exist only inside each report file, so the same issue comes back on every run and
there is no way to see whether it was fixed.

- **Table `review_issues`** (migration 0012): user, workspace, note, kind
  (`factual_error` / `unclear` / `missing`), quote (empty for `missing`), problem (for `missing`:
  topic and why), linked knowledge point (nullable), status (`open` / `fixed` / `dismissed` /
  `obsolete`), fingerprint, first seen, last seen, closed time, closed by (`review` / `user` /
  `notion`).
- **Lifecycle.** When a note is reviewed again, its open and dismissed issues are given to the
  model, each with a label. For every open issue the model returns `still_present` or `fixed`;
  fixed ones are closed. New issues must not repeat an open or dismissed one. As a safety net the
  code also drops a new issue whose fingerprint (kind + normalized quote, or kind + normalized
  topic for `missing`) matches an open or dismissed issue in the same note. Issues of a deleted
  note become `obsolete`; issues of an ignored note are hidden like the rest of its data.
- **Link to knowledge points.** Each issue in the review output names the label of the knowledge
  point it is about (existing or new), or none.
- **Priority** (`scheduler/priority.py`, a pure function like `weights.py`):
  `priority = kind_weight × importance × (1 + (1 − mastery))`. Unlinked issues use importance 3;
  a point never quizzed counts as mastery 0.5. Defaults in `config.toml` `[issues]`:
  `factual_error = 3`, `unclear = 2`, `missing = 1`.
- **CLI.** `learnova issues [--workspace] [--kind] [--status]` lists issues by priority (open by
  default). `learnova issues fix|dismiss|reopen <id>` changes status by hand; ids are short ids,
  as with `kp`.
- **Report.** Still written to `reports/<date>-review.md` after each `review`, now rendered from
  the database:
  1. A summary line: new, fixed this run, still open; knowledge points new / updated / removed.
  2. "Fix first": the top 5 open issues by priority, with importance and mastery shown.
  3. "Fixed this run".
  4. "Other open issues", grouped by note.
  5. "Possible duplicates" with the `kp merge` command, as today.
  The per-note knowledge-point table is removed (the knowledge base page shows points). Old
  report files are left as they are.

Done when: a second `review` of an unchanged note adds no issues; editing a note to fix an issue
and reviewing it closes that issue and lists it under "Fixed this run"; a dismissed issue is not
raised again; `issues` lists open issues in priority order; `priority.py` has unit tests.

### 2. Block anchors

- `sync` records the blocks of each note in a new table `note_blocks` (migration 0013): note,
  Notion block ID, position, plain text, text hash.
- When an issue is created, its quote is matched to a block (normalized substring first, then the
  closest block above a similarity threshold); no match leaves the block empty.
- Reports and the web app link each issue to its block
  (`https://www.notion.so/<page-id>#<block-id>`, IDs without dashes), or to the page when there
  is no block.

Done when: clicking an issue link opens Notion at the quoted paragraph for the usual block types
(paragraph, list item, heading, quote, callout, toggle); an unmatched quote links to the page.

### 3. Knowledge hierarchy

- **Table `kp_parents`** (migration 0014): child, parent, is_primary. Every point has at most one
  primary parent (where it "lives"; points without one are at the top level) and at most 2
  secondary parents ("shortcuts"). No cycles; depth at most 4. The code enforces all of this.
- **Topics.** A point with children is a topic. A topic's mastery is not stored: it is the
  importance-weighted average of the quizzed points in its subtree (shortcuts included), shown
  with "n of m quizzed". Topics are not drawn by `quiz` in this version.
- **During `review`.** For each new point the model may pick a primary parent from the user's
  existing topics and the note's other points ("X is a kind of Y"). It does not create topics or
  shortcuts here.
- **`learnova kp organize [--workspace]`.** Sends every point (name, summary, current parents) to
  the model, which proposes new topics, primary-parent moves, and shortcuts to add or remove
  ("X is used in / part of Y"). The proposal is written to `reports/<date>-organize.md` and
  printed; you confirm before anything changes (`--yes` skips the prompt, `--dry-run` only
  writes the proposal).
- **By hand.** `learnova kp move <point> --under <parent>` (or `--top`),
  `learnova kp link <point> --also-under <parent>`, `learnova kp unlink <point> --from <parent>`.
- **Merge.** `kp merge` moves the merged point's children to the target and keeps the target's
  primary parent; shortcuts are combined, up to the limit.

Done when: `kp organize` on the existing points produces a readable proposal and applies it
only after confirmation; moves that would make a cycle, exceed depth 4 or add a third shortcut
are refused with a clear message; topic mastery matches a hand calculation in tests; merging a
topic keeps its children.

### 4. Knowledge base as the first page; Issues page

- **API.** `GET /points/tree?workspace=` (every point with primary parent, shortcuts, mastery,
  topic mastery, open-issue count); `GET /points/{id}` (summary, all paths, source notes with
  Notion links, last 20 attempts, issues); `PATCH /points/{id}` extended with primary parent and
  shortcuts; `GET /issues?workspace=&status=&kind=`; `PATCH /issues/{id}` (status).
- **Navigation.** Knowledge · Quiz · Issues. `/` opens Knowledge. Status is merged into Knowledge:
  its summary numbers become the stat strip, "weakest" becomes sorting by mastery, and `/status`
  redirects to Knowledge.
- **Knowledge page.**
  - Stat strip: points, average mastery, open issues, last quiz.
  - Search box: filters the tree as you type, keeping matches and their parent path; each result
    shows its primary path and "also in: …".
  - Tree view (default): folders expand and collapse; shortcuts show a link icon and jump to the
    primary location; each row has mastery, importance and an open-issue badge. A flat view
    sorts by mastery, importance, last quiz or issue count.
  - Detail panel: summary, every location, source notes, attempts with a score trend, open
    issues, importance editing, and "Move to…" / "Add shortcut…".
- **Issues page** (replaces Reports). Open issues by priority with filters (workspace, kind,
  status), a Notion link and Fix / Dismiss / Reopen buttons; below that, past reports and the
  Sync and Review buttons.

Done when: the app opens on Knowledge; a point can be found by search and opened from a
shortcut; importance and parents can be changed from the detail panel; an issue can be dismissed
from the Issues page and stays dismissed after a new review; `npm test`, `npm run typecheck` and
`npm run build` pass.

### 5. Quiz on chosen points or a topic

- `learnova quiz --point <ref> [--point <ref> …]` or `--topic <ref>`. A topic expands to every
  point in its subtree, shortcuts included. Points are drawn by the usual weights, but only from
  the selection.
- API: starting a quiz accepts point IDs. Web: checkboxes in the Knowledge tree with "Practice
  selected", and "Practice this topic" on topic rows; the Quiz page shows the current scope.

Done when: a quiz on one topic only asks about points under it; a quiz on two chosen points only
asks about those two; mastery updates as in a normal quiz.

### 6. Issues as Notion comments (optional, off by default)

- `config.toml` `[notion_comments] enabled = false`. When on, the Notion integration needs "Read
  comments" and "Insert comments". This is another exception to "Learnova only reads Notion": it
  adds and deletes its own comments and never edits note text.
- After `review`, each new open issue gets one comment: on its block when it has one, otherwise
  on the page. Text: `Learnova · <kind>: <problem> (<short id>)`. The issue stores the comment
  ID, discussion ID and the block's text hash at that time. The public API cannot anchor a
  comment to selected text or resolve a discussion.
- **Reconcile** on every `sync`, for notes with commented issues (the API lists only open
  comments):

  | Comment | Block text | Result |
  |---|---|---|
  | still open | unchanged | nothing; no new comment |
  | still open | changed | next `review` decides; if fixed, close the issue and delete the comment |
  | gone (resolved or deleted) | changed | next `review` decides: `fixed`, otherwise `dismissed` |
  | gone | unchanged | `dismissed`, closed by `notion`; never raised again |

- Your replies in the discussion are saved on the issue and given to the model at the next
  review as context; they do not change the status by themselves.
- Turning the option off stops new comments. `learnova issues clear-comments` deletes all of
  Learnova's own comments. Requests are rate-limited.

Done when: a review with the option on posts one comment per new issue and none on a second run;
resolving a comment in Notion without editing makes the issue `dismissed` after `sync`; fixing
the text and reviewing deletes the comment and marks the issue `fixed`; with the option off
nothing is written to Notion.

Open questions for Version 2.2:

1. Topic questions: should `quiz` ever ask a broader question about a topic itself, or only about
   its points? (Draft: only its points.)
2. `kp organize` on many points: above a few hundred, send points in batches per workspace or per
   top-level topic?

## Version 2.3

2026-11-26 · Version 2.3 adds `learnova web`: a web tutorial is imported into Notion **once**, and
later changes on the site are compared with a local cache and reported, never written to Notion.
The first source is the GeeksforGeeks AI tutorial
(`https://www.geeksforgeeks.org/artificial-intelligence/artificial-intelligence/`).

`web import` is the one exception to "Learnova only reads Notion": it writes only under the page
named with `--into`, and only until the import is complete.

1. **`learnova web import <index-url> --into <notion-page> [--limit N]`.** The `--into` page is
   laid out like the index page: its introduction, h2/h3 headings and section descriptions, with
   each listed article as a Notion sub-page in the place of its link. An article page is titled
   with the article's own title and holds a source link, then the original text; no template or
   extra blocks: you write your notes on it. Links inside articles are not followed. The same text
   is saved as Markdown in `<user home>/web/<slug>/cache/`. Notion adds a sub-page at the end of
   its parent, so the layout is written strictly in order and the run stops at the first error;
   running it again continues from that step (also after `--limit`), and nothing is written
   twice. If the index changes while an import is unfinished, it is refused (start over). Once
   everything is in, the import is complete and `web import` refuses it. Requests to the site are
   rate-limited (`config.toml` `[import_web] min_interval_seconds`, default 2). The Notion
   integration needs "Insert content" on the target page, and "Update content" to redo a page
   whose import was cut off. `learnova web forget <index-url>` drops an import's records and
   cache (reports are kept) so it can be imported again; the Notion pages are deleted by hand.
2. **`learnova web check [index-url]`.** Fetches the index and each imported article (with ETag;
   skipped when `article:modified_time` or the text is unchanged) and compares it with the cache,
   section by section. Changes go into `<user home>/web/<slug>/reports/<date>-check.md`: for each
   changed article a link to its Notion page and the original, which sections were added, changed
   or removed, and the changed lines as a diff; articles new in the index (listed, not imported);
   articles removed from it (their Notion pages are left alone). You decide what goes into your
   notes. After the report the cache holds the new version, so each change is reported once. No
   report is written when nothing changed.
3. **State.** `web_imports` (index URL, target page, folder slug, layout progress, completed time)
   and `web_articles` (URL, section, title, state imported/new/removed, Notion page ID, modified
   time, ETag, hash of the cached Markdown, cache path, times), migrations 0010 and 0011.

Done when: an import lays out the `--into` page like the index with one page per article and
fills the cache; a
second `web import` is refused; `web check` with no changes writes nothing anywhere but the
check times; a changed article shows up once in a report with its diff, and the cache is updated;
new and removed articles are listed once; Notion is never written by `web check`.

## Version 2.4 (not started)

2026-11-26 · Moved here from the earlier Version 2.2. Planned after Version 2.2.

1. **Bookmark questions.** Save questions to come back to; bookmarked questions show in the
   knowledge point's detail panel.
2. **Add questions by hand.** Write your own question for a knowledge point, from its detail
   panel; it joins that point's question pool.
3. **Guided workspace setup.** A step-by-step flow: add the Notion link, create
   `.learnovaignore`, configure the workspace.
