// The only place that talks to the Learnova API. Types come from the API's OpenAPI description
// (`npm run gen:api` regenerates schema.d.ts), so a change in the API breaks the type check.
import type { components } from "./schema";

type S = components["schemas"];
export type Workspace = S["WorkspaceOut"];
export type Status = S["StatusOut"];
export type Summary = S["SummaryOut"];
export type Point = S["PointOut"];
export type ReportFile = S["ReportFileOut"];
export type Report = S["ReportOut"];
export type SyncResult = S["SyncOut"];
export type ReviewResult = S["ReviewOut"];
export type Question = S["QuestionOut"];
export type Answer = S["AnswerOut"];
export type QuizKind = Question["type"];

/** An error answered by the API: `{error, code}`, or FastAPI's validation `detail`. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Offline", "Can't reach the Learnova API. Is `learnova serve` running?");
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((d: { msg: string }) => d.msg).join("; ")
      : undefined;
    throw new ApiError(res.status, data?.code ?? "HttpError", data?.error ?? detail ?? res.statusText);
  }
  return data as T;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

const ws = (name: string) => `/api/workspaces/${encodeURIComponent(name)}`;

export const api = {
  workspaces: () => request<Workspace[]>("GET", "/api/workspaces"),
  status: (workspace: string | null, weakest = 10) =>
    request<Status>("GET", `/api/status${query({ workspace, weakest })}`),
  points: (workspace: string | null) => request<Point[]>("GET", `/api/points${query({ workspace })}`),
  setImportance: (id: string, importance: number) =>
    request<Point>("PATCH", `/api/points/${id}`, { importance }),
  reports: (workspace: string) => request<ReportFile[]>("GET", `${ws(workspace)}/reports`),
  report: (workspace: string, name: string) =>
    request<Report>("GET", `${ws(workspace)}/reports/${encodeURIComponent(name)}`),
  sync: (workspace: string, full: boolean) =>
    request<SyncResult>("POST", `${ws(workspace)}/sync`, { full, describe: true }),
  review: (workspace: string, all: boolean) =>
    request<ReviewResult>("POST", `${ws(workspace)}/review`, { all }),
  issueQuestion: (type: QuizKind, workspace: string | null, exclude: string[]) =>
    request<Question>("POST", "/api/quiz/questions", { type, workspace, exclude }),
  answer: (id: string, answer: string) =>
    request<Answer>("POST", `/api/quiz/questions/${id}/answer`, { answer }),
  skip: (id: string) => request<unknown>("POST", `/api/quiz/questions/${id}/skip`, {}),
};
