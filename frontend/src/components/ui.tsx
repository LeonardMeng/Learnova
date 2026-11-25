import { ApiError } from "../api/client";

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
