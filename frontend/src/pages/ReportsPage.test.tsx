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

describe("Reports page", () => {
  it("shows a sync summary with ignored and restored pages", async () => {
    mockApi({
      "GET /api/workspaces": workspaces,
      "GET /api/workspaces/interview/reports": [],
      "POST /api/workspaces/interview/sync": {
        changes: [{ kind: "ignored", path: ["Interview", "Behaviour"], previous_path: null }],
        counts: { added: 0, changed: 0, "renamed/moved": 0, deleted: 0, ignored: 1, restored: 0, unchanged: 3 },
        notes_downloaded: 0, images_downloaded: 0, image_errors: [], block_types: {}, skipped_databases: [],
        images_described: 0, images_reused: 0, describe_errors: [], pending_images: 0, not_described_because: null,
        ignored: [["Interview", "Behaviour"]],
      },
    });
    const user = userEvent.setup();
    renderApp("/reports");
    await user.click(await screen.findByRole("button", { name: "Sync from Notion" }));
    expect(await screen.findByText(/1 ignored,/)).toBeInTheDocument();
    expect(screen.getByText("Ignored by .learnovaignore: Behaviour")).toBeInTheDocument();
    expect(screen.getByText("ignored: Interview / Behaviour")).toBeInTheDocument();
  });

  it("uses the default workspace, lists and renders reports, and runs a review", async () => {
    let reports = [{ name: "2026-11-25-review.md", size: 10, modified: "2026-11-25T03:00:00Z" }];
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "GET /api/workspaces/interview/reports": () => ({ json: reports }),
      "GET /api/workspaces/interview/reports/2026-11-25-review.md": { name: "2026-11-25-review.md", markdown },
      "GET /api/workspaces/interview/reports/2026-11-25-review-2.md": { name: "2026-11-25-review-2.md", markdown: "# Second" },
      "POST /api/workspaces/interview/review": () => {
        reports = [{ name: "2026-11-25-review-2.md", size: 5, modified: "2026-11-25T04:00:00Z" }, ...reports];
        return { json: { nothing_to_review: false, reviewed: 1, skipped: 0, failed: [], new_points: 2, issues: 1,
          missing_topics: 0, fixed: 1, open: 3, possible_duplicates: 0, report: "2026-11-25-review-2.md", model: "m", pending_images: 0 } };
      },
    });
    const user = userEvent.setup();
    renderApp("/reports"); // "All" selected: falls back to the default workspace

    expect(await screen.findByRole("heading", { name: "Reports: interview" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Review: interview" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toHaveTextContent("RDB"); // GFM table rendered

    await user.click(screen.getByRole("button", { name: "Run review" }));
    expect(await screen.findByText(/Reviewed 1 note/)).toBeInTheDocument();
    expect(screen.getByText(/1 new issue\(s\), 1 fixed, 3 open/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Second" })).toBeInTheDocument(); // new report opened
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ all: false });
  });
});
