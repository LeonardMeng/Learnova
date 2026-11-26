import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api, ApiError, type IssueKind } from "../api/client";
import { useWorkspace } from "../workspace";

export function MasteryBar({ value }: { value: number | null | undefined }) {
  const v = value ?? 0;
  return (
    <span className="mastery" title={value == null ? "never quizzed" : v.toFixed(2)}>
      <span className="mastery-track">
        <span className="mastery-fill" style={{ width: `${Math.round(v * 100)}%` }} />
      </span>
      <span className="mastery-value">{value == null ? "–" : v.toFixed(2)}</span>
    </span>
  );
}

export function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 3.5 ? "good" : score >= 2 ? "ok" : "bad";
  return <span className={`score score-${tone}`}>{score.toFixed(1)} / 5</span>;
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError || error instanceof Error ? error.message : String(error);
  return (
    <div className="error" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button className="secondary" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <p className="loading" role="status">
      <span className="dot" /> {label}
    </p>
  );
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <span className="muted">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

/** The workspace filter of a page, kept in the URL (`?workspace=`); empty = all workspaces. */
export function WorkspacePicker({ allowAll = true }: { allowAll?: boolean }) {
  const [workspace, setWorkspace] = useWorkspace();
  const { data } = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  return (
    <label className="field inline">
      Workspace
      <select value={workspace ?? ""} onChange={(e) => setWorkspace(e.target.value || null)}>
        {allowAll && <option value="">All</option>}
        {data?.map((w) => (
          <option key={w.name} value={w.name}>
            {w.name}
            {w.found ? "" : " (not found)"}
          </option>
        ))}
      </select>
    </label>
  );
}

export const KIND_LABEL: Record<IssueKind, string> = {
  factual_error: "Factual error",
  unclear: "Unclear",
  missing: "Missing topic",
};

export function IssueBadge({ count }: { count: number }) {
  if (!count) return null;
  return (
    <span className="badge" title={`${count} open issue(s)`}>
      {count}
    </span>
  );
}

/** A Notion page icon: an emoji, or an image URL; 📄 when the page has none. */
export function PageIcon({ icon }: { icon: string | null | undefined }) {
  if (icon && /^https?:\/\//.test(icon)) return <img className="page-icon" src={icon} alt="" />;
  return <span className="page-icon" aria-hidden="true">{icon || "📄"}</span>;
}

/** Scores (0–5) as a tiny line, oldest on the left. */
export function Sparkline({ scores }: { scores: number[] }) {
  if (scores.length < 2) return null;
  const w = 120;
  const h = 28;
  const step = w / (scores.length - 1);
  const points = scores.map((s, i) => `${(i * step).toFixed(1)},${(h - (s / 5) * h).toFixed(1)}`).join(" ");
  return (
    <svg className="sparkline" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Score trend">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "never";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function sourceText(sources: { workspace: string; path: string[] }[]): string {
  if (!sources.length) return "(no notes)";
  const first = `${sources[0].workspace}: ${sources[0].path.join(" / ")}`;
  return sources.length > 1 ? `${first} (+${sources.length - 1})` : first;
}

export function percent(value: number | null | undefined): string {
  return value == null ? "–" : `${Math.round(value * 100)}%`;
}
