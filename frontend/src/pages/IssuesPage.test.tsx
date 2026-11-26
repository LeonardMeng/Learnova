import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const issue = (id: string, status: string, extra: object = {}) => ({
  id, kind: "unclear", status, quote: "AOF is safer", topic: "", problem: "Safer than what?", workspace: "interview",
  note_path: ["Interview", "Redis"], point: { id: "p1", name: "AOF" }, priority: 9, url: "https://www.notion.so/x#b",
  first_seen: "2026-11-25T03:00:00Z", last_seen: "2026-11-25T03:00:00Z", closed_at: null, closed_by: null, ...extra,
});

describe("Issues page", () => {
  it("dismisses an issue, which then leaves the open list", async () => {
    let issues = [issue("i1", "open")];
    const calls = mockApi({
      "GET /api/workspaces": [],
      "GET /api/issues": () => ({ json: issues.filter((i) => i.status === "open") }),
      "PATCH /api/issues/i1": (body: { status: string }) => {
        issues = [issue("i1", body.status)];
        return { json: issues[0] };
      },
    });
    const user = userEvent.setup();
    renderApp("/issues");
    const item = await screen.findByRole("listitem", { name: "Unclear: Safer than what?" });
    expect(within(item).getByRole("link", { name: "Open in Notion" })).toHaveAttribute("href", "https://www.notion.so/x#b");
    expect(within(item).getByText("AOF is safer")).toBeInTheDocument();

    await user.click(within(item).getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByText("No issues here.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "dismissed" });
    expect(calls.some((c) => c.path === "/api/issues?status=open")).toBe(true);
  });

  it("filters by kind, and reopens from All issues", async () => {
    const calls = mockApi({
      "GET /api/workspaces": [],
      "GET /api/issues": [issue("i2", "dismissed", { closed_at: "2026-11-25T04:00:00Z" })],
      "PATCH /api/issues/i2": { json: issue("i2", "open") },
    });
    const user = userEvent.setup();
    renderApp("/reports");
    const item = await screen.findByRole("listitem", { name: "Unclear: Safer than what?" });
    expect(within(item).queryByRole("button", { name: "Dismiss" })).toBeNull();
    await user.click(within(item).getByRole("button", { name: "Reopen" }));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "open" });

    await user.selectOptions(screen.getByLabelText("Kind"), "missing");
    expect(calls.some((c) => c.path === "/api/issues?status=all&kind=missing")).toBe(true);
  });
});
