// A container drawn from the design's surface tokens: solid, gradient, glass, outline or nothing.
import React from "react";
import { color, design } from "./design";

const shadows = (kind: string, c: string): string => {
  switch (kind) {
    case "soft": return "0 2px 6px rgba(0,0,0,.18), 0 12px 32px rgba(0,0,0,.22), 0 30px 80px rgba(0,0,0,.18)";
    case "lift": return "0 1px 0 rgba(255,255,255,.08) inset, 0 18px 40px -12px rgba(0,0,0,.45)";
    case "glow": return `0 0 24px ${c}66, 0 0 80px ${c}33`;
    case "long": return Array.from({ length: 14 }, (_, i) => `${i + 1}px ${i + 1}px 0 rgba(0,0,0,${0.22 - i * 0.012})`).join(", ");
    default: return "none";
  }
};

export const Surface: React.FC<{
  children: React.ReactNode; sizePx: number; style?: React.CSSProperties;
  override?: Partial<typeof design.surface>;
}> = ({ children, sizePx, style, override }) => {
  const s = { ...design.surface, ...(override ?? {}) };
  const fill = color(s.fill ?? "paper");
  const pad = s.padding * sizePx;
  const base: React.CSSProperties = {
    borderRadius: s.radius, padding: s.style === "none" ? 0 : `${pad * 0.7}px ${pad}px`,
    boxShadow: s.style === "none" ? "none" : shadows(s.shadow, color(design.highlight.color)),
  };
  const look: React.CSSProperties =
    s.style === "solid" ? { background: fill }
    : s.style === "gradient" ? { background: `linear-gradient(${s.angle ?? 135}deg, ${fill}, ${color(s.fill2 ?? "accent")})` }
    // The layer is drawn over transparency, so there's nothing behind to blur: "glass" is a
    // dense translucent fill with a fine light edge, which reads as glass over the video.
    : s.style === "glass" ? { background: `${fill}D6`, border: `1px solid rgba(255,255,255,.14)`,
        boxShadow: `inset 0 1px 0 rgba(255,255,255,.10), ${shadows(s.shadow, color(design.highlight.color))}` }
    : s.style === "outline" ? { border: `${s.borderWidth ?? Math.max(2, sizePx * 0.05)}px solid ${color(s.border ?? "ink")}` }
    : {};
  return <div style={{ ...base, ...look, ...style }}>{children}</div>;
};
