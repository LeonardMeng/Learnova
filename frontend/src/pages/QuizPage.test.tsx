import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const workspaces = [{ name: "interview", path: "/w", notion_page_id: "p", is_default: true, found: true }];

function question(n: number, type: "short" | "mc" | "tf") {
  return {
    id: `q${n}`, type, point_id: `p${n}`, status: "open", created_at: "2026-11-25T05:00:00Z",
    source: "interview: Interview Questions / Redis",
    text: type === "tf" ? `Statement ${n}.` : `Question ${n}?`,
    options: type === "mc" ? ["Snapshots", "A log of writes", "Replication", "Sharding"] : type === "tf" ? ["True", "False"] : null,
  };
}

const answer = (id: string, extra: object = {}) => ({
  question_id: id, point: { id: "p1", name: "AOF" }, score: 5, correct: true, correct_option: "B) A log of writes",
  feedback: "", missing: "", reference_answer: "", explanation: "AOF appends every write.",
  mastery_before: 0, mastery_after: 0.18, ...extra,
});

describe("Quiz page", () => {
  it("runs a multiple-choice quiz, excludes asked points, and skips", async () => {
    let n = 0;
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/quiz/questions": () => ({ status: 201, json: question(++n, "mc") }),
      "POST /api/quiz/questions/q1/answer": (body: { answer: string }) => ({ json: answer("q1", { correct: body.answer === "B" }) }),
      "POST /api/quiz/questions/q2/skip": { question_id: "q2", status: "skipped" },
    });
    const user = userEvent.setup();
    renderApp("/quiz?workspace=interview");

    await user.click(screen.getByLabelText(/Multiple choice/));
    await user.clear(screen.getByRole("spinbutton"));
    await user.type(screen.getByRole("spinbutton"), "2");
    await user.click(screen.getByRole("button", { name: "Start quiz" }));

    expect(await screen.findByText("Question 1?")).toBeInTheDocument();
    expect(screen.getByText("from interview: Interview Questions / Redis")).toBeInTheDocument();
    await user.keyboard("b"); // keyboard shortcut picks option B
    expect(await screen.findByText("Correct.")).toBeInTheDocument();
    expect(screen.getByText("AOF appends every write.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next question" }));
    expect(await screen.findByText("Question 2?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip" }));
    expect(await screen.findByText("Quiz finished")).toBeInTheDocument();
    expect(screen.getByText(/1 answered, 1 skipped/)).toBeInTheDocument();

    const issued = calls.filter((c) => c.path === "/api/quiz/questions");
    expect(issued.map((c) => c.body)).toEqual([
      { type: "mc", workspace: "interview", exclude: [] },
      { type: "mc", workspace: "interview", exclude: ["p1"] },
    ]);
    expect(calls.find((c) => c.path.endsWith("/answer"))?.body).toEqual({ answer: "B" });
  });

  it("grades a short answer and shows feedback and the reference answer", async () => {
    mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/quiz/questions": { ...question(1, "short") },
      "POST /api/quiz/questions/q1/answer": answer("q1", {
        score: 3.4, correct: null, correct_option: null, explanation: "",
        feedback: "Right idea, no numbers.", missing: "How much data each loses.", reference_answer: "RDB loses more.",
        mastery_after: 0.2,
      }),
    });
    const user = userEvent.setup();
    renderApp("/quiz");
    await user.click(screen.getByRole("button", { name: "Start quiz" }));
    await user.type(await screen.findByLabelText("Your answer"), "RDB snapshots, AOF logs.");
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(await screen.findByText("3.4 / 5")).toBeInTheDocument();
    expect(screen.getByText("Right idea, no numbers.")).toBeInTheDocument();
    expect(screen.getByText("How much data each loses.")).toBeInTheDocument();
    expect(screen.getByText("Reference answer")).toBeInTheDocument();
  });

  it("resumes the current question after a reload without asking for a new one", async () => {
    const calls = mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/quiz/questions": { ...question(1, "tf") },
    });
    const user = userEvent.setup();
    const first = renderApp("/quiz");
    await user.click(screen.getByLabelText(/True \/ false/));
    await user.click(screen.getByRole("button", { name: "Start quiz" }));
    expect(await screen.findByText("True or false: Statement 1.")).toBeInTheDocument();
    first.unmount();

    renderApp("/quiz");
    expect(await screen.findByText("True or false: Statement 1.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/api/quiz/questions")).toHaveLength(1);
  });

  it("shows API errors with a retry, and ends when no points are left", async () => {
    let fail = true;
    mockApi({
      "GET /api/workspaces": workspaces,
      "POST /api/quiz/questions": () =>
        fail
          ? { status: 400, json: { error: "No main model configured.", code: "LLMConfigError" } }
          : { status: 404, json: { error: "No more knowledge points to ask.", code: "NoPointsLeft" } },
    });
    const user = userEvent.setup();
    renderApp("/quiz");
    await user.click(screen.getByRole("button", { name: "Start quiz" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No main model configured.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Quiz finished")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("No more knowledge points to ask.")).toBeInTheDocument());
  });
});
