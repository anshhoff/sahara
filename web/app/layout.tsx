import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Shell } from "@/components/Shell";
import "./globals.css";

/**
 * Two faces, and a reason for each.
 *
 * Inter for the interface: it ships real tabular figures, which is not decoration on a
 * console whose every panel is a column of rupee amounts, and it holds up at the 11 and
 * 12px the dense layout leans on. JetBrains Mono for case IDs, hashes and JSON, where
 * the job is telling `0` from `O` and `1` from `l` in a 64-character hash.
 *
 * `display: swap` so a slow font never blanks the numbers, and both are subset to latin.
 */
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono-face",
});

export const metadata: Metadata = {
  title: {
    default: "Sahara — recovery console",
    template: "%s · Sahara",
  },
  description:
    "Failed subscription recovery, measured against a randomised control arm and net of what it cost.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-dvh">
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
