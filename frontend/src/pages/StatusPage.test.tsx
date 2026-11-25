import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const point = (id: string, name: string, importance: number, mastery: number | null) => ({
  id, name, summary: `About ${name}.`, importance, mastery, review_count: mastery == null ? 0 : 1,
  last_reviewed: null, sources: [{ workspace: "interview", path: ["Interview", "Redis"] }],
});
const summary = (name: string, points: number) => ({ name, points, quizzed: 1, average_mastery: 0.15, last_quiz: null });

describe("Status page", () => {
  it("shows totals, workspaces, weakest points, and edits importance", async () => {
    const rdb = point("1", "RDB vs AOF", 5, null);
    const evict = point("2", "Eviction", 2, 0.3);
    const calls = mockApi({
      "GET /api/workspaces": [],
      "GET /api/status": { overall: summary("All", 2), workspaces: [summary("interview", 2)], weakest: [{ point: rdb, weight: 10 }] },
      "GET /api/points": [evict, rdb],
      "PATCH /api/points/2": (body: { importance: number }) => ({ json: { ...evict, importance: body.importance } }),
    });
    const user = userEvent.setup();
    renderApp("/status");

    expect(await screen.findByText("Weakest points")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Quiz this workspace" })).toHaveAttribute("href", "/quiz?workspace=interview");
    const all = (await screen.findByText(/All points/)).closest(".card") as HTMLElement;
    const rows = within(all).getAllByRole("row").slice(1) as HTMLTableRowElement[];
    expect(rows.map((r) => r.cells[0].textContent)).toEqual(["RDB vs AOF", "Eviction"]); // weakest first

    await user.selectOptions(within(all).getByLabelText("Importance of Eviction"), "4");
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ importance: 4 });

    await user.type(within(all).getByLabelText("Search points"), "evic");
    expect(within(all).getAllByRole("row")).toHaveLength(2);
  });
});
