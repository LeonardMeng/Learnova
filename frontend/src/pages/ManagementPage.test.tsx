import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const workspaces = [
  { name: "ml", path: "/ml", notion_page_id: "p", is_default: false, found: true },
  { name: "interview", path: "/w", notion_page_id: "p", is_default: true, found: true },
];
const markdown = "# Review: interview\n\n| | Point |\n|---|---|\n| new | **RDB** |\n";
const PAGE = "3e6135e3-ecf0-4389-846c-e4126df126b0";

describe("Management page", () => {
  it("is in the sidebar as Management, and /sync redirects to it", async () => {
    mockApi({ "GET /api/workspaces": workspaces });
    renderApp("/sync");
    expect(await screen.findByRole("heading", { name: "Management" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Management" })).toHaveAttribute("href", "/management");
    expect(screen.queryByText("Sync & review")).toBeNull();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Management"]);
  });

  it("has no page-wide toolbar: each workspace row has its own Sync and Review", async () => {
    mockApi({ "GET /api/workspaces": workspaces });
    renderApp("/management");
    const ml = await screen.findByRole("row", { name: "Workspace ml" });
    expect(screen.queryByRole("button", { name: "Sync from Notion" })).toBeNull();
    expect(screen.queryByText("re-download all")).toBeNull();
    expect(screen.queryByLabelText("Workspace")).toBeNull(); // no picker
    expect(within(ml).getByRole("button", { name: "Sync" })).toBeInTheDocument();
    expect(within(ml).getByRole("button", { name: "Review" })).toBeInTheDocument();
  });

  it("syncs one workspace and shows the summary under its row", async () => {
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/workspaces/interview/sync": {
        changes: [{ kind: "ignored", path: ["Interview", "Behaviour"], previous_path: null }],
        counts: { added: 0, changed: 0, "renamed/moved": 0, deleted: 0, ignored: 1, restored: 0, unchanged: 3 },
        notes_downloaded: 0, images_downloaded: 0, image_errors: [], block_types: {}, skipped_databases: [],
        images_described: 0, images_reused: 0, describe_errors: [], pending_images: 0, not_described_because: null,
        ignored: [["Interview", "Behaviour"]],
      },
    });
    const user = userEvent.setup();
    renderApp("/management");
    const row = await screen.findByRole("row", { name: "Workspace interview" });
    await user.click(within(row).getByRole("button", { name: "Sync" }));
    expect(await screen.findByText(/1 ignored,/)).toBeInTheDocument();
    expect(screen.getByText("Ignored by .learnovaignore: Behaviour")).toBeInTheDocument();
    // The result row comes right after the interview row.
    expect((row.nextElementSibling as HTMLElement).textContent).toContain("Synced.");
    expect(calls.find((c) => c.method === "POST")).toMatchObject({
      path: "/api/workspaces/interview/sync", body: { full: false, describe: true },
    });
  });

  it("reviews one workspace and links to its report", async () => {
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/workspaces/ml/review": {
        nothing_to_review: false, reviewed: 1, skipped: 0, failed: [], new_points: 2, issues: 1,
        missing_topics: 0, fixed: 1, open: 3, possible_duplicates: 0, report: "2026-11-25-review-2.md", model: "m",
        pending_images: 0,
      },
    });
    const user = userEvent.setup();
    renderApp("/management");
    const row = await screen.findByRole("row", { name: "Workspace ml" });
    await user.click(within(row).getByRole("button", { name: "Review" }));
    expect(await screen.findByText(/Reviewed 1 note/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the report" })).toHaveAttribute(
      "href", "/reports?workspace=ml&report=2026-11-25-review-2.md",
    );
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ all: false });
  });

  it("offers re-download all, review all and make default under More", async () => {
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/workspaces/ml/sync": {
        changes: [], counts: { unchanged: 2 }, notes_downloaded: 2, images_downloaded: 0, image_errors: [],
        block_types: {}, skipped_databases: [], images_described: 0, images_reused: 0, describe_errors: [],
        pending_images: 0, not_described_because: null, ignored: [],
      },
      "POST /api/workspaces/ml/review": { nothing_to_review: true },
      "PUT /api/workspaces/ml/default": { ...workspaces[0], is_default: true },
    });
    const user = userEvent.setup();
    renderApp("/management");
    const row = await screen.findByRole("row", { name: "Workspace ml" });
    await user.click(within(row).getByText("More ▾"));
    await user.click(within(row).getByRole("button", { name: "Re-download all" }));
    expect(await screen.findByText(/Synced\./)).toBeInTheDocument();
    expect(row.querySelector("details")?.open).toBe(false); // the menu closes after a choice
    await user.click(within(row).getByText("More ▾"));
    await user.click(within(row).getByRole("button", { name: "Review all notes" }));
    expect(await screen.findByText(/Nothing to review/)).toBeInTheDocument();
    await user.click(within(row).getByText("More ▾"));
    await user.click(within(row).getByRole("button", { name: "Make default" }));
    const posts = calls.filter((c) => c.method !== "GET");
    expect(posts.map((c) => [c.method, c.path, c.body])).toEqual([
      ["POST", "/api/workspaces/ml/sync", { full: true, describe: true }],
      ["POST", "/api/workspaces/ml/review", { all: true }],
      ["PUT", "/api/workspaces/ml/default", undefined],
    ]);
    // The default workspace has no "Make default".
    const interview = screen.getByRole("row", { name: "Workspace interview" });
    await user.click(within(interview).getByText("More ▾"));
    expect(within(interview).queryByRole("button", { name: "Make default" })).toBeNull();
  });

  it("runs two workspaces at the same time", async () => {
    const pending: Record<string, () => void> = {};
    mockApi({ "GET /api/workspaces": workspaces });
    const sync = (name: string) =>
      new Promise<Response>((resolve) => {
        pending[name] = () => resolve(new Response(JSON.stringify({
          changes: [], counts: { unchanged: 1 }, notes_downloaded: 0, images_downloaded: 0, image_errors: [],
          block_types: {}, skipped_databases: [], images_described: 0, images_reused: 0, describe_errors: [],
          pending_images: 0, not_described_because: null, ignored: [],
        })));
      });
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const m = path.match(/\/api\/workspaces\/(\w+)\/sync$/);
      return m ? sync(m[1]) : base(input, init);
    });
    const user = userEvent.setup();
    renderApp("/management");
    const ml = await screen.findByRole("row", { name: "Workspace ml" });
    const interview = screen.getByRole("row", { name: "Workspace interview" });
    await user.click(within(ml).getByRole("button", { name: "Sync" }));
    await user.click(within(interview).getByRole("button", { name: "Sync" }));
    expect(within(ml).getByRole("button", { name: "Sync" })).toBeDisabled();
    expect(within(interview).getByRole("button", { name: "Sync" })).toBeDisabled();
    expect(screen.getAllByText("Syncing… this can take a minute.")).toHaveLength(2);
    pending.ml();
    await waitFor(() => expect(within(ml).getByRole("button", { name: "Sync" })).toBeEnabled());
    expect(within(interview).getByRole("button", { name: "Sync" })).toBeDisabled(); // still running
    pending.interview();
    await waitFor(() => expect(screen.getAllByText(/Synced\./)).toHaveLength(2));
  });

  it("adds a new workspace: check the Notion page, create it, make it the default", async () => {
    let list = [...workspaces];
    const calls = mockApi({
      "GET /api/workspaces": () => ({ json: list }),
      "POST /api/notion/check": (body: { notion_page: string }) =>
        body.notion_page.includes("bad")
          ? { status: 502, json: { error: "Notion can't see that page. Open it in Notion, click ••• → Connections…", code: "APIResponseError" } }
          : { json: { page_id: PAGE, title: "Graph Theory", suggested_name: "graph-theory" } },
      "POST /api/workspaces": (body: { name: string; path: string }) => {
        const ws = { name: body.name, path: body.path, notion_page_id: PAGE, is_default: false, found: true };
        list = [...list, ws];
        return { status: 201, json: { ...ws, notion_title: "Graph Theory" } };
      },
      "PUT /api/workspaces/graph-theory/default": () => {
        list = list.map((w) => ({ ...w, is_default: w.name === "graph-theory" }));
        return { json: list.find((w) => w.name === "graph-theory") };
      },
    });
    const user = userEvent.setup();
    renderApp("/management");
    await user.click(await screen.findByRole("button", { name: "Add new workspace" }));
    const form = screen.getByRole("form", { name: "Add new workspace" });
    const create = within(form).getByRole("button", { name: "Create workspace" });
    expect(create).toBeDisabled(); // not checked yet

    await user.type(within(form).getByLabelText(/Notion page/), "https://notion.so/bad-page");
    await user.click(within(form).getByRole("button", { name: "Check" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Connections");

    await user.clear(within(form).getByLabelText(/Notion page/));
    await user.type(within(form).getByLabelText(/Notion page/), `https://www.notion.so/Graph-${PAGE.replaceAll("-", "")}`);
    await user.click(within(form).getByRole("button", { name: "Check" }));
    expect(await within(form).findByText("✓ Learnova can read “Graph Theory”.")).toBeInTheDocument();
    expect(within(form).getByLabelText("Name")).toHaveValue("graph-theory"); // from the title
    expect(within(form).getByLabelText(/Folder/)).toHaveValue(""); // no default folder
    expect(create).toBeDisabled();

    await user.type(within(form).getByLabelText(/Folder/), "~/Notes/graphs");
    await user.click(within(form).getByLabelText("Make it the default workspace"));
    await user.click(create);

    expect(await screen.findByText(/Created workspace graph-theory/)).toBeInTheDocument();
    expect(screen.getByText(/Created workspace graph-theory/).closest(".notice")).toHaveTextContent(".learnovaignore");
    expect(await screen.findByRole("row", { name: "Workspace graph-theory" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Add new workspace" })).toBeNull();
    const post = calls.find((c) => c.method === "POST" && c.path === "/api/workspaces")!;
    expect(post.body).toEqual({ name: "graph-theory", notion_page: PAGE, path: "~/Notes/graphs" });
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  });

  it("offers Add new workspace when there are none yet", async () => {
    mockApi({ "GET /api/workspaces": [] });
    const user = userEvent.setup();
    renderApp("/management");
    expect(await screen.findByText("No workspaces yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add new workspace" }));
    expect(screen.getByRole("form", { name: "Add new workspace" })).toBeInTheDocument();
  });
});

describe("All issues page", () => {
  it("lists issues of every status and renders the report files below", async () => {
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "GET /api/issues": [],
      "GET /api/workspaces/interview/reports": [{ name: "2026-11-25-review.md", size: 10, modified: "2026-11-25T03:00:00Z" }],
      "GET /api/workspaces/interview/reports/2026-11-25-review.md": { name: "2026-11-25-review.md", markdown },
    });
    renderApp("/reports");
    expect(await screen.findByRole("heading", { name: "Review: interview" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toHaveTextContent("RDB"); // GFM table rendered
    expect(calls.some((c) => c.path === "/api/issues?status=all")).toBe(true);
  });
});
