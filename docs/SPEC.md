# Learnova · Project Spec v0.1

2026-11-24

## Goals and core concepts

Version 1 is a Python command-line tool called Learnova: an AI teaching assistant for your study notes. It works like a harness that goes wherever you go. You can run it from any directory, and it manages the multiple workspaces you create. Each workspace connects to one Notion sub-page. It scans the notes there (including images), points out gaps, quizzes you and grades your answers. Knowledge points and mastery belong to you, the user, not to any single workspace.

- **User home**: `~/.learnova/` holds global config and the database connection. Just as Claude Code remembers which projects you have opened, Learnova remembers which workspaces you have created.
- **Workspace**: maps to one Notion sub-page. It can live anywhere on disk. Workspaces need no parent–child directory relationship; they only need to be registered in the user home. Page structure is organized in Notion; the workspace directory holds only the note cache, images and reports.
- **Source**: an adapter that reads notes. Notion is the first one. A local folder or other note apps can be added later without changing the rest of the code.
- **Knowledge point**: the smallest testable unit, extracted from notes by the AI and owned by the user. One knowledge point can come from several notes across several workspaces. Deleting a workspace does not lose what you have mastered.
- **Data layer abstraction**: upper layers read and write data only through repository interfaces. SQLite now; switching to Postgres later is mostly a config change.
- **Human-driven**: Learnova only reads Notion and never writes back. Every action is triggered by you from the command line.

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
- [ ] Score on a 0–5 or a 0–100 scale?
- [ ] Which provider is the default vision model? With many images, a cheaper model saves a lot.
- [ ] Which search service for `check-latest`? A dedicated search API (such as Tavily), or the model's built-in web search, which is written differently for each provider.
- [ ] Which providers do you currently have API keys for? This decides what step 5 tests first.
