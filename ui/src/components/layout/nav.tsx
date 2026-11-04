// The navigation items the sidebar shows. The app's own list is the default; a dashboard built on this one
// supplies its list through NavItemsProvider.
import { createContext, type ComponentType, type ReactNode, useContext } from 'react';
import {
  OverviewIcon, PipelineIcon, ProfileIcon, SessionsIcon, SettingsIcon,
} from '@/components/icons';

export interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType;
  end: boolean;
}

export const DEFAULT_NAV: NavItem[] = [
  { to: '/', label: 'Overview', Icon: OverviewIcon, end: true },
  { to: '/pipeline', label: 'Pipeline', Icon: PipelineIcon, end: false },
  { to: '/sessions', label: 'Sessions', Icon: SessionsIcon, end: false },
  { to: '/profile', label: 'Profile & Guards', Icon: ProfileIcon, end: false },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon, end: false },
];

const NavItemsContext = createContext<NavItem[]>(DEFAULT_NAV);

export function NavItemsProvider({ items, children }: { items: NavItem[]; children: ReactNode }) {
  return <NavItemsContext.Provider value={items}>{children}</NavItemsContext.Provider>;
}

export function useNavItems(): NavItem[] {
  return useContext(NavItemsContext);
}
