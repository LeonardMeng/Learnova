import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, type Point, type Summary } from "../api/client";
import { ErrorBox, MasteryBar, sourceText, Spinner, when } from "../components/ui";
import { useWorkspace } from "../workspace";

export function StatusPage() {
  const [workspace] = useWorkspace();
  const [weakest, setWeakest] = useState(10);
  const status = useQuery({
    queryKey: ["status", workspace, weakest],
    queryFn: () => api.status(workspace, weakest),
  });

  if (status.isPending) return <Spinner label="Loading…" />;
  if (status.isError) return <ErrorBox error={status.error} onRetry={() => status.refetch()} />;
  const st = status.data;
  if (!st.overall.points) {
    return (
      <section className="card">
        <h1>Status</h1>
        <p>No knowledge points yet. Sync and review a workspace first (Reports page).</p>
      </section>
    );
  }

  return (
    <section>
      <h1>Status{workspace ? `: ${workspace}` : ""}</h1>
      <div className="stats">
        <Stat label="Points" value={st.overall.points} />
        <Stat label="Quizzed" value={st.overall.quizzed} />
        <Stat label="Average mastery" value={<MasteryBar value={st.overall.average_mastery} />} />
        <Stat label="Last quiz" value={when(st.overall.last_quiz)} />
      </div>

      {!workspace && st.workspaces.length > 0 && (
        <div className="card">
          <h2>Workspaces</h2>
          <table>
            <thead>
              <tr><th>Workspace</th><th>Points</th><th>Quizzed</th><th>Average mastery</th><th>Last quiz</th><th /></tr>
            </thead>
            <tbody>
              {st.workspaces.map((w: Summary) => (
                <tr key={w.name}>
                  <td>{w.name}</td>
                  <td>{w.points}</td>
                  <td>{w.quizzed}</td>
                  <td><MasteryBar value={w.average_mastery} /></td>
                  <td className="nowrap">{when(w.last_quiz)}</td>
                  <td><Link to={`/quiz?workspace=${encodeURIComponent(w.name)}`}>Quiz this workspace</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Weakest points</h2>
          <label className="field inline">
            show
            <select value={weakest} onChange={(e) => setWeakest(Number(e.target.value))}>
              {[5, 10, 20, 50].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>
        <PointTable points={st.weakest.map((w) => w.point)} />
      </div>

      <AllPoints workspace={workspace} />
    </section>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="stat">
      <span className="muted">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

function PointTable({ points, editable = false }: { points: Point[]; editable?: boolean }) {
  return (
    <table>
      <thead>
        <tr><th>Point</th><th>Importance</th><th>Mastery</th><th>Quizzed</th><th>Last quiz</th><th>From</th></tr>
      </thead>
      <tbody>
        {points.map((p) => (
          <tr key={p.id}>
            <td title={p.summary}>{p.name}</td>
            <td>{editable ? <ImportanceSelect point={p} /> : p.importance}</td>
            <td><MasteryBar value={p.mastery} /></td>
            <td>{p.review_count}</td>
            <td className="nowrap">{when(p.last_reviewed)}</td>
            <td className="muted">{sourceText(p.sources)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ImportanceSelect({ point }: { point: Point }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (importance: number) => api.setImportance(point.id, importance),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["points"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
  return (
    <select
      aria-label={`Importance of ${point.name}`}
      value={change.variables ?? point.importance}
      disabled={change.isPending}
      onChange={(e) => change.mutate(Number(e.target.value))}
    >
      {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
    </select>
  );
}

type SortKey = "mastery" | "importance" | "name";

function AllPoints({ workspace }: { workspace: string | null }) {
  const points = useQuery({ queryKey: ["points", workspace], queryFn: () => api.points(workspace) });
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("mastery");

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const list = (points.data ?? []).filter(
      (p) => !needle || p.name.toLowerCase().includes(needle) || p.summary.toLowerCase().includes(needle),
    );
    const by: Record<SortKey, (a: Point, b: Point) => number> = {
      mastery: (a, b) => (a.mastery ?? 0) - (b.mastery ?? 0) || b.importance - a.importance,
      importance: (a, b) => b.importance - a.importance || a.name.localeCompare(b.name),
      name: (a, b) => a.name.localeCompare(b.name),
    };
    return [...list].sort(by[sort]);
  }, [points.data, search, sort]);

  return (
    <div className="card">
      <div className="card-head">
        <h2>All points {points.data ? <span className="muted">({points.data.length})</span> : null}</h2>
        <input type="search" placeholder="Search points…" aria-label="Search points" value={search} onChange={(e) => setSearch(e.target.value)} />
        <label className="field inline">
          sort by
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            <option value="mastery">mastery (weakest first)</option>
            <option value="importance">importance</option>
            <option value="name">name</option>
          </select>
        </label>
      </div>
      {points.isPending && <Spinner label="Loading…" />}
      {points.isError && <ErrorBox error={points.error} onRetry={() => points.refetch()} />}
      {points.data && <PointTable points={shown} editable />}
    </div>
  );
}
