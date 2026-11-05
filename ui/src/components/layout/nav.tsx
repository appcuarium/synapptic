// The navigation items the sidebar shows. The app's own list is the default; a dashboard built on this one
// supplies its list through NavItemsProvider.
import { createContext, type ComponentType, type ReactNode, useContext } from 'react';
import {
  BenchmarkIcon, OverviewIcon, PipelineIcon, ProfileIcon, SessionsIcon, SettingsIcon,
} from '@/components/icons';

export interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType;
  end: boolean;
  bottom?: boolean;   // pinned to the sidebar's foot (Settings, operator 2026-11-05)
}

export const DEFAULT_NAV: NavItem[] = [
  { to: '/', label: 'Overview', Icon: OverviewIcon, end: true },
  { to: '/pipeline', label: 'Pipeline', Icon: PipelineIcon, end: false },
  { to: '/sessions', label: 'Sessions', Icon: SessionsIcon, end: false },
  { to: '/profile', label: 'Profile & Guards', Icon: ProfileIcon, end: false },
  { to: '/benchmark', label: 'Benchmark', Icon: BenchmarkIcon, end: false },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon, end: false, bottom: true },
];

const NavItemsContext = createContext<NavItem[]>(DEFAULT_NAV);

export function NavItemsProvider({ items, children }: { items: NavItem[]; children: ReactNode }) {
  return <NavItemsContext.Provider value={items}>{children}</NavItemsContext.Provider>;
}

export function useNavItems(): NavItem[] {
  return useContext(NavItemsContext);
}

// The name on the top bar: the app's own by default; a dashboard built on this one names itself
// (the author's says "Synapptic Core", operator 2026-11-05).
export const DEFAULT_BRAND = 'Synapptic';
const BrandContext = createContext<string>(DEFAULT_BRAND);

export function BrandProvider({ name, children }: { name: string; children: ReactNode }) {
  return <BrandContext.Provider value={name}>{children}</BrandContext.Provider>;
}

export function useBrand(): string {
  return useContext(BrandContext);
}
