import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import Markdown from "react-markdown";
import { useSearchParams } from "react-router";
import remarkGfm from "remark-gfm";
import { api, type ReviewResult, type SyncResult } from "../api/client";
import { ErrorBox, Spinner, when } from "../components/ui";
import { useWorkspace } from "../workspace";

export function ReportsPage() {
  const [picked] = useWorkspace();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  // Reports belong to one workspace: with "All" selected, use the default one.
  const name = picked ?? workspaces.data?.find((w) => w.is_default)?.name ?? workspaces.data?.[0]?.name ?? null;

  if (workspaces.isPending) return <Spinner label="Loading…" />;
  if (workspaces.isError) return <ErrorBox error={workspaces.error} onRetry={() => workspaces.refetch()} />;
  if (!name) {
    return (
      <section className="card">
        <h1>Reports</h1>
        <p>No workspaces yet. Create one with <code>learnova workspace new</code>.</p>
      </section>
    );
  }
  return <WorkspaceReports key={name} workspace={name} />;
}

function WorkspaceReports({ workspace }: { workspace: string }) {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const reports = useQuery({ queryKey: ["reports", workspace], queryFn: () => api.reports(workspace) });
  const selected = params.get("report") ?? reports.data?.[0]?.name ?? null;
  const report = useQuery({
    queryKey: ["report", workspace, selected],
    queryFn: () => api.report(workspace, selected!),
    enabled: selected !== null,
  });
  const select = (report: string) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("report", report);
      return p;
    });

  const [fullSync, setFullSync] = useState(false);
  const [reviewAll, setReviewAll] = useState(false);
  const sync = useMutation({ mutationFn: () => api.sync(workspace, fullSync) });
  const review = useMutation({
    mutationFn: () => api.review(workspace, reviewAll),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["reports", workspace] });
      queryClient.invalidateQueries({ queryKey: ["points"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
      if (r.report) select(r.report);
    },
  });
  const running = sync.isPending || review.isPending;

  return (
    <section>
      <h1>Reports: {workspace}</h1>
      <div className="card toolbar">
        <button disabled={running} onClick={() => sync.mutate()}>Sync from Notion</button>
        <label className="check"><input type="checkbox" checked={fullSync} onChange={(e) => setFullSync(e.target.checked)} /> re-download all</label>
        <button disabled={running} onClick={() => review.mutate()}>Run review</button>
        <label className="check"><input type="checkbox" checked={reviewAll} onChange={(e) => setReviewAll(e.target.checked)} /> all notes</label>
        {sync.isPending && <Spinner label="Syncing… this can take a minute." />}
        {review.isPending && <Spinner label="Reviewing… the model reads every changed note." />}
      </div>
      {sync.isError && <ErrorBox error={sync.error} />}
      {review.isError && <ErrorBox error={review.error} />}
      {sync.data && <SyncSummary result={sync.data} />}
      {review.data && <ReviewSummary result={review.data} />}

      <div className="reports">
        <aside className="card report-list">
          <h2>Reports</h2>
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
    </section>
  );
}

function SyncSummary({ result }: { result: SyncResult }) {
  const c = result.counts;
  return (
    <div className="card notice">
      <strong>Synced.</strong> {c.added ?? 0} added, {c.changed ?? 0} changed, {c["renamed/moved"] ?? 0} renamed/moved,{" "}
      {c.deleted ?? 0} deleted,{c.ignored ? ` ${c.ignored} ignored,` : ""}
      {c.restored ? ` ${c.restored} restored,` : ""} {c.unchanged ?? 0} unchanged.
      {result.images_downloaded > 0 && <> Images: {result.images_downloaded} downloaded.</>}
      {result.images_described + result.images_reused > 0 && (
        <> Descriptions: {result.images_described} new, {result.images_reused} reused.</>
      )}
      {result.pending_images > 0 && result.not_described_because && (
        <p className="muted">{result.pending_images} image(s) not described: {result.not_described_because}</p>
      )}
      {result.ignored && result.ignored.length > 0 && (
        <p className="muted">
          Ignored by .learnovaignore: {result.ignored.map((p) => p.slice(1).join(" / ")).join(", ")}
        </p>
      )}
      {result.changes.length > 0 && (
        <ul>
          {result.changes.map((ch, i) => (
            <li key={i}>
              {ch.kind}: {ch.path.join(" / ")}
              {ch.previous_path && <span className="muted"> (was {ch.previous_path.join(" / ")})</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ReviewSummary({ result }: { result: ReviewResult }) {
  if (result.nothing_to_review) {
    return <div className="card notice">Nothing to review: no note changed since its last review. Sync first, or tick “all notes”.</div>;
  }
  return (
    <div className="card notice">
      <strong>Reviewed {result.reviewed} note(s).</strong> {result.new_points} new point(s), {result.issues} issue(s),{" "}
      {result.missing_topics} missing topic(s)
      {result.possible_duplicates > 0 && <>, {result.possible_duplicates} possible duplicate(s)</>}.
      {result.failed.length > 0 && <p className="bad">{result.failed.length} note(s) failed and will be retried.</p>}
    </div>
  );
}
