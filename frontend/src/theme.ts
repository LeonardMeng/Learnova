// The colour theme: System follows the computer, Light and Dark are fixed. The choice is kept
// in this browser, and the resolved theme is written to <html data-theme> so the CSS can pick
// its colours without asking the OS again.
export type ThemePref = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const KEY = "learnova.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

export const THEME_ORDER: ThemePref[] = ["system", "light", "dark"];

export const THEME_LABEL: Record<ThemePref, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

export const THEME_ICON: Record<ThemePref, string> = {
  system: "🖥️",
  light: "☀️",
  dark: "🌙",
};

export function loadTheme(): ThemePref {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
  } catch {
    return "system";
  }
}

export function saveTheme(pref: ThemePref): void {
  try {
    localStorage.setItem(KEY, pref);
  } catch {
    // storage unavailable (private mode): the choice lasts for this page only
  }
}

export function prefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

export function resolvedTheme(pref: ThemePref): ResolvedTheme {
  return pref === "system" ? (prefersDark() ? "dark" : "light") : pref;
}

/** Write the resolved theme to <html data-theme>; CSS uses that attribute. */
export function applyTheme(pref: ThemePref): ResolvedTheme {
  const theme = resolvedTheme(pref);
  document.documentElement.dataset.theme = theme;
  return theme;
}

/** The next preference when the top-bar button is clicked: System → Light → Dark → System. */
export function nextTheme(pref: ThemePref): ThemePref {
  return THEME_ORDER[(THEME_ORDER.indexOf(pref) + 1) % THEME_ORDER.length];
}

/** Call `onChange` when the computer's appearance changes (used while the choice is System). */
export function watchSystemTheme(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(DARK_QUERY);
  const listener = () => onChange();
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
