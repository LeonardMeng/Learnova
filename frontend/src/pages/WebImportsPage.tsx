// Workspaces › Web imports: the sites imported into Notion with `learnova web import`.
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorBox, Spinner, when } from "../components/ui";

export function WebImportsPage() {
  const imports = useQuery({ queryKey: ["web-imports"], queryFn: api.webImports });
  return (
    <section>
      <div className="page-head"><h1>Web imports</h1></div>
      <div className="card">
        {imports.isPending && <Spinner label="Loading…" />}
        {imports.isError && <ErrorBox error={imports.error} onRetry={() => imports.refetch()} />}
        {imports.data?.length === 0 && (
          <p className="muted">No web imports yet. Import a site with <code>learnova web import &lt;index-url&gt; --into &lt;page&gt;</code>.</p>
        )}
        {imports.data && imports.data.length > 0 && (
          <table>
            <thead>
              <tr><th>Site</th><th>Articles</th><th>Imported</th><th>Last check</th><th>Notion</th></tr>
            </thead>
            <tbody>
              {imports.data.map((w) => (
                <tr key={w.index_url}>
                  <td><a href={w.index_url} target="_blank" rel="noreferrer">{w.index_url}</a></td>
                  <td>
                    {w.imported} imported
                    {w.new > 0 && <>, <strong>{w.new} new</strong></>}
                    {w.removed > 0 && <>, {w.removed} removed</>}
                  </td>
                  <td className="nowrap">{w.completed_at ? when(w.completed_at) : <span className="muted">not finished</span>}</td>
                  <td className="nowrap">{when(w.last_checked)}</td>
                  <td><a href={w.notion_url} target="_blank" rel="noreferrer">Open in Notion</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="muted small">Run <code>learnova web check</code> to compare a site with what was imported.</p>
      </div>
    </section>
  );
}
