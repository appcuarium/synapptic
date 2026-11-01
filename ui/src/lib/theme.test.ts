import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

// jsdom has no matchMedia: an OS colour-scheme query we can flip, installed before theme.ts loads.
const os = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  const query = {
    matches: true,
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
  };
  window.matchMedia = (() => query) as unknown as typeof window.matchMedia;
  return {
    setDark(dark: boolean) {
      query.matches = dark;
      listeners.forEach((listener) => listener());
    },
  };
});

const { setThemeChoice, useResolvedTheme } = await import('./theme');

it('follows the OS while the choice stays "system" (tile colours read the theme shown, not the choice)', () => {
  setThemeChoice('system');
  const { result } = renderHook(() => useResolvedTheme());
  expect(result.current).toBe('dark');
  act(() => os.setDark(false));
  expect(document.documentElement.dataset.theme).toBe('light');
  expect(result.current).toBe('light');
});
