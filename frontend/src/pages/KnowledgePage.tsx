// Knowledge › Overview, the first page: the stat strip, the tree of points (topics open and
// close; shortcuts jump to the point's own place) and the selected point's detail panel. The
// sidebar search filters the tree (`?q=`), and the selected point is `?point=`.
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, type TreePoint } from "../api/client";
import { ErrorBox, IssueBadge, MasteryBar, percent, Spinner, Stat, when, WorkspacePicker } from "../components/ui";
import { scopedQuizUrl } from "../quiz/session";
import { useWorkspace } from "../workspace";
import { PointDetailPanel } from "./PointDetail";

type Scope = {
  points: number;
  mastery: number | null;
  issues: number;
  last: string | null;
};

/** The figures for one selected point: its own, or for a topic every point under it (shortcuts
 * included, each once), matching how the API works out a topic's mastery. */
function scopeOf(rows: TreePoint[], root: TreePoint): Scope {
  const children = new Map<string, string[]>();
  const add = (parent: string, child: string) =>
    children.set(parent, [...(children.get(parent) ?? []), child]);
  for (const row of rows) {
    if (row.parent_id) add(row.parent_id, row.id);
    for (const shortcut of row.shortcuts) add(shortcut, row.id);
  }
  const seen = new Set<string>();
  const todo = [...(children.get(root.id) ?? [])];
  while (todo.length) {
    const id = todo.pop()!;
    if (id === root.id || seen.has(id)) continue;
    seen.add(id);
    todo.push(...(children.get(id) ?? []));
  }
  const nodes = [root, ...rows.filter((row) => seen.has(row.id))];
  return {
    points: root.topic ? root.topic.total : 1,
    mastery: root.topic ? root.topic.mastery : root.mastery,
    issues: nodes.reduce((count, row) => count + row.open_issues, 0),
    last:
      nodes
        .map((row) => row.last_reviewed)
        .filter((date): date is string => date !== null)
        .sort()
        .pop() ?? null,
  };
}

export function KnowledgePage() {
  const [workspace] = useWorkspace();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const selected = params.get("point");
  const tree = useQuery({ queryKey: ["tree", workspace], queryFn: () => api.tree(workspace) });
  const status = useQuery({ queryKey: ["status", workspace], queryFn: () => api.status(workspace, 1) });
  const issues = useQuery({
    queryKey: ["issues", workspace, "open", null],
    queryFn: () => api.issues({ workspace, status: "open" }),
  });
  const [expanded, setExpanded] = useState<Set<string> | null>(null); // null: top level open
  const [checked, setChecked] = useState<Set<string>>(new Set()); // points to practice
  const navigate = useNavigate();
  const toggleCheck = (id: string) => {
    const next = new Set(checked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChecked(next);
  };

  const select = (id: string | null) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (id) p.set("point", id);
      else p.delete("point");
      return p;
    });

  const rows = tree.data ?? [];
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const selectedRow = selected ? byId.get(selected) : undefined;
  const scope = selectedRow ? scopeOf(rows, selectedRow) : null;

  /** Open every topic above a point, then select it (used by shortcuts and search results). */
  const reveal = (id: string) => {
    const open = new Set(expanded ?? rows.filter((r) => r.level === 0).map((r) => r.id));
    for (let p = byId.get(id)?.parent_id; p; p = byId.get(p)?.parent_id) open.add(p);
    setExpanded(open);
    select(id);
  };

  return (
    <section>
      <div className="page-head">
        <h1>Knowledge</h1>
        <WorkspacePicker />
      </div>
      {scope && selectedRow && (
        <p className="scope-note">
          Showing {selectedRow.topic ? "topic" : "point"} <strong>{selectedRow.name}</strong>
          <button className="link small" onClick={() => select(null)}>
            Show all
          </button>
        </p>
      )}
      {(status.data || scope) && (
        <div className="stats">
          <Stat label="Points" value={scope ? scope.points : status.data!.overall.points} />
          <Stat
            label="Average mastery"
            value={<MasteryBar value={scope ? scope.mastery : status.data!.overall.average_mastery} />}
          />
          <Stat label="Open issues" value={scope ? scope.issues : (issues.data?.length ?? "…")} />
          <Stat label="Last quiz" value={when(scope ? scope.last : status.data!.overall.last_quiz)} />
        </div>
      )}
      <div className="split">
        <div className="card tree-card">
          {checked.size > 0 && (
            <div className="toolbar practice-bar">
              <button onClick={() => navigate(scopedQuizUrl({ points: [...checked] }))}>
                Practice selected ({checked.size})
              </button>
              <button className="link" onClick={() => setChecked(new Set())}>Clear</button>
            </div>
          )}
          {tree.isPending && <Spinner label="Loading…" />}
          {tree.isError && <ErrorBox error={tree.error} onRetry={() => tree.refetch()} />}
          {tree.data?.length === 0 && (
            <p>No knowledge points yet. Sync and review a workspace first (Workspaces › Management).</p>
          )}
          {tree.data && tree.data.length > 0 &&
            (q.trim() ? (
              <SearchResults rows={rows} byId={byId} q={q} selected={selected} onPick={reveal} />
            ) : (
              <Tree rows={rows} byId={byId} expanded={expanded} setExpanded={setExpanded} selected={selected}
                    onSelect={select} onJump={reveal} checked={checked} onCheck={toggleCheck}
                    onPractice={(id) => navigate(scopedQuizUrl({ topic: id }))} />
            ))}
        </div>
        <div className="detail-column">
          {selected ? (
            <PointDetailPanel id={selected} onSelect={reveal} onClose={() => select(null)} />
          ) : (
            <p className="muted card">Select a point to see its details.</p>
          )}
        </div>
      </div>
    </section>
  );
}

