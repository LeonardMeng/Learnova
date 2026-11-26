import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const row = (id: string, name: string, extra: object = {}) => ({
  id, name, summary: `About ${name}.`, importance: 3, level: 0, parent_id: null, shortcuts: [] as string[],
  path: [name], topic: null, mastery: null, review_count: 0, last_reviewed: null, open_issues: 0,
  sources: [{ workspace: "interview", path: ["Interview", "Redis"] }], ...extra,
});
// Redis › Eviction, and Eviction is also under Caching (a shortcut).
const tree = [
  row("r", "Redis", { importance: 5, topic: { mastery: 0.5, quizzed: 1, total: 1 } }),
  row("e", "Eviction", { level: 1, parent_id: "r", path: ["Redis", "Eviction"], shortcuts: ["c"], mastery: 0.5, open_issues: 2 }),
  row("c", "Caching", { importance: 2, topic: { mastery: 0.5, quizzed: 1, total: 1 } }),
];
const detail = (id: string, extra: object = {}) => {
  const r = tree.find((t) => t.id === id)!;
  return {
    id, name: r.name, summary: r.summary, importance: r.importance, sources: r.sources, mastery: r.mastery,
    review_count: 1, last_reviewed: "2026-11-25T03:00:00Z", parent_id: r.parent_id,
    shortcuts: id === "e" ? [{ id: "c", name: "Caching" }] : [],
    paths: id === "e" ? [["Redis", "Eviction"], ["Caching", "Eviction"]] : [[r.name]],
    topic: r.topic, children: [], notes: [{ workspace: "interview", path: ["Interview", "Redis"], notion_url: "https://www.notion.so/x" }],
    attempts: [
      { question: "Q2", type: "short_answer", answer: "a", score: 4, feedback: "", created_at: "2026-11-25T04:00:00Z" },
      { question: "Q1", type: "short_answer", answer: "a", score: 2, feedback: "", created_at: "2026-11-25T03:00:00Z" },
    ],
    issues: [], ...extra,
  };
};
const routes = (extra: object = {}) => ({
  "GET /api/workspaces": [{ name: "interview", path: "/w", notion_page_id: "p", is_default: true, found: true }],
  "GET /api/me": { name: "local" },
  "GET /api/status": { overall: { name: "All", points: 3, quizzed: 1, average_mastery: 0.5, last_quiz: null }, workspaces: [], weakest: [] },
  "GET /api/issues": [],
  "GET /api/points/tree": tree,
  "GET /api/points/e": detail("e"),
  "GET /api/points/r": detail("r"),
  ...extra,
});

