import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router";
import { api } from "./api/client";
import { ThemeToggle } from "./components/ThemeToggle";
import { Tabs } from "./layout/Tabs";
import { AllIssuesPage, IssuesPage } from "./pages/IssuesPage";
import { AllPointsPage } from "./pages/AllPointsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { NotesPage } from "./pages/NotesPage";
import { QuizPage } from "./pages/QuizPage";
import { ManagementPage } from "./pages/ManagementPage";
import { WebImportsPage } from "./pages/WebImportsPage";

/** The title of the page at a path (sidebar entries and tabs). */
export function titleFor(pathname: string): string {
  const fixed: Record<string, string> = {
    "/": "Overview",
    "/points": "All points",
    "/quiz": "Quiz",
    "/history": "History",
    "/issues": "Issues",
    "/management": "Management",
    "/web-imports": "Web imports",
    "/reports": "All issues",
  };
  if (fixed[pathname]) return fixed[pathname];
  const ws = pathname.match(/^\/workspaces\/([^/]+)$/);
  return ws ? decodeURIComponent(ws[1]) : "Page";
}

function SidebarSearch() {
  const { pathname } = useLocation();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const value = pathname === "/" ? (params.get("q") ?? "") : "";
  const change = (q: string) => {
    if (pathname === "/") {
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (q) p.set("q", q);
          else p.delete("q");
          return p;
        },
        { replace: true },
      );
    } else {
      navigate(q ? `/?q=${encodeURIComponent(q)}` : "/");
    }
  };
  return (
    <input
      className="sidebar-search"
      type="search"
      placeholder="🔍 Search points…"
      aria-label="Search points"
      value={value}
      onChange={(e) => change(e.target.value)}
    />
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="nav-group">
      <button className="nav-group-title" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {title}
      </button>
      {open && <div className="nav-items">{children}</div>}
    </div>
  );
}

function Item({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink to={to} end className="nav-item">
      {children}
    </NavLink>
  );
}

function Sidebar() {
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  return (
    <aside className="sidebar">
      <div className="logo">🤖 Learnova</div>
      <SidebarSearch />
      <nav aria-label="Main">
        <Group title="Knowledge">
          <Item to="/">Overview</Item>
          <Item to="/points">All points</Item>
        </Group>
        <Group title="Workspaces">
          <Item to="/quiz">Quiz</Item>
          <Item to="/history">History</Item>
          <Item to="/issues">Issues</Item>
          <Item to="/management">Management</Item>
          {workspaces.data?.map((w) => (
            <Item key={w.name} to={`/workspaces/${encodeURIComponent(w.name)}`}>
              <span className="nav-icon" aria-hidden="true">📁</span> {w.name}
            </Item>
          ))}
          <Item to="/web-imports">Web imports</Item>
        </Group>
        <Group title="Reports">
          <Item to="/reports">All issues</Item>
        </Group>
      </nav>
    </aside>
  );
}

function UserName() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  if (!me.data) return null;
  return (
    <span className="user" title="The local user (there are no accounts yet)">
      <span className="avatar" aria-hidden="true">
        {me.data.name.slice(0, 1).toUpperCase()}
      </span>
      {me.data.name}
    </span>
  );
}

export function App() {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main-column">
        <header className="topbar">
          <span className="spacer" />
          <ThemeToggle />
          <UserName />
        </header>
        <Tabs titleFor={titleFor} />
        <main>
          <Routes>
            <Route path="/" element={<KnowledgePage />} />
            <Route path="/points" element={<AllPointsPage />} />
            <Route path="/quiz" element={<QuizPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/issues" element={<IssuesPage />} />
            <Route path="/management" element={<ManagementPage />} />
            <Route path="/sync" element={<Navigate to="/management" replace />} />
            <Route path="/workspaces/:name" element={<NotesPage />} />
            <Route path="/web-imports" element={<WebImportsPage />} />
            <Route path="/reports" element={<AllIssuesPage />} />
            <Route path="/status" element={<Navigate to="/" replace />} />
            <Route path="*" element={<p>Page not found.</p>} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
