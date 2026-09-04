/**
 * Inline SVG icon set for the admin console (AD-01-12).
 *
 * Lucide is banned from admin pages ("no Lucide" in the spec), so the small
 * set of glyphs the admin UI needs is defined here as hand-drawn 24×24
 * stroke icons. Each icon accepts `size` and `strokeWidth` so call sites
 * keep the same ergonomics they had with lucide-react.
 */

import type { CSSProperties, ReactElement } from "react";

export interface AdminIconProps {
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: CSSProperties;
}

function Icon({
  size = 16,
  strokeWidth = 1.75,
  className,
  style,
  children,
}: AdminIconProps & { children: ReactElement | ReactElement[] }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
      style={style}
    >
      {children}
    </svg>
  );
}

/* ── Sidebar section glyphs ───────────────────────────────────────────── */

export function TodayIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <path d="M8 2v4M16 2v4M3 9h18" />
      <path d="M12 13v4M10 15h4" />
    </Icon>
  );
}

export function DataQualityIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M3 20h18" />
      <path d="M6 20v-7M11 20V8M16 20v-11M21 20V4" />
    </Icon>
  );
}

export function OperationsIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" />
    </Icon>
  );
}

export function CustomersIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.8-3.4 3.4-5 6.5-5s5.7 1.6 6.5 5" />
      <circle cx="17.5" cy="9" r="2.5" />
      <path d="M17 14.6c2.3.3 3.9 1.7 4.5 4.4" />
    </Icon>
  );
}

export function AgentsIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <rect x="5" y="8" width="14" height="11" rx="2.5" />
      <path d="M12 8V4M9 4h6" />
      <circle cx="9.5" cy="13" r="1" />
      <circle cx="14.5" cy="13" r="1" />
      <path d="M9.5 16.5h5" />
    </Icon>
  );
}

/* ── Topbar glyphs ────────────────────────────────────────────────────── */

export function SearchIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </Icon>
  );
}

export function SignOutIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M14 4h-8a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h8" />
      <path d="M10 12h11M18 8l3 4-3 4" />
    </Icon>
  );
}

/* ── Glyphs used inside admin pages (replacing lucide-react) ──────────── */

export function SendIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M21 3 10 14" />
      <path d="M21 3 14 21l-4-7-7-4Z" />
    </Icon>
  );
}

export function SpinnerIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M21 12a9 9 0 1 1-6.2-8.56" />
    </Icon>
  );
}

export function RefreshIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      <path d="M21 3v6h-6" />
    </Icon>
  );
}

export function CheckIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="m4 12.5 5 5L20 6.5" />
    </Icon>
  );
}

export function XIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M6 6l12 12M18 6 6 18" />
    </Icon>
  );
}

export function AlertTriangleIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M12 3 2.5 20h19L12 3Z" />
      <path d="M12 10v4M12 17.5h.01" />
    </Icon>
  );
}

export function CheckCircleIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.5 2.5 2.5 5-5.5" />
    </Icon>
  );
}

export function ClockIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.5 2" />
    </Icon>
  );
}

export function ChevronDownIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="m5 9 7 7 7-7" />
    </Icon>
  );
}

export function ChevronRightIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="m9 5 7 7-7 7" />
    </Icon>
  );
}

export function ChevronLeftIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="m15 5-7 7 7 7" />
    </Icon>
  );
}

export function ArrowLeftIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M20 12H4M10 6l-6 6 6 6" />
    </Icon>
  );
}

export function LockIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <rect x="4.5" y="10" width="15" height="10.5" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      <path d="M12 14.5v2" />
    </Icon>
  );
}

export function DatabaseIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <ellipse cx="12" cy="5.5" rx="8" ry="2.8" />
      <path d="M4 5.5v13c0 1.55 3.58 2.8 8 2.8s8-1.25 8-2.8v-13" />
      <path d="M4 12c0 1.55 3.58 2.8 8 2.8s8-1.25 8-2.8" />
    </Icon>
  );
}

export function MessageSquareIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9l-5 4V6a1 1 0 0 1 1-1Z" />
    </Icon>
  );
}

export function PlusIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function TrashIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
      <path d="M6.5 7 7.5 20a1 1 0 0 0 1 .9h7a1 1 0 0 0 1-.9L17.5 7" />
      <path d="M10 11v6M14 11v6" />
    </Icon>
  );
}

export function PanelLeftIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9.5 4v16" />
    </Icon>
  );
}

export function BotIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <rect x="5" y="8" width="14" height="11" rx="2.5" />
      <path d="M12 8V4M9 4h6" />
      <circle cx="9.5" cy="13" r="1" />
      <circle cx="14.5" cy="13" r="1" />
    </Icon>
  );
}