describe("Knowledge page", () => {
  it("opens first, with the stat strip, the tree, the sidebar and a tab", async () => {
    mockApi(routes());
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Knowledge" })).toBeInTheDocument();
    const t = await screen.findByRole("list", { name: "Knowledge tree" });
    expect(within(t).getByRole("button", { name: "Redis" })).toBeInTheDocument();
    expect(within(t).getByRole("button", { name: "Caching" })).toBeInTheDocument();
    expect(screen.getByText("Points").nextSibling).toHaveTextContent("3");
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("local")).toBeInTheDocument();
  });

  it("finds a point by search and opens it from a shortcut", async () => {
    mockApi(routes());
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("list", { name: "Knowledge tree" });

    await user.type(screen.getByRole("searchbox", { name: "Search points" }), "evic");
    const results = await screen.findByRole("list", { name: "Search results" });
    expect(results).toHaveTextContent("Eviction");
    expect(results).toHaveTextContent("Redis");
    expect(results).toHaveTextContent("also in: Caching");
    await user.click(within(results).getByRole("button", { name: "Eviction" }));
    expect(await screen.findByRole("article", { name: "Details of Eviction" })).toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: "Search points" }));
    const t = await screen.findByRole("list", { name: "Knowledge tree" });
    // Caching is open (top level): its shortcut to Eviction jumps to Eviction's own place.
    await user.click(within(t).getByRole("button", { name: "Close Redis" }));
    await user.click(within(t).getByRole("button", { name: "↗ Eviction" }));
    expect(await screen.findByRole("article", { name: "Details of Eviction" })).toBeInTheDocument();
    expect(within(t).getByRole("button", { name: "Close Redis" })).toBeInTheDocument(); // opened again
    const panel = screen.getByRole("article", { name: "Details of Eviction" });
    expect(panel).toHaveTextContent("Caching (shortcut)");
    expect(within(panel).getByRole("img", { name: "Score trend" })).toBeInTheDocument();
  });

  it("changes importance and parent from the detail panel, and shows refusals", async () => {
    const calls = mockApi(
      routes({
        "PATCH /api/points/e": (body: { parent_id?: string | null }) =>
          body.parent_id === "c"
            ? { status: 409, json: { error: "That would make a loop.", code: "HierarchyError" } }
            : { json: detail("e", { importance: 5, ...body }) },
      }),
    );
    const user = userEvent.setup();
    renderApp("/?point=e");
    const panel = await screen.findByRole("article", { name: "Details of Eviction" });

    await user.selectOptions(within(panel).getByLabelText("Importance"), "5");
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ importance: 5 });

    await user.selectOptions(within(panel).getByLabelText("Move to"), "__top");
    await user.click(within(panel).getByRole("button", { name: "Move" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1].body).toEqual({ parent_id: null });

    await user.selectOptions(within(panel).getByLabelText("Move to"), "c");
    await user.click(within(panel).getByRole("button", { name: "Move" }));
    expect(await within(panel).findByRole("alert")).toHaveTextContent("That would make a loop.");

    await user.click(within(panel).getByRole("button", { name: "remove" })); // the Caching shortcut
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(4));
    expect(calls.filter((c) => c.method === "PATCH")[3].body).toEqual({ shortcuts: [] });
  });

  it("redirects /status to the Knowledge page", async () => {
    mockApi(routes());
    renderApp("/status");
    expect(await screen.findByRole("heading", { name: "Knowledge" })).toBeInTheDocument();
  });

  it("closes tabs", async () => {
    mockApi(routes({ "GET /api/web-imports": [] }));
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Knowledge" });
    await user.click(screen.getByRole("link", { name: "Web imports" }));
    expect(await screen.findByRole("heading", { name: "Web imports" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Web imports"]);
    await user.click(screen.getByRole("button", { name: "Close Web imports" }));
    expect(await screen.findByRole("heading", { name: "Knowledge" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview"]);
  });
});

describe("Practice chosen points or a topic", () => {
  const question = (point: string, scope: string) => ({
    id: `q-${point}`, type: "short", text: "Explain?", options: null, source: "interview: Interview / Redis",
    point_id: point, status: "open", created_at: "2026-11-25T05:00:00Z", scope,
  });

  it("starts a quiz on the checked points", async () => {
    const calls = mockApi(routes({ "POST /api/quiz/questions": () => ({ status: 201, json: question("e", "2 chosen points") }) }));
    const user = userEvent.setup();
    renderApp("/");
    const t = await screen.findByRole("list", { name: "Knowledge tree" });
    await user.click(within(t).getByRole("checkbox", { name: "Choose Eviction" }));
    await user.click(within(t).getByRole("checkbox", { name: "Choose Caching" }));
    await user.click(screen.getByRole("button", { name: "Practice selected (2)" }));

    expect(await screen.findByText(/Only: Eviction, Caching/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Workspace")).toBeNull(); // a selection has no workspace
    await user.click(screen.getByRole("button", { name: "Start quiz" }));
    expect(await screen.findByText("Only: 2 chosen points")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({ points: ["e", "c"], topic: null, workspace: null, exclude: [] });
  });

  it("practises a topic from the detail panel, and can go back to everything", async () => {
    const calls = mockApi(routes({ "POST /api/quiz/questions": () => ({ status: 201, json: question("e", "topic Redis (1 point(s))") }) }));
    const user = userEvent.setup();
    renderApp("/?point=r");
    const panel = await screen.findByRole("article", { name: "Details of Redis" });
    await user.click(within(panel).getByRole("button", { name: "Practice this topic" }));
    expect(await screen.findByText(/Only: topic Redis/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start quiz" }));
    expect(await screen.findByText("Only: topic Redis (1 point(s))")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ topic: "r", points: [] });
  });

  it("offers practising everything instead", async () => {
    mockApi(routes());
    renderApp("/quiz?topic=r");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Practice everything instead" }));
    expect(await screen.findByLabelText("Workspace")).toBeInTheDocument();
  });
});
