// Workspaces › Management: every workspace with its own Sync and Review buttons (and, under
// "More", re-download all / review all / make default), and adding a new workspace (check the
// Notion page, then create it with an empty .learnovaignore).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router";
import { api, type NotionCheck, type ReviewResult, type SyncResult, type Workspace } from "../api/client";
import { ErrorBox, Spinner } from "../components/ui";

export function ManagementPage() {
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<string | null>(null);

  if (workspaces.isPending) return <Spinner label="Loading…" />;
  if (workspaces.isError) return <ErrorBox error={workspaces.error} onRetry={() => workspaces.refetch()} />;
  return (
    <section>
      <div className="page-head">
        <h1>Management</h1>
      </div>
      {created && (
        <div className="card notice">
          <strong>Created workspace {created}</strong> with an empty <code>.learnovaignore</code> (no pages left
          out). Press <em>Sync</em> on its row to download its notes, then <em>Review</em>.
        </div>
      )}
      <div className="card">
        <div className="card-head">
          <h2>Workspaces</h2>
          <button className="secondary" aria-expanded={adding} onClick={() => setAdding(!adding)}>
            Add new workspace
          </button>
        </div>
        {adding && (
          <AddWorkspace
            onCreated={(name) => {
              setAdding(false);
              setCreated(name);
            }}
            onCancel={() => setAdding(false)}
          />
        )}
        {workspaces.data.length === 0 ? (
          <p className="muted">No workspaces yet.</p>
        ) : (
          <table className="workspaces">
            <thead><tr><th>Name</th><th>Folder</th><th>Actions</th></tr></thead>
            <tbody>
              {workspaces.data.map((w) => <WorkspaceRow key={w.name} workspace={w} />)}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

/** One workspace: Sync and Review, the rarer actions under "More", and the last result below.
 * Each row runs on its own, so two workspaces can sync at the same time. */
function WorkspaceRow({ workspace: w }: { workspace: Workspace }) {
  const queryClient = useQueryClient();
  const refresh = () => {
    for (const key of ["reports", "points", "tree", "status", "issues", "notes", "point", "workspaces"]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
  const sync = useMutation({ mutationFn: (full: boolean) => api.sync(w.name, full), onSuccess: refresh });
  const review = useMutation({ mutationFn: (all: boolean) => api.review(w.name, all), onSuccess: refresh });
  const makeDefault = useMutation({ mutationFn: () => api.setDefault(w.name), onSuccess: refresh });
  const [last, setLast] = useState<"sync" | "review" | null>(null);
  const running = sync.isPending || review.isPending;
  const closeMenu = (e: React.MouseEvent) => {
    const menu = e.currentTarget.closest("details");
    if (menu) menu.open = false;
  };
  const run = (what: "sync" | "review", everything: boolean) => {
    setLast(what);
    if (what === "sync") sync.mutate(everything);
    else review.mutate(everything);
  };
  const current = last === "sync" ? sync : last === "review" ? review : null;
  const hasResult = running || current?.isError || current?.data || makeDefault.isError;

  return (
    <Fragment>
      <tr aria-label={`Workspace ${w.name}`}>
        <td>
          <Link to={`/workspaces/${encodeURIComponent(w.name)}`}>{w.name}</Link>
          {w.is_default && <span className="muted"> (default)</span>}
        </td>
        <td className="muted">{w.path}{w.found ? "" : " (not found)"}</td>
        <td className="row-actions">
          <button className="small" disabled={running} onClick={() => run("sync", false)}>Sync</button>
          <button className="small" disabled={running} onClick={() => run("review", false)}>Review</button>
          <details className="more">
            <summary aria-label={`More actions for ${w.name}`}>More ▾</summary>
            <div className="more-menu">
              <button className="link" disabled={running} onClick={(e) => { closeMenu(e); run("sync", true); }}>Re-download all</button>
              <span className="muted small">Fetch every page again, even unchanged ones (after upgrading, or if a note looks wrong).</span>
              <button className="link" disabled={running} onClick={(e) => { closeMenu(e); run("review", true); }}>Review all notes</button>
              <span className="muted small">Review unchanged notes too (after changing the model or prompts).</span>
              {!w.is_default && (
                <button className="link" disabled={makeDefault.isPending} onClick={(e) => { closeMenu(e); makeDefault.mutate(); }}>
                  Make default
                </button>
              )}
            </div>
          </details>
        </td>
      </tr>
      {hasResult && (
        <tr className="row-result">
          <td colSpan={3}>
            {sync.isPending && <Spinner label="Syncing… this can take a minute." />}
            {review.isPending && <Spinner label="Reviewing… the model reads every changed note." />}
            {!running && current?.isError && <ErrorBox error={current.error} />}
            {!running && last === "sync" && sync.data && <SyncSummary result={sync.data} />}
            {!running && last === "review" && review.data && <ReviewSummary workspace={w.name} result={review.data} />}
            {makeDefault.isError && <ErrorBox error={makeDefault.error} />}
          </td>
        </tr>
      )}
    </Fragment>
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
      className="add-workspace"
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

function SyncSummary({ result }: { result: SyncResult }) {
  const c = result.counts;
  return (
    <div className="notice result">
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
    return <div className="notice result">Nothing to review: no note changed since its last review. Sync first, or use More › Review all notes.</div>;
  }
  return (
    <div className="notice result">
      <strong>Reviewed {result.reviewed} note(s).</strong> {result.new_points} new point(s),{" "}
      {result.issues + result.missing_topics} new issue(s), {result.fixed} fixed, {result.open} open
      {result.possible_duplicates > 0 && <>, {result.possible_duplicates} possible duplicate(s)</>}.{" "}
      {result.report && <Link to={`/reports?workspace=${encodeURIComponent(workspace)}&report=${encodeURIComponent(result.report)}`}>Open the report</Link>}
      {result.failed.length > 0 && <p className="bad">{result.failed.length} note(s) failed and will be retried.</p>}
    </div>
  );
}
