// Workspaces › Issues (open issues, highest priority first, with Fix / Dismiss) and
// Reports › All issues (every status, with Reopen, and the review report files below).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api, type Issue, type IssueKind, type IssueStatus } from "../api/client";
import { ErrorBox, KIND_LABEL, Spinner, when, WorkspacePicker } from "../components/ui";
import { useWorkspace } from "../workspace";
import { ReportFiles } from "./ReportFiles";

export function IssuesPage() {
  return (
    <section>
      <div className="page-head">
        <h1>Issues</h1>
        <WorkspacePicker />
      </div>
      <IssueList initialStatus="open" />
    </section>
  );
}

export function AllIssuesPage() {
  return (
    <section>
      <div className="page-head">
        <h1>All issues</h1>
        <WorkspacePicker />
      </div>
      <IssueList initialStatus="all" statusFilter />
      <ReportFiles />
    </section>
  );
}

type StatusChoice = IssueStatus | "all";

function IssueList({ initialStatus, statusFilter = false }: { initialStatus: StatusChoice; statusFilter?: boolean }) {
  const [workspace] = useWorkspace();
  const [status, setStatus] = useState<StatusChoice>(initialStatus);
  const [kind, setKind] = useState<IssueKind | "">("");
  const queryClient = useQueryClient();
  const key = ["issues", workspace, status, kind || null];
  const issues = useQuery({
    queryKey: key,
    queryFn: () => api.issues({ workspace, status, kind: kind || null }),
  });
  const change = useMutation({
    mutationFn: ({ id, to }: { id: string; to: "open" | "fixed" | "dismissed" }) => api.setIssueStatus(id, to),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["issues"] });
      queryClient.invalidateQueries({ queryKey: ["tree"] });
      queryClient.invalidateQueries({ queryKey: ["point"] });
    },
  });

  return (
    <div className="card">
      <div className="toolbar">
        <label className="field inline">
          Kind
          <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value as IssueKind | "")}>
            <option value="">all</option>
            {(Object.keys(KIND_LABEL) as IssueKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select>
        </label>
        {statusFilter && (
          <label className="field inline">
            Status
            <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value as StatusChoice)}>
              {["all", "open", "fixed", "dismissed", "obsolete"].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        )}
        {issues.data && <span className="muted">{issues.data.length} issue(s)</span>}
      </div>
      {issues.isPending && <Spinner label="Loading…" />}
      {issues.isError && <ErrorBox error={issues.error} onRetry={() => issues.refetch()} />}
      {change.isError && <ErrorBox error={change.error} />}
      {issues.data?.length === 0 && <p className="muted">No issues here.</p>}
      {issues.data && issues.data.length > 0 && (
        <ol className="issues">
          {issues.data.map((i) => (
            <IssueRow key={i.id} issue={i} busy={change.isPending} onChange={(to) => change.mutate({ id: i.id, to })} />
          ))}
        </ol>
      )}
    </div>
  );
}

function IssueRow({ issue, busy, onChange }: {
  issue: Issue;
  busy: boolean;
  onChange: (to: "open" | "fixed" | "dismissed") => void;
}) {
  return (
    <li className={`issue issue-${issue.status}`} aria-label={`${KIND_LABEL[issue.kind]}: ${issue.problem}`}>
      <div className="issue-head">
        <div className="issue-title">
          <strong>{KIND_LABEL[issue.kind]}</strong>
          <span className="muted">
            {issue.workspace ? `${issue.workspace}: ` : ""}{issue.note_path.join(" / ")} · priority {issue.priority}
            {issue.status !== "open" && <> · {issue.status} {when(issue.closed_at)}</>}
          </span>
        </div>
        <div className="issue-actions">
          {issue.url && <a href={issue.url} target="_blank" rel="noreferrer">Open in Notion</a>}
          {issue.status === "open" && (
            <>
              <button className="secondary small" disabled={busy} onClick={() => onChange("fixed")}>Fix</button>
              <button className="secondary small" disabled={busy} onClick={() => onChange("dismissed")}>Dismiss</button>
            </>
          )}
          {(issue.status === "fixed" || issue.status === "dismissed") && (
            <button className="secondary small" disabled={busy} onClick={() => onChange("open")}>Reopen</button>
          )}
        </div>
      </div>
      {issue.kind === "missing" ? <p>Missing: <strong>{issue.topic}</strong></p> : <blockquote>{issue.quote}</blockquote>}
      <p>{issue.problem}</p>
      {issue.point && <p className="muted small">Point: <Link to={`/?point=${issue.point.id}`}>{issue.point.name}</Link></p>}
    </li>
  );
}
