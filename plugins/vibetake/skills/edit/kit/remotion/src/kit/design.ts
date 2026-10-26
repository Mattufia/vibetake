import type React from "react";
// This video's design, generated for it (02-work/design.json → src/data/design.json).
// The kit has no house style: every value below comes from the video's own design;
// the defaults only keep a half-written design.json from crashing.
import raw from "../data/design.json";

export type Role = "display" | "text" | "captions";
export type FontRef = { family: string; weight: number | string; name?: string; file?: string };

export type Design = {
  concept?: string;
  fonts: Partial<Record<Role, FontRef>>;
  colors: Record<string, string>;
  type: { case: "upper" | "lower" | "title" | "sentence"; tracking: number; lineHeight: number; scale: number };
  surface: {
    style: "solid" | "gradient" | "glass" | "outline" | "none";
    fill?: string; fill2?: string; angle?: number; radius: number; padding: number;
    shadow: "none" | "soft" | "lift" | "glow" | "long"; border?: string; borderWidth?: number;
  };
  highlight: { style: "marker" | "box" | "underline" | "color" | "outline" | "none"; color: string; text?: string;
    skew?: number; pad?: number };
  motion: { enter: Enter; stagger: number; stiffness: number; damping: number; mass?: number;
    distance: number; exitFrames: number; blur?: number };
  captions?: { "3d"?: boolean };   // captions extruded in the captions face (Captions.tsx)
};
export type Enter = "rise" | "drop" | "scale" | "mask" | "blur" | "slide" | "typewriter" | "pop";

const d = raw as unknown as Partial<Design>;

export const design: Design = {
  concept: d.concept,
  captions: d.captions,
  fonts: d.fonts ?? {},
  colors: { ink: "#ffffff", paper: "#000000", accent: "#ffffff", ...(d.colors ?? {}) },
  type: { case: "sentence", tracking: 0, lineHeight: 1.05, scale: 1, ...(d.type ?? {}) },
  surface: { style: "none", radius: 0, padding: 0.5, shadow: "none", ...(d.surface ?? {}) },
  highlight: { style: "color", color: "accent", ...(d.highlight ?? {}) },
  motion: { enter: "rise", stagger: 0.06, stiffness: 160, damping: 16, distance: 0.6, exitFrames: 8,
    ...(d.motion ?? {}) },
};

/** A color token ("accent") or a literal color ("#ffcc00", "rgba(…)"). */
export const color = (c?: string): string => {
  if (!c) return design.colors.ink;
  return design.colors[c] ?? c;
};

export const font = (role: Role = "display"): React.CSSProperties => {
  const f = design.fonts[role] ?? design.fonts.display ?? design.fonts.text;
  return f ? { fontFamily: `'${f.family}'`, fontWeight: Number(f.weight) || 400 } : {};
};

export const applyCase = (s: string): string => {
  switch (design.type.case) {
    case "upper": return s.toLocaleUpperCase();
    case "lower": return s.toLocaleLowerCase();
    case "title": return s.replace(/(^|\s)(\p{L})/gu, (_, a, b) => a + b.toLocaleUpperCase());
    default: return s;
  }
};