export function UserIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4.5 20.5c1-4 3.9-6 7.5-6s6.5 2 7.5 6" />
    </Icon>
  );
}

export function OrbitIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.8 9.6c1.6 2.2.5 5.7-3.1 8.7-3.7 3-8.1 3.7-9.7 1.5" />
      <path d="M4.2 14.4c-1.6-2.2-.5-5.7 3.1-8.7 3.7-3 8.1-3.7 9.7-1.5" />
      <circle cx="19.5" cy="5.5" r="1.4" />
    </Icon>
  );
}

export function CreditCardIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <rect x="2.5" y="5" width="19" height="14" rx="2" />
      <path d="M2.5 10h19M6 15h4" />
    </Icon>
  );
}

export function GitBranchIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="9" r="2.5" />
      <path d="M6 8.5v7" />
      <path d="M18 11.5c0 4-5 4-9.2 4.9" />
    </Icon>
  );
}

export function ShieldAlertIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M12 2.5 4.5 5.5v6c0 4.7 3.2 8.4 7.5 10 4.3-1.6 7.5-5.3 7.5-10v-6L12 2.5Z" />
      <path d="M12 8.5v4M12 15.5h.01" />
    </Icon>
  );
}

export function UsersIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.8-3.4 3.4-5 6.5-5s5.7 1.6 6.5 5" />
      <circle cx="17.5" cy="9" r="2.5" />
      <path d="M17 14.6c2.3.3 3.9 1.7 4.5 4.4" />
    </Icon>
  );
}

export function ExternalLinkIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
    </Icon>
  );
}

export function ArrowLeftRightIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M4 7h13M13 3l4 4-4 4" />
      <path d="M20 17H7M11 21l-4-4 4-4" />
    </Icon>
  );
}

export function FlagIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M5 21V4" />
      <path d="M5 5c4-2 7 2 11 0v8c-4 2-7-2-11 0" />
    </Icon>
  );
}

export function GitMergeIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="14" r="2.5" />
      <path d="M6 8.5v7" />
      <path d="M18 11.5c0-3-4-3.4-8.2-4.4" />
    </Icon>
  );
}

export function PauseIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M9 5v14M15 5v14" />
    </Icon>
  );
}

export function RotateCcwIcon(p: AdminIconProps) {
  return (
    <Icon {...p}>
      <path d="M3 12a9 9 0 1 0 2.64-6.36" />
      <path d="M3 3v6h6" />
    </Icon>
  );
}

export function TagIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42l-8.704-8.704z" />
      <circle cx="7.5" cy="7.5" r=".5" fill="currentColor" />
    </Icon>
  );
}

export function WalletIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
      <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
      <path d="M18 12a2 2 0 0 0 0 4h4v-4Z" />
    </Icon>
  );
}

export function AnchorIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M12 22V8" />
      <path d="M5 12H2a10 10 0 0 0 20 0h-3" />
      <circle cx="12" cy="5" r="3" />
    </Icon>
  );
}

export function WavesIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
      <path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
      <path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
    </Icon>
  );
}

export function ListChecksIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="m3 17 2 2 4-4" />
      <path d="m3 7 2 2 4-4" />
      <path d="M13 6h8" />
      <path d="M13 12h8" />
      <path d="M13 18h8" />
    </Icon>
  );
}

export function MinusIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M5 12h14" />
    </Icon>
  );
}

export function SkipForwardIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <polygon points="5 4 15 12 5 20 5 4" />
      <line x1="19" x2="19" y1="5" y2="19" />
    </Icon>
  );
}

export function HistoryIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l4 2" />
    </Icon>
  );
}

export function PanelLeftCloseIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
      <path d="M9 3v18" />
      <path d="m16 15-3-3 3-3" />
    </Icon>
  );
}

export function PanelLeftOpenIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
      <path d="M9 3v18" />
      <path d="m14 9 3 3-3 3" />
    </Icon>
  );
}

export function SendIconAlt(props: AdminIconProps) {
  // Same as SendIcon but some components might import it as Send
  return <SendIcon {...props} />;
}

export function FileTextIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v4a2 2 0 0 0 2 2h4" />
      <path d="M10 9H8" />
      <path d="M16 13H8" />
      <path d="M16 17H8" />
    </Icon>
  );
}

export function RotateCwIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
    </Icon>
  );
}

export function ShipIcon(props: AdminIconProps) {
  return (
    <Icon {...props}>
      <path d="M2 21c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
      <path d="M19.38 20A11.6 11.6 0 0 0 21 14l-9-4-9 4c0 2.9.94 5.34 2.81 7.76" />
      <path d="M19 13V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6" />
      <path d="M12 10v4" />
      <path d="M12 2v3" />
    </Icon>
  );
}
