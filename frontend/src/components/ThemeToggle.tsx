// The top-bar theme button: System → Light → Dark → System. The choice is kept in the browser
// and applied to <html data-theme>, so the whole page and the native form controls follow it.
import { useEffect, useState } from "react";
import {
  applyTheme,
  loadTheme,
  nextTheme,
  saveTheme,
  THEME_ICON,
  THEME_LABEL,
  watchSystemTheme,
  type ThemePref,
} from "../theme";

export function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>(loadTheme);
  useEffect(() => {
    applyTheme(pref);
    return pref === "system" ? watchSystemTheme(() => applyTheme("system")) : undefined;
  }, [pref]);

  const next = nextTheme(pref);
  return (
    <button
      type="button"
      className="theme-toggle small"
      aria-label={`Theme: ${THEME_LABEL[pref]}`}
      title={`Theme: ${THEME_LABEL[pref]} — click for ${THEME_LABEL[next].toLowerCase()}`}
      onClick={() => {
        saveTheme(next);
        setPref(next);
        applyTheme(next);
      }}
    >
      <span aria-hidden="true">{THEME_ICON[pref]}</span> {THEME_LABEL[pref]}
    </button>
  );
}
