import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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
    expect(await screen.findByRole("heading", { name: "Management: interview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Management" })).toHaveAttribute("href", "/management");
    expect(screen.queryByText("Sync & review")).toBeNull();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Management"]);
  });

  it("shows a sync summary with ignored pages", async () => {
    mockApi({
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
    renderApp("/management"); // no workspace chosen: the default one
    expect(await screen.findByRole("heading", { name: "Management: interview" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sync from Notion" }));
    expect(await screen.findByText(/1 ignored,/)).toBeInTheDocument();
    expect(screen.getByText("Ignored by .learnovaignore: Behaviour")).toBeInTheDocument();
  });

  it("runs a review and links to its report", async () => {
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/workspaces/interview/review": {
        nothing_to_review: false, reviewed: 1, skipped: 0, failed: [], new_points: 2, issues: 1,
        missing_topics: 0, fixed: 1, open: 3, possible_duplicates: 0, report: "2026-11-25-review-2.md", model: "m",
        pending_images: 0,
      },
    });
    const user = userEvent.setup();
    renderApp("/management?workspace=interview");
    await user.click(await screen.findByRole("button", { name: "Run review" }));
    expect(await screen.findByText(/Reviewed 1 note/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the report" })).toHaveAttribute(
      "href", "/reports?workspace=interview&report=2026-11-25-review-2.md",
    );
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ all: false });
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
    // The button sits with Sync and Review.
    const toolbar = (await screen.findByRole("button", { name: "Sync from Notion" })).closest(".toolbar") as HTMLElement;
    await user.click(within(toolbar).getByRole("button", { name: "Add new workspace" }));
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
    expect(await screen.findByRole("heading", { name: "Management: graph-theory" })).toBeInTheDocument();
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
