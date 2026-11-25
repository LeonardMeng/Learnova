import { useQuery } from "@tanstack/react-query";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router";
import { api } from "./api/client";
import { QuizPage } from "./pages/QuizPage";
import { ReportsPage } from "./pages/ReportsPage";
import { StatusPage } from "./pages/StatusPage";
import { useWorkspace } from "./workspace";

function WorkspacePicker() {
  const [workspace, setWorkspace] = useWorkspace();
  const { data } = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  return (
    <label className="picker">
      Workspace
      <select value={workspace ?? ""} onChange={(e) => setWorkspace(e.target.value || null)}>
        <option value="">All</option>
        {data?.map((w) => (
          <option key={w.name} value={w.name}>
            {w.name}
            {w.found ? "" : " (not found)"}
          </option>
        ))}
      </select>
    </label>
  );
}

export function App() {
  const { search } = useLocation(); // keep ?workspace= when switching pages
  return (
    <>
      <header className="topbar">
        <span className="brand">Learnova</span>
        <nav>
          <NavLink to={{ pathname: "/quiz", search }}>Quiz</NavLink>
          <NavLink to={{ pathname: "/status", search }}>Status</NavLink>
          <NavLink to={{ pathname: "/reports", search }}>Reports</NavLink>
        </nav>
        <WorkspacePicker />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to={{ pathname: "/quiz", search }} replace />} />
          <Route path="/quiz" element={<QuizPage />} />
          <Route path="/status" element={<StatusPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
    </>
  );
}
