import type React from "react";
// Timing and motion from the design's motion tokens: one spring language for the whole video.
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { design, Enter } from "./design";
import cues from "../data/cues.json";

export type At = number | { t: number } | { word: string; after?: number } | { line: number; word?: number };

type Word = { text: string; start: number; end: number; frame: number };
const allWords: Word[] = (cues as any).lines.flatMap((l: any) => l.words);
const lineWords = (n: number): Word[] => (cues as any).lines.find((l: any) => l.line === n)?.words ?? [];
const norm = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

/** Seconds on the edit's timeline: a number, {t}, the first time a word is said (after a
 * moment), or a word of a transcript line. */
export const secondsOf = (at: At | undefined, fallback = 0): number => {
  if (at === undefined) return fallback;
  if (typeof at === "number") return at;
  if ("t" in at) return at.t;
  if ("line" in at) return lineWords(at.line)[at.word ?? 0]?.start ?? fallback;
  const w = allWords.find((x) => x.start >= (at.after ?? 0) - 1e-3 && norm(x.text) === norm(at.word));
  return w ? w.start : fallback;
};

/** When each of `words` is said, from `from` on — so a headline can build in sync with the voice. */
export const spokenTimes = (words: string[], from: number): (number | null)[] => {
  let t = from;
  return words.map((w) => {
    const hit = allWords.find((x) => x.start >= t - 0.05 && norm(x.text) === norm(w));
    if (!hit) return null;
    t = hit.start;
    return hit.start;
  });
};

/** The beat nearest to `t` if one is within `within` seconds (music placed with audio.py), else `t`:
 * a key element can land on the beat instead of a hair off it. */
export const onBeat = (t: number, within = 0.12): number => {
  const beats: number[] = (cues as any).beats ?? [];
  const b = beats.reduce((best, x) => (Math.abs(x - t) < Math.abs(best - t) ? x : best), Infinity);
  return Math.abs(b - t) <= within ? b : t;
};

/** Weight by size: `size` (px, the element's larger side) makes big things heavier and slower and small
 * ones quicker, with the same bounce (damping grows with √mass, so the damping ratio stays). */
export const sizeWeight = (size?: number) => (size ? Math.min(2.5, Math.max(0.5, size / 400)) : 1);

export const useSpring = (startFrame: number,
  opts?: { stiffness?: number; damping?: number; mass?: number; size?: number }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const w = sizeWeight(opts?.size);
  return spring({
    frame: frame - startFrame, fps,
    config: { stiffness: opts?.stiffness ?? design.motion.stiffness,
      damping: (opts?.damping ?? design.motion.damping) * Math.sqrt(w),
      mass: (opts?.mass ?? design.motion.mass ?? 1) * w },
  });
};

/** 1 → 0 over the design's exit length, ending at `endFrame`. */
export const useExit = (endFrame: number | undefined) => {
  const frame = useCurrentFrame();
  if (endFrame === undefined) return 1;
  const n = Math.max(1, design.motion.exitFrames);
  return interpolate(frame, [endFrame - n, endFrame], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
};

/** CSS for an entering unit (a word, a letter, a card) at spring progress p (0→1, may overshoot). */
export const enterStyle = (p: number, kind: Enter = design.motion.enter, sizePx = 100): React.CSSProperties => {
  const dist = design.motion.distance * sizePx;
  const o = Math.min(1, Math.max(0, p * 1.4));
  switch (kind) {
    case "rise": return { opacity: o, transform: `translateY(${(1 - p) * dist}px)` };
    case "drop": return { opacity: o, transform: `translateY(${-(1 - p) * dist}px)` };
    case "slide": return { opacity: o, transform: `translateX(${-(1 - p) * dist}px)` };
    case "scale": return { opacity: o, transform: `scale(${0.4 + 0.6 * p})` };
    case "pop": return { opacity: o, transform: `scale(${p})` };
    case "blur": return { opacity: o, filter: `blur(${(1 - Math.min(p, 1)) * (design.motion.blur ?? 18)}px)`,
      transform: `translateY(${(1 - p) * dist * 0.3}px)` };
    case "mask": return { transform: `translateY(${(1 - p) * 110}%)` }; // inside an overflow:hidden line
    case "typewriter": return { opacity: p > 0.5 ? 1 : 0 };
  }
};
