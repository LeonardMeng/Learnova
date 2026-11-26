import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const note = (id: string, path: string[], extra: object = {}) => ({
  id, title: path[path.length - 1], path, icon: null, notion_url: `https://www.notion.so/${id}`, ignored: false,
  points: 0, open_issues: 0, last_edited: "2026-11-25T03:00:00Z", reviewed: true, ...extra,
});

describe("Workspace notes page", () => {
  it("shows the page tree with icons and a note's points and issues", async () => {
    mockApi({
      "GET /api/workspaces": [{ name: "interview", path: "/w", notion_page_id: "p", is_default: true, found: true }],
      "GET /api/workspaces/interview/notes": [
        note("n1", ["Interview"], { icon: "🤖" }),
        note("n2", ["Interview", "Redis"], { points: 2, open_issues: 1 }),
        note("n3", ["Interview", "Behaviour"], { ignored: true }),
      ],
      "GET /api/workspaces/interview/notes/n2": {
        ...note("n2", ["Interview", "Redis"], { points: 2, open_issues: 1 }),
        knowledge_points: [{ id: "p1", name: "AOF", summary: "", importance: 3, sources: [], mastery: 0.4, review_count: 1, last_reviewed: null }],
        issues: [{ id: "i1", kind: "missing", status: "open", quote: "", topic: "Replication", problem: "Should be covered.",
          workspace: "interview", note_path: ["Interview", "Redis"], point: null, priority: 4.5, url: "https://www.notion.so/n2",
          first_seen: "2026-11-25T03:00:00Z", last_seen: "2026-11-25T03:00:00Z", closed_at: null, closed_by: null }],
      },
    });
    const user = userEvent.setup();
    renderApp("/workspaces/interview");
    const tree = await screen.findByRole("list", { name: "Notes" });
    expect(within(tree).getByText("🤖")).toBeInTheDocument();
    expect(within(tree).getAllByText("📄")).toHaveLength(2); // pages without an icon
    expect(within(tree).getByText("ignored")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "interview" })).toBeInTheDocument();

    await user.click(within(tree).getByRole("button", { name: "Redis" }));
    const panel = await screen.findByRole("article", { name: "Details of Redis" });
    expect(within(panel).getByRole("link", { name: "AOF" })).toHaveAttribute("href", "/?point=p1");
    expect(panel).toHaveTextContent("Missing topic (open): Replication — Should be covered.");
  });
});
