import type { Config } from "tailwindcss";

// Tokens mirror the architecture diagram's four isolation dimensions
// (project=blue, privacy=orange, kind=purple, source=green) and the
// quiet SaaS palette referenced by the Vaultis-style mock.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        ink: {
          900: "#0b1020",
          800: "#0f1530",
          700: "#1a2142",
          600: "#262d52",
          500: "#3c4474",
          400: "#5b6396",
        },
        canvas: {
          DEFAULT: "#f6f7fb",
          paper: "#ffffff",
          subtle: "#eef0f6",
        },
        line: {
          DEFAULT: "#e6e8ef",
          strong: "#d3d6e0",
        },
        text: {
          primary: "#0c1322",
          secondary: "#4d5773",
          muted: "#7a8299",
          inverse: "#f5f7ff",
        },
        accent: {
          project: "#3b82f6",
          privacy: "#f59e0b",
          kind: "#8b5cf6",
          source: "#10b981",
        },
        good: "#16a34a",
        warn: "#f59e0b",
        bad: "#dc2626",
      },
      boxShadow: {
        card: "0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 1px rgba(15, 23, 42, 0.02)",
        cardHover: "0 4px 14px rgba(15, 23, 42, 0.08)",
        sidebar: "inset -1px 0 0 rgba(255, 255, 255, 0.05)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
    },
  },
  plugins: [],
} satisfies Config;
