/**
 * The icon set.
 *
 * Lucide geometry, drawn inline rather than installed. The console has no runtime
 * dependencies beyond React and Next, and pulling a 1,500-glyph icon package to draw
 * eleven shapes would have been the first one. Inline SVG also means the icons inherit
 * `currentColor` and the stroke width is ours to keep consistent, which is the actual
 * thing that makes an icon set look like a set.
 *
 * Every icon here is decorative — each one sits next to its own text label, so they
 * are hidden from screen readers rather than described twice.
 */
import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
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

export const IconOverview = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="3" width="7" height="9" rx="1.5" />
    <rect x="14" y="3" width="7" height="5" rx="1.5" />
    <rect x="14" y="12" width="7" height="9" rx="1.5" />
    <rect x="3" y="16" width="7" height="5" rx="1.5" />
  </Svg>
);

export const IconResults = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 3v16a2 2 0 0 0 2 2h16" />
    <path d="M7 15l3.5-4.5 3 3L20 6" />
  </Svg>
);

export const IconCause = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 20V10" />
    <path d="M9 20V4" />
    <path d="M15 20v-7" />
    <path d="M21 20v-11" />
  </Svg>
);

export const IconMechanism = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
  </Svg>
);

export const IconFencing = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
    <path d="M9 12l2 2 4-4" />
  </Svg>
);

export const IconPromise = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18M8 3v4M16 3v4" />
    <path d="M9.5 15.5l1.75 1.75L15 13.5" />
  </Svg>
);

export const IconQueue = (p: IconProps) => (
  <Svg {...p}>
    <path d="M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20" />
    <circle cx="10" cy="7.5" r="3.5" />
    <path d="M19 8v6M22 11h-6" />
  </Svg>
);

export const IconCases = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H17l3 3v14a1 1 0 0 1-1 1H6.5A2.5 2.5 0 0 1 4 18.5Z" />
    <path d="M16 3v4h4M8.5 12h7M8.5 16h4.5" />
  </Svg>
);

export const IconControl = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
    <circle cx="9" cy="6" r="2" fill="var(--surface)" />
    <circle cx="15" cy="12" r="2" fill="var(--surface)" />
    <circle cx="7" cy="18" r="2" fill="var(--surface)" />
  </Svg>
);

export const IconMenu = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Svg>
);

export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Svg>
);

export const IconChevronRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 5l7 7-7 7" />
  </Svg>
);

export const IconChevronDown = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 9l7 7 7-7" />
  </Svg>
);

export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9v4M12 17h.01" />
  </Svg>
);

export const IconEmpty = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 15h4l2 3h6l2-3h4" />
    <path d="M5.5 15 7 5.5A2 2 0 0 1 9 4h6a2 2 0 0 1 2 1.5L18.5 15V19a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2Z" />
  </Svg>
);

export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Svg>
);

export const IconBolt = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5Z" />
  </Svg>
);

export const IconExternal = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14 4h6v6M20 4l-8.5 8.5" />
    <path d="M18 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" />
  </Svg>
);

export const IconClock = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5.2l3.2 1.9" />
  </Svg>
);
