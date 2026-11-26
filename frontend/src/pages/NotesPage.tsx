// Workspaces › <workspace>: the workspace's Notion page tree (with the pages' icons), and the
// selected note's knowledge points and issues (`?note=`).
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, type Note } from "../api/client";
import { ErrorBox, IssueBadge, KIND_LABEL, MasteryBar, PageIcon, Spinner, when } from "../components/ui";

type Node = { note: Note; children: Node[] };

function buildTree(notes: Note[]): Node[] {
  const byPath = new Map<string, Node>();
  const roots: Node[] = [];
  for (const note of notes) {
    const node: Node = { note, children: [] };
    byPath.set(note.path.join("\u0000"), node);
    const parent = byPath.get(note.path.slice(0, -1).join("\u0000"));
    if (note.path.length > 1 && parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

export function NotesPage() {
  const { name = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const selected = params.get("note");
  const notes = useQuery({ queryKey: ["notes", name], queryFn: () => api.notes(name) });
  const tree = useMemo(() => buildTree(notes.data ?? []), [notes.data]);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const select = (id: string) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("note", id);
      return p;
    });
  const toggle = (id: string) => {
    const next = new Set(closed);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setClosed(next);
  };

  const render = (nodes: Node[], depth: number): React.ReactNode =>
    nodes.map(({ note, children }) => (
      <li key={note.id}>
        <div className={`tree-row ${note.id === selected ? "selected" : ""} ${note.ignored ? "ignored" : ""}`}
             style={{ paddingLeft: depth * 18 }}>
          {children.length ? (
            <button className="twisty" aria-label={`${closed.has(note.id) ? "Open" : "Close"} ${note.title}`}
                    onClick={() => toggle(note.id)}>
              {closed.has(note.id) ? "▸" : "▾"}
            </button>
          ) : (
            <span className="twisty" />
          )}
          <PageIcon icon={note.icon} />
          <button className="link" onClick={() => select(note.id)}>{note.title}</button>
          <span className="row-meta">
            <span className="muted small meta-points"
                  title={note.ignored ? "Hidden by .learnovaignore" : note.points > 0 ? "Knowledge points" : undefined}>
              {note.ignored ? "ignored" : note.points > 0 ? `${note.points} pt` : ""}
            </span>
            <span className="meta-issues"><IssueBadge count={note.open_issues} /></span>
          </span>
        </div>
        {children.length > 0 && !closed.has(note.id) && <ul className="tree">{render(children, depth + 1)}</ul>}
      </li>
    ));

  return (
    <section>
      <div className="page-head">
        <h1>{name}</h1>
        <Link to={`/management?workspace=${encodeURIComponent(name)}`}>Management</Link>
      </div>
      <div className="split">
        <div className="card tree-card">
          {notes.isPending && <Spinner label="Loading…" />}
          {notes.isError && <ErrorBox error={notes.error} onRetry={() => notes.refetch()} />}
          {notes.data?.length === 0 && <p className="muted">No notes yet. Sync this workspace first.</p>}
          {tree.length > 0 && <ul className="tree" aria-label="Notes">{render(tree, 0)}</ul>}
        </div>
        <div className="detail-column">
          {selected ? <NoteDetail workspace={name} id={selected} /> : <p className="muted card">Select a page.</p>}
        </div>
      </div>
    </section>
  );
}

function NoteDetail({ workspace, id }: { workspace: string; id: string }) {
  const note = useQuery({ queryKey: ["note", workspace, id], queryFn: () => api.note(workspace, id) });
  if (note.isPending) return <div className="card"><Spinner label="Loading…" /></div>;
  if (note.isError) return <div className="card"><ErrorBox error={note.error} /></div>;
  const n = note.data;
  return (
    <article className="card detail" aria-label={`Details of ${n.title}`}>
      <div className="card-head">
        <h2><PageIcon icon={n.icon} /> {n.title}</h2>
        <a href={n.notion_url} target="_blank" rel="noreferrer">Open in Notion</a>
      </div>
      <p className="muted">
        {n.path.join(" / ")} · edited {when(n.last_edited)} · {n.reviewed ? "reviewed" : "not reviewed since its last change"}
        {n.ignored && " · ignored by .learnovaignore"}
      </p>
      <h3>Knowledge points ({n.knowledge_points.length})</h3>
      {n.knowledge_points.length ? (
        <ul className="plain">
          {n.knowledge_points.map((p) => (
            <li key={p.id}>
              <Link to={`/?point=${p.id}`}>{p.name}</Link> <MasteryBar value={p.mastery} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None yet. Run a review.</p>
      )}
      <h3>Issues</h3>
      {n.issues.length ? (
        <ul className="plain">
          {n.issues.map((i) => (
            <li key={i.id} className={i.status === "open" ? "" : "muted"}>
              <strong>{KIND_LABEL[i.kind]}</strong> ({i.status}): {i.kind === "missing" ? i.topic : `“${i.quote}”`} — {i.problem}
              {i.url && i.status === "open" && <> · <a href={i.url} target="_blank" rel="noreferrer">Open in Notion</a></>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None.</p>
      )}
      <p><Link to={`/issues?workspace=${encodeURIComponent(workspace)}`}>Fix or dismiss on the Issues page</Link></p>
    </article>
  );
}
