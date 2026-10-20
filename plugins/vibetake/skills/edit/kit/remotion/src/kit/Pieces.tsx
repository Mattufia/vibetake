// Small pieces built on the design tokens: icons (real SVG, can draw themselves), counters,
// a reveal mask. Compose them, or write a component of your own for this video.
import React from "react";
import { interpolate, useCurrentFrame, useVideoConfig, Easing } from "remotion";
import { color, design, font } from "./design";
import { enterStyle, useSpring } from "./motion";
import { ICONS } from "../data/icons";

/** An SVG icon from 02-work/icons (or one drawn for the video). "draw" traces its strokes. */
export const Icon: React.FC<{ name: string; sizePx: number; startFrame: number; colorToken?: string;
  anim?: "pop" | "draw" | "rise" | "none" }> = ({ name, sizePx, startFrame, colorToken, anim = "pop" }) => {
  const p = useSpring(startFrame);
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  let svg = ICONS[name];
  if (!svg) return null;
  svg = svg.replace(/<svg\b[^>]*>/, (tag) =>
    tag.replace(/\s(width|height)="[^"]*"/g, "").replace("<svg", `<svg width="${sizePx}" height="${sizePx}"`));
  let style: React.CSSProperties = anim === "none" ? {} : anim === "rise" ? enterStyle(p, "rise", sizePx) : enterStyle(p, "pop", sizePx);
  if (anim === "draw") {
    const d = interpolate(frame - startFrame, [0, Math.round(0.6 * fps)], [1, 0],
      { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic) });
    // Inline per shape: a <style> tag here would be global and reset every other drawing icon.
    svg = svg.replace(/<(path|line|polyline|circle|rect)\b/g,
      `<$1 pathLength="1" style="stroke-dasharray:1;stroke-dashoffset:${d}"`);
    style = {};
  }
  return <div style={{ width: sizePx, height: sizePx, color: colorToken ? color(colorToken) : undefined, ...style }}
    dangerouslySetInnerHTML={{ __html: svg }} />;
};

/** A number that rolls up to its value on an ease-out, with the design's display face. */
export const Counter: React.FC<{ to: number; from?: number; startFrame: number; seconds?: number; sizePx: number;
  prefix?: string; suffix?: string; decimals?: number; locale?: string; colorToken?: string }> = ({
  to, from = 0, startFrame, seconds = 1.2, sizePx, prefix = "", suffix = "", decimals = 0, locale = "es-AR",
  colorToken = "ink" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const v = interpolate(frame - startFrame, [0, seconds * fps], [from, to],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const p = useSpring(startFrame);
  return (
    <div style={{ ...font("display"), fontSize: sizePx, color: color(colorToken), fontVariantNumeric: "tabular-nums",
      letterSpacing: `${design.type.tracking}em`, ...enterStyle(p, design.motion.enter, sizePx) }}>
      {prefix}{v.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}{suffix}
    </div>
  );
};

/** Reveals its children with a wipe (a mask moving across), for cards and images. */
export const Wipe: React.FC<{ children: React.ReactNode; startFrame: number; from?: "left" | "bottom" | "center" }> = ({
  children, startFrame, from = "left" }) => {
  const p = Math.min(1, useSpring(startFrame, { damping: 30 }));
  const clip = from === "left" ? `inset(0 ${100 - p * 100}% 0 0)`
    : from === "bottom" ? `inset(${100 - p * 100}% 0 0 0)`
    : `inset(${50 - p * 50}% ${50 - p * 50}% ${50 - p * 50}% ${50 - p * 50}%)`;
  return <div style={{ clipPath: clip }}>{children}</div>;
};
