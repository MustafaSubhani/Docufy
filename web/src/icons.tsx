import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 18, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const Plus = (p: IconProps) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;
export const Search = (p: IconProps) => <Icon {...p}><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></Icon>;
export const Paperclip = (p: IconProps) => (
  <Icon {...p}><path d="m20.5 11.5-8.3 8.3a5 5 0 0 1-7.1-7.1l8.6-8.6a3.4 3.4 0 0 1 4.8 4.8l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9" /></Icon>
);
export const ArrowUp = (p: IconProps) => <Icon {...p}><path d="M12 19V5M6 11l6-6 6 6" /></Icon>;
export const StopSquare = (p: IconProps) => <Icon {...p}><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" /></Icon>;
export const Close = (p: IconProps) => <Icon {...p}><path d="M6 6l12 12M18 6 6 18" /></Icon>;
export const ChevronDown = (p: IconProps) => <Icon {...p}><path d="m6 9 6 6 6-6" /></Icon>;
export const ChevronLeft = (p: IconProps) => <Icon {...p}><path d="m15 6-6 6 6 6" /></Icon>;
export const ChevronRight = (p: IconProps) => <Icon {...p}><path d="m9 6 6 6-6 6" /></Icon>;
export const Download = (p: IconProps) => <Icon {...p}><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></Icon>;
export const FileText = (p: IconProps) => (
  <Icon {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></Icon>
);
export const Folder = (p: IconProps) => <Icon {...p}><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" /></Icon>;
export const Gear = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </Icon>
);
export const Menu = (p: IconProps) => <Icon {...p}><path d="M4 7h16M4 12h16M4 17h16" /></Icon>;
export const Kebab = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="6" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="18" r="1.2" fill="currentColor" stroke="none" />
  </Icon>
);
export const Check = (p: IconProps) => <Icon {...p}><path d="m5 12.5 4.5 4.5L19 7.5" /></Icon>;
export const Refresh = (p: IconProps) => <Icon {...p}><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" /></Icon>;
export const Pencil = (p: IconProps) => <Icon {...p}><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z" /></Icon>;
export const Trash = (p: IconProps) => <Icon {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" /></Icon>;
export const Upload = (p: IconProps) => <Icon {...p}><path d="M12 20V9M7 14l5-5 5 5M5 4h14" /></Icon>;
export const Pages = (p: IconProps) => (
  <Icon {...p}><rect x="4" y="3" width="12" height="16" rx="2" /><path d="M8 21h10a2 2 0 0 0 2-2V7" /></Icon>
);
export const Chip = (p: IconProps) => (
  <Icon {...p}><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></Icon>
);
export const Cloud = (p: IconProps) => <Icon {...p}><path d="M7 18a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 8.5a4.8 4.8 0 0 1-.5 9.5z" /></Icon>;
export const Type = (p: IconProps) => <Icon {...p}><path d="M5 6V4h14v2M12 4v16M9 20h6" /></Icon>;
export const Minus = (p: IconProps) => <Icon {...p}><path d="M5 12h14" /></Icon>;
export const Shield = (p: IconProps) => <Icon {...p}><path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6z" /></Icon>;
export const PanelLeft = (p: IconProps) => <Icon {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M9.5 4.5v15" /></Icon>;
export const Copy = (p: IconProps) => <Icon {...p}><rect x="8.5" y="8.5" width="11" height="11" rx="2" /><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" /></Icon>;
export const ArrowDown = (p: IconProps) => <Icon {...p}><path d="M12 5v14M6 13l6 6 6-6" /></Icon>;
export const Pen = (p: IconProps) => <Icon {...p}><path d="M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10z" /><path d="M13.5 7.5l3 3" /></Icon>;
export const Layout = (p: IconProps) => <Icon {...p}><rect x="4" y="4" width="16" height="16" rx="2.5" /><path d="M4 10h16M10 10v10" /></Icon>;
export const List = (p: IconProps) => <Icon {...p}><path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" /></Icon>;
export const Lock = (p: IconProps) => <Icon {...p}><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></Icon>;

export function Spinner({ size = 14 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden="true" />;
}

export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="var(--ink)" />
      <path d="M11 8.5h7l4.5 4.5v10.5a1.5 1.5 0 0 1-1.5 1.5H11a1.5 1.5 0 0 1-1.5-1.5V10A1.5 1.5 0 0 1 11 8.5z" fill="var(--paper)" />
      <path d="M18 8.5V13h4.5" fill="none" stroke="var(--ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M12.8 16.5h6.4M12.8 19.5h4.4" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
