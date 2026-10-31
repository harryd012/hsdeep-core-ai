import type { Metadata } from "next";
import "./globals.css";
import Providers from "@/components/Providers";
import { THEME_BOOT_SCRIPT } from "@/lib/theme";

/**
 * Root layout — HTML document shell ONLY.
 * No Sidebar, no CopilotBar, no AuthGuard here: the authenticated shell
 * (AuthGuard + Sidebar + main + CopilotBar) mounts exactly once in
 * src/app/(dashboard)/layout.tsx, so login/signup stay shell-free while
 * dashboard routes get one shell.
 */
export const metadata: Metadata = {
  title: "HSDEEP CORE AI",
  description: "HSDEEP CORE AI — AIOps Control Plane",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: data-theme is set by the boot script before
    // React hydrates, so the server-rendered <html> intentionally differs.
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
