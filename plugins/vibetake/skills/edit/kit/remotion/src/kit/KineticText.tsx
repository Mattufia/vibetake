// Text that builds word by word (or letter by letter, or line by line), each unit on its own
// frame — staggered, or in sync with the moment the voice says it — with the design's
// highlight on the words that matter.
import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { fitText } from "@remotion/layout-utils";
import { applyCase, color, design, Enter, font, Role } from "./design";
import { enterStyle } from "./motion";

const norm = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

export type KineticTextProps = {
  lines: string[];                 // one string per line: the design decides the breaks, never re-wrapped
  startFrame: number;
  sizePx: number;                  // font size in px
  role?: Role;
  colorToken?: string;
  highlight?: string[];            // words to highlight (as written)
  unit?: "word" | "char" | "line";
  enter?: Enter;
  wordFrames?: (number | null)[];  // per word, the frame it enters (sync to speech); null → stagger
  align?: "left" | "center" | "right";
  maxWidthPx?: number;             // the size shrinks so the widest line fits (never grows)
};

export const KineticText: React.FC<KineticTextProps> = ({
  lines, startFrame, sizePx, role = "display", colorToken = "ink", highlight = [], unit = "word",
  enter = design.motion.enter, wordFrames, align = "center", maxWidthPx,
}) => {
  if (maxWidthPx) {
    const f = font(role) as { fontFamily?: string; fontWeight?: number };
    for (const line of lines) {
      if (!f.fontFamily || !line) continue;
      const fit = fitText({ text: applyCase(line), withinWidth: maxWidthPx * 0.94,
        fontFamily: f.fontFamily.replace(/'/g, ""), fontWeight: f.fontWeight,
        letterSpacing: `${design.type.tracking}em` }).fontSize;
      sizePx = Math.min(sizePx, fit);
    }
  }
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const hl = new Set(highlight.map(norm));
  const staggerF = Math.max(1, Math.round(design.motion.stagger * fps));
  const sp = (f0: number) => spring({ frame: frame - f0, fps,
    config: { stiffness: design.motion.stiffness, damping: design.motion.damping, mass: design.motion.mass ?? 1 } });
  let wi = 0;
  let ci = 0;
  const h = design.highlight;
  return (
    <div style={{ ...font(role), fontSize: sizePx, lineHeight: design.type.lineHeight, color: color(colorToken),
      letterSpacing: `${design.type.tracking}em`, textAlign: align }}>
      {lines.map((line, li) => {
        const words = applyCase(line).split(/\s+/).filter(Boolean);
        const lineStart = startFrame + li * staggerF * 3;
        return (
          <div key={li} style={{ display: "flex", flexWrap: "nowrap", gap: `0 ${sizePx * 0.26}px`,
            justifyContent: align === "center" ? "center" : align === "right" ? "flex-end" : "flex-start",
            overflow: enter === "mask" ? "hidden" : "visible", paddingBottom: enter === "mask" ? sizePx * 0.08 : 0 }}>
            {words.map((w, k) => {
              const idx = wi++;
              const f0 = unit === "line" ? lineStart
                : (wordFrames?.[idx] ?? null) !== null && wordFrames ? (wordFrames[idx] as number)
                : startFrame + idx * staggerF;
              const p = sp(f0);
              const isHl = hl.has(norm(w));
              // Highlight draws on a beat after the word lands.
              const hp = interpolate(sp(f0 + Math.round(staggerF * 2)), [0, 1], [0, 1], { extrapolateRight: "clamp" });
              const pad = (h.pad ?? 0.12) * sizePx;
              const wordStyle: React.CSSProperties = {
                position: "relative", display: "inline-block", whiteSpace: "pre",
                color: isHl && (h.style === "color" || h.style === "outline") ? color(h.color)
                  : isHl && (h.style === "marker" || h.style === "box") ? color(h.text ?? colorToken) : undefined,
                WebkitTextStroke: isHl && h.style === "outline" ? `${Math.max(2, sizePx * 0.04)}px ${color(h.color)}` : undefined,
                ...(isHl && h.style === "outline" ? { color: "transparent" } : {}),
              };
              const chars = unit === "char" ? Array.from(w) : [w];
              return (
                <span key={k} style={{ ...wordStyle, ...(unit === "char" ? {} : enterStyle(p, enter, sizePx)) }}>
                  {isHl && (h.style === "marker" || h.style === "box" || h.style === "underline") && (
                    <span style={{
                      position: "absolute", zIndex: -1,
                      left: -pad, right: -pad,
                      top: h.style === "underline" ? "88%" : `${h.style === "marker" ? 18 : 2}%`,
                      bottom: h.style === "underline" ? "-2%" : `${h.style === "marker" ? 8 : -4}%`,
                      background: color(h.color), borderRadius: h.style === "box" ? design.surface.radius * 0.5 : 2,
                      transformOrigin: "left center",
                      transform: `skewX(${h.skew ?? 0}deg) scaleX(${h.style === "box" ? 1 : hp})`,
                      opacity: h.style === "box" ? Math.min(1, p) : 1,
                    }} />
                  )}
                  {unit === "char"
                    ? chars.map((c, j) => {
                      const cf = startFrame + (ci++) * Math.max(1, Math.round(staggerF / 2));
                      return <span key={j} style={{ display: "inline-block", ...enterStyle(sp(cf), enter, sizePx) }}>{c}</span>;
                    })
                    : w}
                </span>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};