type TreeProps = {
  rows: TreePoint[];
  byId: Map<string, TreePoint>;
  expanded: Set<string> | null;
  setExpanded: (s: Set<string>) => void;
  selected: string | null;
  onSelect: (id: string) => void;
  onJump: (id: string) => void;
  checked: Set<string>;
  onCheck: (id: string) => void;
  onPractice: (topicId: string) => void;
};

function Tree({ rows, byId, expanded, setExpanded, selected, onSelect, onJump, checked, onCheck, onPractice }: TreeProps) {
  // Children in the API's order (most important first): own points, then shortcuts.
  const kids = useMemo(() => {
    const m = new Map<string | null, { row: TreePoint; shortcut: boolean }[]>();
    const add = (parent: string | null, row: TreePoint, shortcut: boolean) =>
      m.set(parent, [...(m.get(parent) ?? []), { row, shortcut }]);
    for (const r of rows) add(r.parent_id && byId.has(r.parent_id) ? r.parent_id : null, r, false);
    for (const r of rows) for (const s of r.shortcuts) if (byId.has(s)) add(s, r, true);
    return m;
  }, [rows, byId]);
  const open = expanded ?? new Set(rows.filter((r) => r.level === 0).map((r) => r.id));
  const toggle = (id: string) => {
    const next = new Set(open);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExpanded(next);
  };

  const render = (parent: string | null, depth: number): React.ReactNode =>
    (kids.get(parent) ?? []).map(({ row, shortcut }) => {
      const isTopic = (kids.get(row.id) ?? []).length > 0;
      const key = `${parent}-${row.id}`;
      if (shortcut) {
        return (
          <li key={key} className="tree-row shortcut" style={{ paddingLeft: depth * 18 }}>
            <span className="twisty" />
            <button className="link" title={`Lives under ${row.path.slice(0, -1).join(" › ") || "the top level"}`}
                    onClick={() => onJump(row.id)}>
              ↗ {row.name}
            </button>
          </li>
        );
      }
      return (
        <li key={key}>
          <div className={`tree-row ${row.id === selected ? "selected" : ""}`} style={{ paddingLeft: depth * 18 }}>
            {isTopic ? (
              <button className="twisty" aria-label={`${open.has(row.id) ? "Close" : "Open"} ${row.name}`}
                      aria-expanded={open.has(row.id)} onClick={() => toggle(row.id)}>
                {open.has(row.id) ? "▾" : "▸"}
              </button>
            ) : (
              <span className="twisty" />
            )}
            <input type="checkbox" aria-label={`Choose ${row.name}`} checked={checked.has(row.id)}
                   onChange={() => onCheck(row.id)} />
            <button className={`link ${row.topic ? "topic-name" : ""}`} onClick={() => onSelect(row.id)}>
              {row.name}
            </button>
            <span className="row-meta">
              {row.topic && (
                <button className="link small practice meta-practice" onClick={() => onPractice(row.id)}
                        aria-label={`Practice this topic: ${row.name}`}>
                  practice
                </button>
              )}
              <span className="meta-mastery">
                {row.topic ? (
                  <span className="muted" title={`${row.topic.quizzed} of ${row.topic.total} quizzed`}>
                    {percent(row.topic.mastery)} · {row.topic.quizzed}/{row.topic.total}
                  </span>
                ) : (
                  <MasteryBar value={row.mastery} />
                )}
              </span>
              <span className="meta-importance importance" title="Importance">★{row.importance}</span>
              <span className="meta-issues"><IssueBadge count={row.open_issues} /></span>
            </span>
          </div>
          {isTopic && open.has(row.id) && <ul className="tree">{render(row.id, depth + 1)}</ul>}
        </li>
      );
    });

  return <ul className="tree" aria-label="Knowledge tree">{render(null, 0)}</ul>;
}

function SearchResults({ rows, byId, q, selected, onPick }: {
  rows: TreePoint[];
  byId: Map<string, TreePoint>;
  q: string;
  selected: string | null;
  onPick: (id: string) => void;
}) {
  const needle = q.trim().toLowerCase();
  const matches = rows.filter(
    (r) => r.name.toLowerCase().includes(needle) || r.summary.toLowerCase().includes(needle),
  );
  if (!matches.length) return <p className="muted">No point matches “{q}”.</p>;
  return (
    <>
    <p className="muted small">{matches.length} point(s) match “{q}”.</p>
    <ul className="results" aria-label="Search results">
      {matches.map((r) => (
        <li key={r.id} className={r.id === selected ? "selected" : ""}>
          <button className="link" onClick={() => onPick(r.id)}>{r.name}</button>
          <span className="muted path"> · {r.path.length > 1 ? `in ${r.path.slice(0, -1).join(" › ")}` : "top level"}</span>
          {r.shortcuts.length > 0 && (
            <span className="muted"> · also in: {r.shortcuts.map((s) => byId.get(s)?.name ?? "?").join(", ")}</span>
          )}
        </li>
      ))}
    </ul>
    </>
  );
}
