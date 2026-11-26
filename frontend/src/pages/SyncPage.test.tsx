import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const workspaces = [
  { name: "ml", path: "/ml", notion_page_id: "p", is_default: false, found: true },
  { name: "interview", path: "/w", notion_page_id: "p", is_default: true, found: true },
];
const markdown = "# Review: interview\n\n| | Point |\n|---|---|\n| new | **RDB** |\n";

describe("Sync & review page", () => {
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
    renderApp("/sync"); // no workspace chosen: the default one
    expect(await screen.findByRole("heading", { name: "Sync & review: interview" })).toBeInTheDocument();
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
    renderApp("/sync?workspace=interview");
    await user.click(await screen.findByRole("button", { name: "Run review" }));
    expect(await screen.findByText(/Reviewed 1 note/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the report" })).toHaveAttribute(
      "href", "/reports?workspace=interview&report=2026-11-25-review-2.md",
    );
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ all: false });
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
