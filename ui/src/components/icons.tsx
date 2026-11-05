// The mockup's icons, path for path (16px nav icons, 14px control icons, 22px logo).
import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, strokeWidth = 1.8, children, ...rest }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" aria-hidden="true" {...rest}>
      {children}
    </svg>
  );
}

export const LogoIcon = (p: IconProps) => (
  <Svg size={22} {...p}>
    <circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" />
    <path d="M8.3 11l7.4-3.6M8.3 13l7.4 3.6" />
  </Svg>
);
// Machine's restart arrow (the circular arrow at the bottom right of its pages), drawn larger than the
// theme chips' icons so it reads as the one action on the bar (operator: "very small" at the chip size).
export const RestartIcon = (p: IconProps) => (
  <Svg size={20} strokeWidth={2} {...p}><path d="M20 12a8 8 0 11-2.3-5.7" /><path d="M20 4v5h-5" /></Svg>
);
export const OverviewIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="3" width="7" height="9" /><rect x="14" y="3" width="7" height="5" />
    <rect x="14" y="12" width="7" height="9" /><rect x="3" y="16" width="7" height="5" />
  </Svg>
);
export const PipelineIcon = (p: IconProps) => (
  <Svg {...p}><path d="M4 6h10M4 12h16M4 18h7" /><circle cx="17" cy="6" r="2" /><circle cx="14" cy="18" r="2" /></Svg>
);
export const SessionsIcon = (p: IconProps) => (
  <Svg {...p}><path d="M21 12a8 8 0 01-11.6 7.1L4 20l1-4.6A8 8 0 1121 12z" /></Svg>
);
export const ProfileIcon = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z" /></Svg>
);
// A gauge: the benchmark's dial.
export const BenchmarkIcon = (p: IconProps) => (
  <Svg {...p}><path d="M4 17a8 8 0 1116 0" /><path d="M12 17l4-6" /><circle cx="12" cy="17" r="1.5" /></Svg>
);
export const SettingsIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" />
  </Svg>
);
export const SunIcon = (p: IconProps) => (
  <Svg size={14} strokeWidth={2} {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Svg>
);
export const MonitorIcon = (p: IconProps) => (
  <Svg size={14} strokeWidth={2} {...p}><rect x="3" y="4" width="18" height="12" rx="1" /><path d="M8 20h8M12 16v4" /></Svg>
);
export const MoonIcon = (p: IconProps) => (
  <Svg size={14} strokeWidth={2} {...p}><path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z" /></Svg>
);
export const MenuIcon = (p: IconProps) => (
  <Svg size={20} strokeWidth={2} {...p}><path d="M4 6h16M4 12h16M4 18h16" /></Svg>
);
