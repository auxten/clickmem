import { useCallback, useEffect, useState } from "react";

/**
 * Tracks the user's preferred theme. The dashboard ships light-mode by default
 * because the Vaultis-style reference uses a bright canvas; the toggle keeps a
 * dark fallback for late-night reviewers. Theme is persisted to localStorage.
 */
export type ThemeName = "light" | "dark";

const STORAGE_KEY = "CLICKMEM_THEME";

function readInitialTheme(): ThemeName {
  if (typeof window === "undefined") return "light";
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v === "dark" || v === "light") return v;
  } catch {
    /* ignore */
  }
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return prefersDark ? "dark" : "light";
}

export function useTheme(): {
  theme: ThemeName;
  setTheme: (t: ThemeName) => void;
  toggle: () => void;
} {
  const [theme, setThemeState] = useState<ThemeName>(readInitialTheme);

  const setTheme = useCallback((t: ThemeName) => {
    setThemeState(t);
    try {
      window.localStorage.setItem(STORAGE_KEY, t);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    if (theme === "dark") root.classList.add("dark");
    else root.classList.remove("dark");
  }, [theme]);

  return {
    theme,
    setTheme,
    toggle: () => setTheme(theme === "dark" ? "light" : "dark"),
  };
}
