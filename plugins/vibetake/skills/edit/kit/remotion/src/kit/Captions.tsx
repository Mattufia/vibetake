// Burned-in captions drawn by the layer (subtitles.py --engine remotion → src/data/captions.json).
// Grouping, timing, placement around the face and the safe zone come from subtitles.py; this
// only draws and animates them. `captionStyle` (the Graphics props) adjusts them, also from Studio.
import React, { useMemo } from "react";
import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { z } from "zod";
import { zColor } from "@remotion/zod-types";
import data from "../data/captions.json";
import { ThreeCanvas } from "@remotion/three";
import * as THREE from "three";
import type opentype from "opentype.js";
import { design, font } from "./design";
import { extrude, fontFile, loadFont, Studio, textSvg, useAsset } from "./Three";

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

// 3D captions (design.json → "captions": {"3d": true}): the same groups, timing and style, each
// word extruded in the captions face; the face flat in its color (legible), the sides shaded darker.
// The group flips up into place; the active word comes forward in its highlight.
// ponytail: left-to-right lines only; an RTL language keeps the 2D captions.
const Group3D: React.FC<{ g: Group; s: CaptionStyle; f: opentype.Font }> = ({ g, s, f }) => {
  const frame = useCurrentFrame();
  const { fps, width: W, height: H } = useVideoConfig();
  const t = frame / fps;
  const px = g.size * H * s.scale;
  const lh = px * 1.2;
  const boxW = (cap.width ?? 0.84) * W;
  const hl = s.highlightColor || cap.highlight || "#FFFF00";
  const ink = s.textColor || "#FFFFFF";
  const words = cap.style !== "sentence";
  const { placed, lines } = useMemo(() => {
    const gap = cap.join ? f.getAdvanceWidth(" ", px) : 0;
    const items = g.words.map((w, i) => {
      const text = s.upper ? w.text.toLocaleUpperCase() : w.text;
      return { i, w: f.getAdvanceWidth(text, px), geo: extrude(textSvg(f, text, px), 0.25, px), x: 0, line: 0 };
    });
    const rows: (typeof items)[] = [[]];
    let x = 0;
    for (const it of items) {
      if (x > 0 && x + gap + it.w > boxW) { rows.push([]); x = 0; }
      x += (x > 0 ? gap : 0) + it.w;
      rows[rows.length - 1].push(it);
    }
    rows.forEach((r, li) => {
      let cx = -(r.reduce((a, it) => a + it.w, 0) + gap * (r.length - 1)) / 2;
      for (const it of r) { it.x = cx; it.line = li; cx += it.w + gap; }
    });
    return { placed: items, lines: rows.length };
  }, [g, px, s.upper]);
  // The face exactly its color (no tone mapping), the sides a shaded dark version of it: the outline.
  const mats = useMemo(() => {
    const m: Record<string, THREE.Material[]> = {};
    return (c: string) => (m[c] ??= [new THREE.MeshBasicMaterial({ color: c, toneMapped: false }),
      new THREE.MeshStandardMaterial({ color: new THREE.Color(c).multiplyScalar(0.08), roughness: 0.6, envMapIntensity: 0.2 })]);
  }, []);
  const cw = Math.round(boxW + px * 2), chh = Math.round(lines * lh + px * 2);
  const inF = Math.round(g.start * fps);
  const enter = s.animation === "none" ? 1 : spring({ frame: frame - inF, fps, config: { damping: 14, stiffness: 220 } });
  return (
    <div style={{ position: "absolute", left: (cap.x ?? 0.5) * W, top: (g.y + s.yShift) * H,
      transform: "translate(-50%, -50%)", opacity: Math.min(1, enter * 1.5) }}>
      <ThreeCanvas width={cw} height={chh} gl={{ alpha: true, antialias: true }} style={{ background: "transparent" }}
        camera={{ position: [0, 0, chh / 2 / Math.tan((15 * Math.PI) / 180)], fov: 30, near: 1, far: 1e5 }}>
        <Studio />
        <group rotation={[0.15 - (1 - enter) * 1.3, 0, 0]}>
          {placed.map(({ i, geo, x, line }) => {
            if (!geo) return null;
            const w = g.words[i];
            const next = g.words[i + 1]?.t ?? g.end;
            const active = words && t >= (i === 0 ? g.start : w.t) && t < next;
            const said = t >= w.t;
            const c = s.animation === "karaoke" ? (said && words ? hl : ink) : active ? hl : ink;
            const pop = active && s.animation !== "none"
              ? spring({ frame: frame - Math.round(w.t * fps), fps, config: { damping: 12 } }) : 0;
            const y = ((lines - 1) / 2 - line) * lh - px * 0.35;
            return (
              <mesh key={i} geometry={geo} material={mats(c)}
                position={[x, y + (s.animation === "bounce" ? pop * px * 0.12 : 0), pop * px * 0.3]} />
            );
          })}
        </group>
      </ThreeCanvas>
    </div>
  );
};

const Captions3D: React.FC<{ g: Group; s: CaptionStyle; file: string }> = ({ g, s, file }) => {
  const f = useAsset(`font:${file}`, () => loadFont(file));
  return f ? <Group3D g={g} s={s} f={f} /> : null;
};

export const Captions: React.FC<{ style: CaptionStyle }> = ({ style }) => {
  const { fps } = useVideoConfig();
  const t = useCurrentFrame() / fps;
  const g = cap.groups.find((x) => t >= x.start && t < x.end);
  const file = design.captions?.["3d"] && !cap.rtl ? fontFile("captions") : undefined;
  if (!g) return null;
  return file ? <Captions3D key={g.start} g={g} s={style} file={file} /> : <Group_ key={g.start} g={g} s={style} />;
};
