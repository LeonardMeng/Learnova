// Workspaces › Sync & review: the workspaces, and the Sync and Review buttons for one of them.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api, type ReviewResult, type SyncResult } from "../api/client";
import { ErrorBox, Spinner, WorkspacePicker } from "../components/ui";
import { useWorkspace } from "../workspace";

export function SyncPage() {
  const [picked] = useWorkspace();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const name = picked ?? workspaces.data?.find((w) => w.is_default)?.name ?? workspaces.data?.[0]?.name ?? null;

  if (workspaces.isPending) return <Spinner label="Loading…" />;
  if (workspaces.isError) return <ErrorBox error={workspaces.error} onRetry={() => workspaces.refetch()} />;
  if (!name) {
    return (
      <section className="card">
        <h1>Sync &amp; review</h1>
        <p>No workspaces yet. Create one with <code>learnova workspace new</code>.</p>
      </section>
    );
  }
  return (
    <section>
      <div className="page-head">
        <h1>Sync &amp; review: {name}</h1>
        <WorkspacePicker allowAll={false} />
      </div>
      <Actions key={name} workspace={name} />
      <div className="card">
        <h2>Workspaces</h2>
        <table>
          <thead><tr><th>Name</th><th>Folder</th><th /></tr></thead>
          <tbody>
            {workspaces.data.map((w) => (
              <tr key={w.name}>
                <td>
                  <Link to={`/workspaces/${encodeURIComponent(w.name)}`}>{w.name}</Link>
                  {w.is_default && <span className="muted"> (default)</span>}
                </td>
                <td className="muted">{w.path}{w.found ? "" : " (not found)"}</td>
                <td><Link to={`/sync?workspace=${encodeURIComponent(w.name)}`}>sync / review</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Actions({ workspace }: { workspace: string }) {
  const queryClient = useQueryClient();
  const [fullSync, setFullSync] = useState(false);
  const [reviewAll, setReviewAll] = useState(false);
  const refresh = () => {
    for (const key of ["reports", "points", "tree", "status", "issues", "notes", "point"]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
  const sync = useMutation({ mutationFn: () => api.sync(workspace, fullSync), onSuccess: refresh });
  const review = useMutation({ mutationFn: () => api.review(workspace, reviewAll), onSuccess: refresh });
  const running = sync.isPending || review.isPending;
  return (
    <>
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
      {review.data && <ReviewSummary workspace={workspace} result={review.data} />}
    </>
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

function ReviewSummary({ workspace, result }: { workspace: string; result: ReviewResult }) {
  if (result.nothing_to_review) {
    return <div className="card notice">Nothing to review: no note changed since its last review. Sync first, or tick “all notes”.</div>;
  }
  return (
    <div className="card notice">
      <strong>Reviewed {result.reviewed} note(s).</strong> {result.new_points} new point(s),{" "}
      {result.issues + result.missing_topics} new issue(s), {result.fixed} fixed, {result.open} open
      {result.possible_duplicates > 0 && <>, {result.possible_duplicates} possible duplicate(s)</>}.{" "}
      {result.report && <Link to={`/reports?workspace=${encodeURIComponent(workspace)}&report=${encodeURIComponent(result.report)}`}>Open the report</Link>}
      {result.failed.length > 0 && <p className="bad">{result.failed.length} note(s) failed and will be retried.</p>}
    </div>
  );
}
