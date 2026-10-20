// Burned-in captions drawn by the layer (subtitles.py --engine remotion → src/data/captions.json).
// Grouping, timing, placement around the face and the safe zone come from subtitles.py; this
// only draws and animates them. `captionStyle` (the Graphics props) adjusts them, also from Studio.
import React from "react";
import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { z } from "zod";
import { zColor } from "@remotion/zod-types";
import data from "../data/captions.json";
import { font } from "./design";

export const captionStyleSchema = z.object({
  animation: z.enum(["pop", "karaoke", "bounce", "none"]),
  yShift: z.number().min(-0.5).max(0.5).step(0.01),   // share of the height, + moves down
  scale: z.number().min(0.5).max(2).step(0.05),
  highlightColor: z.union([zColor(), z.literal("")]),  // "" = the design's accent
  textColor: z.union([zColor(), z.literal("")]),       // "" = white
  box: z.boolean(),                                     // a dark box behind each group
  upper: z.boolean(),
});
export type CaptionStyle = z.infer<typeof captionStyleSchema>;

type Word = { text: string; t: number; end: number };
type Group = { start: number; end: number; y: number; size: number; words: Word[] };
const cap = data as unknown as {
  style?: string; font?: string; x?: number; width?: number; join?: string; rtl?: boolean;
  highlight?: string; edge?: string; outline?: number; groups: Group[];
};

const Group_: React.FC<{ g: Group; s: CaptionStyle }> = ({ g, s }) => {
  const frame = useCurrentFrame();
  const { fps, width: W, height: H } = useVideoConfig();
  const t = frame / fps;
  const px = g.size * H * s.scale;
  const stroke = Math.max(2, px * (cap.outline ?? 0.08));
  const hl = s.highlightColor || cap.highlight || "#FFFF00";
  const edge = s.highlightColor ? "#000" : cap.edge || "#000";
  const ink = s.textColor || "#FFFFFF";
  const words = cap.style !== "sentence";            // sentence: whole phrase, no active word
  const inF = Math.round(g.start * fps);
  const enter = s.animation === "none" ? 1 : spring({ frame: frame - inF, fps, config: { damping: 14, stiffness: 220 } });
  return (
    <div style={{ position: "absolute", left: (cap.x ?? 0.5) * W, top: (g.y + s.yShift) * H,
      width: (cap.width ?? 0.84) * W, transform: `translate(-50%, -50%) scale(${s.animation === "pop" ? 0.8 + 0.2 * enter : 1})`,
      opacity: Math.min(1, enter * 1.5), display: "flex", justifyContent: "center", direction: cap.rtl ? "rtl" : "ltr" }}>
      <div style={{ ...font("captions"), ...(font("captions").fontFamily ? {} : { fontFamily: cap.font, fontWeight: 800 }),
        fontSize: px, lineHeight: 1.2, textAlign: "center", color: ink,
        background: s.box ? "rgba(0,0,0,0.6)" : undefined, padding: s.box ? `${px * 0.1}px ${px * 0.3}px` : 0,
        borderRadius: px * 0.2 }}>
        {g.words.map((w, i) => {
          const next = g.words[i + 1]?.t ?? g.end;
          const active = words && t >= (i === 0 ? g.start : w.t) && t < next;
          const said = t >= w.t;
          const wf = Math.round(w.t * fps);
          const jump = s.animation === "bounce" && active
            ? spring({ frame: frame - wf, fps, config: { damping: 9, stiffness: 300 } }) : 0;
          const big = s.animation === "pop" && active ? 1 + 0.08 * spring({ frame: frame - wf, fps, config: { damping: 12 } }) : 1;
          const c = s.animation === "karaoke" ? (said && words ? hl : ink) : active ? hl : ink;
          const lit = c === hl && c !== ink;
          return (
            <span key={i} style={{ display: "inline-block", color: c, paintOrder: "stroke fill",
                marginInlineStart: i > 0 && cap.join ? "0.3em" : 0,   // a margin, not a space: the active word grows
                WebkitTextStroke: `${stroke * 2}px ${lit ? edge : "#000"}`,
                transform: `translateY(${-jump * px * 0.15 * (1 - Math.min(1, (frame - wf) / (fps * 0.3)))}px) scale(${big})` }}>
                {s.upper ? w.text.toLocaleUpperCase() : w.text}
              </span>
          );
        })}
      </div>
    </div>
  );
};

export const Captions: React.FC<{ style: CaptionStyle }> = ({ style }) => {
  const { fps } = useVideoConfig();
  const t = useCurrentFrame() / fps;
  const g = cap.groups.find((x) => t >= x.start && t < x.end);
  return g ? <Group_ key={g.start} g={g} s={style} /> : null;
};
