// Covers are free canvases: each concept is a component written for this video
// (src/covers/), rendered as a still per platform. The kit only gives the facts every
// cover needs — its size, the area that survives the platform's crop — and a cut-out
// person that looks finished. Everything else is the concept's own design.
import React from "react";
import { Img, staticFile } from "remotion";

export type Platform = "youtube" | "reels" | "tiktok" | "shorts";

export const COVER_SIZES: Record<Platform, { width: number; height: number }> = {
  youtube: { width: 1280, height: 720 },
  reels: { width: 1080, height: 1920 },
  tiktok: { width: 1080, height: 1920 },
  shorts: { width: 1080, height: 1920 },
};

/** The part that must hold the headline and the face (0-1 of the canvas): the profile
 * grid's central 3:4 on vertical platforms; on YouTube everything but the duration badge. */
export const KEEP: Record<Platform, { x0: number; y0: number; x1: number; y1: number; avoid?: number[] }> = {
  youtube: { x0: 0.04, y0: 0.06, x1: 0.96, y1: 0.94, avoid: [0.8, 0.82, 1, 1] },
  reels: { x0: 0.06, y0: 0.125, x1: 0.94, y1: 0.875 },
  tiktok: { x0: 0.06, y0: 0.125, x1: 0.94, y1: 0.875 },
  shorts: { x0: 0.06, y0: 0.125, x1: 0.94, y1: 0.875 },
};

export type CoverProps = { platform: Platform; width: number; height: number;
  keep: { x0: number; y0: number; x1: number; y1: number; avoid?: number[] } };

/** A person cut out of a frame (cover.py cutout → public/covers/), with an optional outline
 * and a soft shadow that separate it from any background. */
export const Cutout: React.FC<{ file: string; style?: React.CSSProperties; outline?: string; outlinePx?: number;
  shadow?: boolean }> = ({ file, style, outline, outlinePx = 8, shadow = true }) => {
  const ring = outline
    ? [0, 45, 90, 135, 180, 225, 270, 315].map((a) => {
        const r = (a * Math.PI) / 180;
        return `drop-shadow(${(Math.cos(r) * outlinePx).toFixed(1)}px ${(Math.sin(r) * outlinePx).toFixed(1)}px 0 ${outline})`;
      }).join(" ")
    : "";
  const soft = shadow ? " drop-shadow(0 24px 40px rgba(0,0,0,.45))" : "";
  return <Img src={staticFile(`covers/${file}`)} style={{ ...style, filter: `${ring}${soft}`.trim() || undefined }} />;
};

/** A frame of the video (cover.py cutout also keeps it as frame_<t>.png). */
export const Frame: React.FC<{ file: string; style?: React.CSSProperties }> = ({ file, style }) => (
  <Img src={staticFile(`covers/${file}`)} style={{ objectFit: "cover", ...style }} />
);

/** The cut-out person placed by their face: say where the face's center goes and how tall
 * the face is (px); the body follows, and whatever falls outside the canvas is cut — so
 * a big face naturally leaves through the bottom edge. Data from cover.py cutout. */
import coverPlan from "../data/covers.json";

export const Person: React.FC<{ file: string; faceCenter: [number, number]; faceHeight: number;
  /** What faceCenter's x places: "face" (default) the face; "body" the weight of head and
   * shoulders; "balanced" the midpoint of both; "visual" the center of what is actually
   * visible on the canvas once placed and cropped — 85% head, 15% torso — what the eye
   * reads as centered (best for a big person leaving through the edges). */
  align?: "face" | "body" | "balanced" | "visual";
  canvas?: [number, number];        // for "visual": the cover's width and height
  outline?: string; outlinePx?: number; shadow?: boolean; flip?: boolean; style?: React.CSSProperties }> = ({
  file, faceCenter, faceHeight, align = "face", canvas, outline, outlinePx, shadow = true, flip, style }) => {
  const meta = (coverPlan as any).cutouts?.[file];
  if (!meta?.face) return <Cutout file={file} outline={outline} outlinePx={outlinePx} shadow={shadow} style={style} />;
  const scale = faceHeight / (meta.face.h * meta.height);
  const w = meta.width * scale;
  const h = meta.height * scale;
  // "body": the weight of head and shoulders (what a close-up shows), not of a stray arm.
  const bodyCx = meta.upper_cx ?? meta.body_cx;
  const faceCx = meta.face.x + meta.face.w / 2;
  const hasBody = bodyCx !== undefined && bodyCx !== null;
  let fx = (align === "body" && hasBody ? bodyCx : align === "balanced" && hasBody ? (faceCx + bodyCx) / 2 : faceCx) * w;
  if (align === "visual" && meta.grid && canvas) {
    // Iterate: place, measure the visible silhouette's center (head ×2), shift, repeat.
    const g = meta.grid;
    const cw = w / g.cols, ch = h / g.rows;
    const headEnd = (meta.face.y + meta.face.h * 1.1) * h;   // down to the chin, not the shoulders
    const fy0 = (meta.face.y + meta.face.h / 2) * h;
    let left = faceCenter[0] - faceCx * w;
    const top = faceCenter[1] - fy0;
    // The eye centers the head; the visible torso only pulls a little: 85% head, 15% torso,
    // each measured on what stays inside the canvas.
    for (let it = 0; it < 6; it++) {
      let hs = 0, hx = 0, bs = 0, bx = 0;
      for (let r = 0; r < g.rows; r++) {
        const y = top + (r + 0.5) * ch;
        if (y < 0 || y > canvas[1]) continue;
        const head = (r + 0.5) * ch < headEnd;
        for (let c = 0; c < g.cols; c++) {
          const x = left + (c + 0.5) * cw;
          const v = g.cells[r][c];
          if (v < 0.05 || x < 0 || x > canvas[0]) continue;
          if (head) { hs += v; hx += v * x; } else { bs += v; bx += v * x; }
        }
      }
      if (!hs && !bs) break;
      const cx = hs && bs ? 0.85 * (hx / hs) + 0.15 * (bx / bs) : hs ? hx / hs : bx / bs;
      left += faceCenter[0] - cx;
    }
    fx = faceCenter[0] - left;
  }
  const fy = (meta.face.y + meta.face.h / 2) * h;
  return (
    <Cutout file={file} outline={outline} outlinePx={outlinePx} shadow={shadow}
      style={{ position: "absolute", width: w, height: h, left: faceCenter[0] - (flip ? w - fx : fx),
        top: faceCenter[1] - fy, transform: flip ? "scaleX(-1)" : undefined, ...style }} />
  );
};
