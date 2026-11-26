// Knowledge › All points: every point in one sortable list (weakest first by default).
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, type TreePoint } from "../api/client";
import { ErrorBox, IssueBadge, MasteryBar, percent, sourceText, Spinner, when, WorkspacePicker } from "../components/ui";
import { useWorkspace } from "../workspace";

type SortKey = "mastery" | "importance" | "last_quiz" | "issues" | "name";

const mastery = (p: TreePoint) => (p.topic ? p.topic.mastery : p.mastery) ?? 0;

const SORTS: Record<SortKey, (a: TreePoint, b: TreePoint) => number> = {
  mastery: (a, b) => mastery(a) - mastery(b) || b.importance - a.importance,
  importance: (a, b) => b.importance - a.importance || a.name.localeCompare(b.name),
  last_quiz: (a, b) => (b.last_reviewed ?? "").localeCompare(a.last_reviewed ?? ""),
  issues: (a, b) => b.open_issues - a.open_issues || b.importance - a.importance,
  name: (a, b) => a.name.localeCompare(b.name),
};

export function AllPointsPage() {
  const [workspace] = useWorkspace();
  const tree = useQuery({ queryKey: ["tree", workspace], queryFn: () => api.tree(workspace) });
  const [sort, setSort] = useState<SortKey>("mastery");
  const [filter, setFilter] = useState("");
  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return [...(tree.data ?? [])]
      .filter((p) => !needle || p.name.toLowerCase().includes(needle) || p.summary.toLowerCase().includes(needle))
      .sort(SORTS[sort]);
  }, [tree.data, sort, filter]);

  return (
    <section>
      <div className="page-head">
        <h1>All points {tree.data && <span className="muted">({tree.data.length})</span>}</h1>
        <WorkspacePicker />
      </div>
      <div className="card">
        <div className="toolbar">
          <input type="search" placeholder="Filter…" aria-label="Filter points" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <label className="field inline">
            sort by
            <select aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="mastery">mastery (weakest first)</option>
              <option value="importance">importance</option>
              <option value="last_quiz">last quiz</option>
              <option value="issues">open issues</option>
              <option value="name">name</option>
            </select>
          </label>
        </div>
        {tree.isPending && <Spinner label="Loading…" />}
        {tree.isError && <ErrorBox error={tree.error} onRetry={() => tree.refetch()} />}
        {tree.data && (
          <table>
            <thead>
              <tr><th>Point</th><th>Importance</th><th>Mastery</th><th>Issues</th><th>Last quiz</th><th>From</th></tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <tr key={p.id}>
                  <td title={p.summary}>
                    <Link to={`/?point=${p.id}`}>{p.name}</Link>
                    {p.path.length > 1 && <div className="muted small">{p.path.slice(0, -1).join(" › ")}</div>}
                  </td>
                  <td>{p.importance}</td>
                  <td>
                    {p.topic ? <span className="muted">topic · {percent(p.topic.mastery)}</span> : <MasteryBar value={p.mastery} />}
                  </td>
                  <td><IssueBadge count={p.open_issues} /></td>
                  <td className="nowrap">{when(p.last_reviewed)}</td>
                  <td className="muted">{sourceText(p.sources)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
