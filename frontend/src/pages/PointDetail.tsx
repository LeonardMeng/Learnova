// The detail panel of one knowledge point: where it lives, its notes, quiz answers and issues,
// and the controls to change its importance, its parent and its shortcuts. Every change goes to
// PATCH /api/points/{id}; a change the rules refuse (a loop, too deep) shows the API's message.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api, type PointDetail, type PointPatch } from "../api/client";
import { scopedQuizUrl } from "../quiz/session";
import { ErrorBox, KIND_LABEL, MasteryBar, percent, ScoreBadge, Sparkline, Spinner, when } from "../components/ui";

export function PointDetailPanel({ id, onSelect, onClose }: {
  id: string;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const point = useQuery({ queryKey: ["point", id], queryFn: () => api.point(id) });
  if (point.isPending) return <div className="card"><Spinner label="Loading…" /></div>;
  if (point.isError) return <div className="card"><ErrorBox error={point.error} onRetry={() => point.refetch()} /></div>;
  return <Detail point={point.data} onSelect={onSelect} onClose={onClose} />;
}

function Detail({ point, onSelect, onClose }: { point: PointDetail; onSelect: (id: string) => void; onClose: () => void }) {
  const queryClient = useQueryClient();
  const all = useQuery({ queryKey: ["tree", null], queryFn: () => api.tree(null) });
  const update = useMutation({
    mutationFn: (patch: PointPatch) => api.updatePoint(point.id, patch),
    onSuccess: (data) => {
      queryClient.setQueryData(["point", point.id], data);
      queryClient.invalidateQueries({ queryKey: ["tree"] });
      queryClient.invalidateQueries({ queryKey: ["points"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
  const navigate = useNavigate();
  const [moveTo, setMoveTo] = useState("");
  const [shortcut, setShortcut] = useState("");
  const others = (all.data ?? []).filter((p) => p.id !== point.id);
  const shortcutIds = point.shortcuts.map((s) => s.id);
  const scores = [...point.attempts].reverse().map((a) => a.score);
  const openIssues = point.issues.filter((i) => i.status === "open");

  return (
    <article className="card detail" aria-label={`Details of ${point.name}`}>
      <div className="card-head">
        <h2>{point.name}</h2>
        <button className="secondary small" onClick={onClose} aria-label="Close details">×</button>
      </div>
      <p>{point.summary}</p>
      <div className="actions">
        <button onClick={() => navigate(scopedQuizUrl(point.topic ? { topic: point.id } : { points: [point.id] }))}>
          {point.topic ? "Practice this topic" : "Practice this point"}
        </button>
      </div>

      <dl className="facts">
        <dt>Mastery</dt>
        <dd>
          {point.topic ? (
            <>topic · {percent(point.topic.mastery)} · {point.topic.quizzed} of {point.topic.total} quizzed</>
          ) : (
            <><MasteryBar value={point.mastery} /> <span className="muted">{point.review_count} answer(s), last {when(point.last_reviewed)}</span></>
          )}
        </dd>
        <dt>Importance</dt>
        <dd>
          <select
            aria-label="Importance"
            value={point.importance}
            disabled={update.isPending}
            onChange={(e) => update.mutate({ importance: Number(e.target.value) })}
          >
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </dd>
        <dt>Located in</dt>
        <dd>
          <ul className="plain">
            {point.paths.map((path, i) => (
              <li key={i}>
                {path.length > 1 ? path.slice(0, -1).join(" › ") : "Top level"}
                {i > 0 && (
                  <>
                    {" "}<span className="muted">(shortcut)</span>{" "}
                    <button className="link small" onClick={() => update.mutate({ shortcuts: shortcutIds.filter((s) => s !== point.shortcuts[i - 1].id) })}>
                      remove
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </dd>
      </dl>

      <div className="control-rows">
        <div className="control-row">
          <span className="control-label">Move to…</span>
          <select aria-label="Move to" value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
            <option value="">choose…</option>
            <option value="__top">Top level</option>
            {others.map((p) => <option key={p.id} value={p.id}>{p.path.join(" › ")}</option>)}
          </select>
          <button
            className="secondary"
            disabled={!moveTo || update.isPending}
            onClick={() => update.mutate({ parent_id: moveTo === "__top" ? null : moveTo }, { onSuccess: () => setMoveTo("") })}
          >
            Move
          </button>
        </div>
        <div className="control-row">
          <span className="control-label">Add shortcut…</span>
          <select aria-label="Add shortcut" value={shortcut} onChange={(e) => setShortcut(e.target.value)}>
            <option value="">choose…</option>
            {others.filter((p) => !shortcutIds.includes(p.id) && p.id !== point.parent_id).map((p) => (
              <option key={p.id} value={p.id}>{p.path.join(" › ")}</option>
            ))}
          </select>
          <button
            className="secondary"
            disabled={!shortcut || update.isPending}
            onClick={() => update.mutate({ shortcuts: [...shortcutIds, shortcut] }, { onSuccess: () => setShortcut("") })}
          >
            Add
          </button>
        </div>
      </div>
      {update.isError && <ErrorBox error={update.error} />}

      {point.children.length > 0 && (
        <>
          <h3>Under it</h3>
          <ul className="plain">
            {point.children.map((c) => (
              <li key={c.id}><button className="link" onClick={() => onSelect(c.id)}>{c.name}</button></li>
            ))}
          </ul>
        </>
      )}

      <h3>Notes</h3>
      {point.notes.length ? (
        <ul className="plain">
          {point.notes.map((n) => (
            <li key={n.notion_url + n.path.join("/")}>
              {n.workspace}: {n.path.join(" / ")} · <a href={n.notion_url} target="_blank" rel="noreferrer">Open in Notion</a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No notes (a topic made by organize, or its notes were removed).</p>
      )}

      <h3>Open issues {openIssues.length > 0 && <span className="muted">({openIssues.length})</span>}</h3>
      {openIssues.length ? (
        <ul className="plain">
          {openIssues.map((i) => (
            <li key={i.id}>
              <strong>{KIND_LABEL[i.kind]}</strong>: {i.kind === "missing" ? i.topic : `“${i.quote}”`} — {i.problem}
              {i.url && <> · <a href={i.url} target="_blank" rel="noreferrer">Open in Notion</a></>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None.</p>
      )}

      <h3>Quiz answers</h3>
      {point.attempts.length ? (
        <>
          <Sparkline scores={scores} />
          <ul className="plain attempts">
            {point.attempts.map((a, i) => (
              <li key={i}>
                <ScoreBadge score={a.score} /> <span className="muted">{when(a.created_at)}</span> {a.question}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="muted">Not quizzed yet.</p>
      )}
    </article>
  );
}
