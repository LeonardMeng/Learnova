// The report files of one workspace (review, organize, web check), newest first, with the
// selected one rendered. Shown below All issues.
import { useQuery } from "@tanstack/react-query";
import Markdown from "react-markdown";
import { useSearchParams } from "react-router";
import remarkGfm from "remark-gfm";
import { api } from "../api/client";
import { ErrorBox, Spinner, when } from "../components/ui";
import { useWorkspace } from "../workspace";

export function ReportFiles() {
  const [picked] = useWorkspace();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  // Report files belong to one workspace: with "All" selected, use the default one.
  const name = picked ?? workspaces.data?.find((w) => w.is_default)?.name ?? workspaces.data?.[0]?.name ?? null;
  if (!name) return null;
  return <Files key={name} workspace={name} />;
}

function Files({ workspace }: { workspace: string }) {
  const [params, setParams] = useSearchParams();
  const reports = useQuery({ queryKey: ["reports", workspace], queryFn: () => api.reports(workspace) });
  const selected = params.get("report") ?? reports.data?.[0]?.name ?? null;
  const report = useQuery({
    queryKey: ["report", workspace, selected],
    queryFn: () => api.report(workspace, selected!),
    enabled: selected !== null,
  });
  const select = (name: string) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("report", name);
      return p;
    });

  return (
    <>
      <h2 className="section-title">Report files: {workspace}</h2>
      <div className="reports">
        <aside className="card report-list">
          {reports.isPending && <Spinner label="Loading…" />}
          {reports.isError && <ErrorBox error={reports.error} onRetry={() => reports.refetch()} />}
          {reports.data?.length === 0 && <p className="muted">No reports yet. Run a review.</p>}
          <ul>
            {reports.data?.map((r) => (
              <li key={r.name}>
                <button className={`link ${r.name === selected ? "active" : ""}`} onClick={() => select(r.name)}>
                  {r.name.replace(/\.md$/, "")}
                </button>
                <span className="muted">{when(r.modified)}</span>
              </li>
            ))}
          </ul>
        </aside>
        <article className="card report">
          {report.isPending && selected && <Spinner label="Loading…" />}
          {report.isError && <ErrorBox error={report.error} />}
          {report.data && (
            <div className="markdown">
              <Markdown remarkPlugins={[remarkGfm]}>{report.data.markdown}</Markdown>
            </div>
          )}
        </article>
      </div>
    </>
  );
}
