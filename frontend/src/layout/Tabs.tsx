// The row of open pages above the main area. Opening a page (from the sidebar or a link) adds a
// tab, or switches to its tab; × closes it. Each tab remembers its last URL (filters, selected
// point), and the tabs are kept in this browser between visits.
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";

export type Tab = { path: string; to: string };
const KEY = "learnova.tabs";

function load(): Tab[] {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(saved) ? saved.filter((t) => typeof t?.path === "string") : [];
  } catch {
    return [];
  }
}

function save(tabs: Tab[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(tabs));
  } catch {
    // storage unavailable (private mode): tabs just aren't remembered
  }
}

export function Tabs({ titleFor }: { titleFor: (path: string) => string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [tabs, setTabs] = useState<Tab[]>(load);
  const current = location.pathname;
  const to = location.pathname + location.search;

  useEffect(() => {
    if (current === "/status" || current === "/sync") return; // old addresses that redirect
    setTabs((prev) => {
      const i = prev.findIndex((t) => t.path === current);
      const next = i < 0 ? [...prev, { path: current, to }] : prev.map((t, j) => (j === i ? { ...t, to } : t));
      save(next);
      return next;
    });
  }, [current, to]);

  const close = (path: string) => {
    const i = tabs.findIndex((t) => t.path === path);
    const next = tabs.filter((t) => t.path !== path);
    setTabs(next);
    save(next);
    if (path === current) {
      const neighbour = next[Math.min(i, next.length - 1)];
      navigate(neighbour ? neighbour.to : "/");
    }
  };

  return (
    <div className="tabs" role="tablist" aria-label="Open pages">
      {tabs.map((t) => (
        <span key={t.path} className={`tab ${t.path === current ? "active" : ""}`}>
          <button
            role="tab"
            aria-selected={t.path === current}
            className="tab-title"
            onClick={() => navigate(t.to)}
          >
            {titleFor(t.path)}
          </button>
          <button className="tab-close" aria-label={`Close ${titleFor(t.path)}`} onClick={() => close(t.path)}>
            ×
          </button>
        </span>
      ))}
    </div>
  );
}
