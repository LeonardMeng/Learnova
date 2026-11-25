// The quiz session lives in the browser tab (sessionStorage), so reloading the page continues
// where you were. The API holds the questions themselves; this only remembers progress.
import type { Answer, Question, QuizKind } from "../api/client";

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
  number: number; // 1-based number of the current (or next) question
  asked: string[]; // point ids already asked: sent as `exclude`
  current: Question | null;
  result: Answer | null; // set once the current question is answered
  rows: Row[];
  finished: boolean;
  endMessage: string | null; // e.g. "No more knowledge points to ask."
}

const KEY = "learnova.quiz";

export function newSession(kind: QuizKind, count: number, workspace: string | null): Session {
  return {
    kind, count, workspace, number: 1, asked: [], current: null, result: null, rows: [],
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
