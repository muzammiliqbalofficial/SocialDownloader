import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SocialDownloader",
  description:
    "Extract media, captions, thumbnails and metadata from public social posts.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#0d1117",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // className="dark": dark is the default (section 8). The Phase 4 toggle
  // removes the class rather than adding one.
  // suppressHydrationWarning: that toggle writes to <html> before React
  // hydrates, which would otherwise report a mismatch.
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="min-h-dvh">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
