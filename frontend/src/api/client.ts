// The only place that talks to the Learnova API. Types come from the API's OpenAPI description
// (`npm run gen:api` regenerates schema.d.ts), so a change in the API breaks the type check.
import type { components } from "./schema";

type S = components["schemas"];
export type Workspace = S["WorkspaceOut"];
export type Status = S["StatusOut"];
export type Summary = S["SummaryOut"];
export type Point = S["PointOut"];
export type TreePoint = S["TreePointOut"];
export type PointDetail = S["PointDetailOut"];
export type PointPatch = S["PointPatchIn"];
export type Issue = S["IssueOut"];
export type IssueStatus = Issue["status"];
export type IssueKind = Issue["kind"];
export type Note = S["NoteOut"];
export type NoteDetail = S["NoteDetailOut"];
export type WebImport = S["WebImportOut"];
export type ReportFile = S["ReportFileOut"];
export type Report = S["ReportOut"];
export type SyncResult = S["SyncOut"];
export type ReviewResult = S["ReviewOut"];
export type Question = S["QuestionOut"];
export type Answer = S["AnswerOut"];
export type QuizKind = Question["type"];
/** What a quiz is limited to: chosen points, or every point under a topic. */
export type QuizScope = { points?: string[]; topic?: string | null };

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
  me: () => request<{ name: string }>("GET", "/api/me"),
  workspaces: () => request<Workspace[]>("GET", "/api/workspaces"),
  status: (workspace: string | null, weakest = 10) =>
    request<Status>("GET", `/api/status${query({ workspace, weakest })}`),
  points: (workspace: string | null) => request<Point[]>("GET", `/api/points${query({ workspace })}`),
  tree: (workspace: string | null) =>
    request<TreePoint[]>("GET", `/api/points/tree${query({ workspace })}`),
  point: (id: string) => request<PointDetail>("GET", `/api/points/${id}`),
  updatePoint: (id: string, patch: PointPatch) =>
    request<PointDetail>("PATCH", `/api/points/${id}`, patch),
  issues: (filters: { workspace?: string | null; status?: IssueStatus | "all"; kind?: IssueKind | null }) =>
    request<Issue[]>("GET", `/api/issues${query(filters)}`),
  setIssueStatus: (id: string, status: "open" | "fixed" | "dismissed") =>
    request<Issue>("PATCH", `/api/issues/${id}`, { status }),
  notes: (workspace: string) => request<Note[]>("GET", `${ws(workspace)}/notes`),
  note: (workspace: string, id: string) => request<NoteDetail>("GET", `${ws(workspace)}/notes/${id}`),
  webImports: () => request<WebImport[]>("GET", "/api/web-imports"),
  reports: (workspace: string) => request<ReportFile[]>("GET", `${ws(workspace)}/reports`),
  report: (workspace: string, name: string) =>
    request<Report>("GET", `${ws(workspace)}/reports/${encodeURIComponent(name)}`),
  sync: (workspace: string, full: boolean) =>
    request<SyncResult>("POST", `${ws(workspace)}/sync`, { full, describe: true }),
  review: (workspace: string, all: boolean) =>
    request<ReviewResult>("POST", `${ws(workspace)}/review`, { all }),
  issueQuestion: (type: QuizKind, workspace: string | null, exclude: string[], scope: QuizScope = {}) =>
    request<Question>("POST", "/api/quiz/questions", {
      type, workspace, exclude, points: scope.points ?? [], topic: scope.topic ?? null,
    }),
  answer: (id: string, answer: string) =>
    request<Answer>("POST", `/api/quiz/questions/${id}/answer`, { answer }),
  skip: (id: string) => request<unknown>("POST", `/api/quiz/questions/${id}/skip`, {}),
};
