import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Volition — Options Risk Desk",
  description: "A paper-trading options desk with evidence, simulation and hard risk limits.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
