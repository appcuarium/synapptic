// Light / System / Dark; dark when nothing is stored. 'system' follows the OS live.
// index.html applies the stored theme before first paint; this module keeps it in step afterwards.
import { useCallback, useSyncExternalStore } from 'react';

export type ThemeChoice = 'light' | 'system' | 'dark';

const STORAGE_KEY = 'synapptic-theme';
export const DEFAULT_THEME: ThemeChoice = 'dark';
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
const listeners = new Set<() => void>();

function storedChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' || value === 'system' ? value : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

function applyTheme(): void {
  const choice = storedChoice();
  const dark = choice === 'system' ? darkQuery.matches : choice === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setThemeChoice(choice: ThemeChoice): void {
  try {
    localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    // storage blocked: the theme still applies for this page view
  }
  applyTheme();
}

function shownTheme(): 'light' | 'dark' {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

// 'system' follows the OS for as long as the page is open, whichever components are mounted.
darkQuery.addEventListener('change', applyTheme);

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const choice = useSyncExternalStore(subscribe, storedChoice);
  const set = useCallback((next: ThemeChoice) => setThemeChoice(next), []);
  return [choice, set];
}

/** The theme on screen. Re-renders when it changes, also while the choice stays 'system' (colours read from tokens). */
export function useResolvedTheme(): 'light' | 'dark' {
  return useSyncExternalStore(subscribe, shownTheme);
}
