// Workspaces › Management: the workspaces; Sync and Review for one of them; and adding a new
// workspace (check the Notion page, then create it with an empty .learnovaignore).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api, type NotionCheck, type ReviewResult, type SyncResult } from "../api/client";
import { ErrorBox, Spinner, WorkspacePicker } from "../components/ui";
import { useWorkspace } from "../workspace";

export function ManagementPage() {
  const [picked, setPicked] = useWorkspace();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const name = picked ?? workspaces.data?.find((w) => w.is_default)?.name ?? workspaces.data?.[0]?.name ?? null;

  if (workspaces.isPending) return <Spinner label="Loading…" />;
  if (workspaces.isError) return <ErrorBox error={workspaces.error} onRetry={() => workspaces.refetch()} />;
  const onCreated = (ws: string) => {
    setAdding(false);
    setCreated(ws);
    setPicked(ws);
  };
  const addButton = (
    <button className="secondary" aria-expanded={adding} onClick={() => setAdding(!adding)}>
      Add new workspace
    </button>
  );
  return (
    <section>
      <div className="page-head">
        <h1>Management{name ? `: ${name}` : ""}</h1>
        {name && <WorkspacePicker allowAll={false} />}
      </div>
      {name ? (
        <Actions key={name} workspace={name} extra={addButton} />
      ) : (
        <div className="card toolbar">
          <span>No workspaces yet.</span>
          {addButton}
        </div>
      )}
      {adding && <AddWorkspace onCreated={onCreated} onCancel={() => setAdding(false)} />}
      {created && created === name && (
        <div className="card notice">
          <strong>Created workspace {created}</strong> with an empty <code>.learnovaignore</code> (no pages left
          out). Press <em>Sync from Notion</em> to download its notes, then <em>Run review</em>.
        </div>
      )}
      {workspaces.data.length > 0 && (
        <div className="card">
          <h2>Workspaces</h2>
          <table>
            <thead><tr><th>Name</th><th>Folder</th><th>Actions</th></tr></thead>
            <tbody>
              {workspaces.data.map((w) => (
                <tr key={w.name}>
                  <td>
                    <Link to={`/workspaces/${encodeURIComponent(w.name)}`}>{w.name}</Link>
                    {w.is_default && <span className="muted"> (default)</span>}
                  </td>
                  <td className="muted">{w.path}{w.found ? "" : " (not found)"}</td>
                  <td><Link to={`/management?workspace=${encodeURIComponent(w.name)}`}>sync / review</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Check that Notion can see the page, then create the workspace. */
function AddWorkspace({ onCreated, onCancel }: { onCreated: (name: string) => void; onCancel: () => void }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState("");
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const [makeDefault, setMakeDefault] = useState(false);
  const [checked, setChecked] = useState<NotionCheck | null>(null);
  const check = useMutation({
    mutationFn: () => api.checkNotion(page),
    onSuccess: (r) => {
      setChecked(r);
      if (!name.trim()) setName(r.suggested_name);
    },
  });
  const create = useMutation({
    mutationFn: async () => {
      const ws = await api.createWorkspace(name.trim(), checked!.page_id, folder.trim());
      if (makeDefault && !ws.is_default) await api.setDefault(ws.name);
      return ws;
    },
    onSuccess: (ws) => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      onCreated(ws.name);
    },
  });
  const ready = checked !== null && name.trim() !== "" && folder.trim() !== "";

  return (
    <form
      className="card add-workspace"
      aria-label="Add new workspace"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) create.mutate();
      }}
    >
      <h2>Add new workspace</h2>
      <label className="field wide">
        Notion page (link or ID)
        <span className="row">
          <input
            value={page}
            onChange={(e) => {
              setPage(e.target.value);
              setChecked(null);
            }}
            placeholder="https://www.notion.so/…"
          />
          <button type="button" className="secondary" disabled={!page.trim() || check.isPending} onClick={() => check.mutate()}>
            Check
          </button>
        </span>
      </label>
      {check.isPending && <Spinner label="Asking Notion…" />}
      {check.isError && <ErrorBox error={check.error} />}
      {checked && <p className="good">✓ Learnova can read “{checked.title}”.</p>}
      <label className="field wide">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field wide">
        Folder (created if missing; <code>~</code> is your home folder)
        <input value={folder} onChange={(e) => setFolder(e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} /> Make it the default workspace
      </label>
      {create.isError && <ErrorBox error={create.error} />}
      <div className="actions">
        <button type="submit" disabled={!ready || create.isPending}>Create workspace</button>
        <button type="button" className="link" onClick={onCancel}>Cancel</button>
        {!checked && <span className="muted small">Check the Notion page first.</span>}
      </div>
    </form>
  );
}

function Actions({ workspace, extra }: { workspace: string; extra?: React.ReactNode }) {
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
        {extra}
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
