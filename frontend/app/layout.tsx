import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gene Therapy Design Console",
  description: "Gene-to-CDS codon optimization dashboard"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
