// The quiz session lives in the browser tab (sessionStorage), so reloading the page continues
// where you were. The API holds the questions themselves; this only remembers progress.
import type { Answer, Question, QuizKind, QuizScope } from "../api/client";

export interface Row {
  point: string;
  score: number | null; // null = skipped
  before: number;
  after: number;
}

export interface Session {
  kind: QuizKind;
  count: number;
  workspace: string | null;
  scope?: QuizScope; // chosen points or a topic (then no workspace)
  scopeLabel?: string | null; // e.g. "topic Redis (6 point(s))", from the API
  number: number; // 1-based number of the current (or next) question
  asked: string[]; // point ids already asked: sent as `exclude`
  current: Question | null;
  result: Answer | null; // set once the current question is answered
  rows: Row[];
  finished: boolean;
  endMessage: string | null; // e.g. "No more knowledge points to ask."
}

const KEY = "learnova.quiz";

export function newSession(
  kind: QuizKind, count: number, workspace: string | null, scope?: QuizScope,
): Session {
  return {
    kind, count, workspace: scope ? null : workspace, scope, scopeLabel: null, number: 1, asked: [], current: null, result: null, rows: [],
    finished: false, endMessage: null,
  };
}

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session | null): void {
  try {
    if (session) sessionStorage.setItem(KEY, JSON.stringify(session));
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage can be unavailable (private mode, quota); the quiz still works without it
  }
}

/** The Quiz page limited to some points or a topic. Any quiz in progress is dropped first, so
 * the page opens on the start form for the new selection. */
export function scopedQuizUrl(scope: QuizScope): string {
  saveSession(null);
  const q = new URLSearchParams();
  if (scope.topic) q.set("topic", scope.topic);
  if (scope.points?.length) q.set("points", scope.points.join(","));
  return `/quiz?${q.toString()}`;
}
