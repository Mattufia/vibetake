// 3D in the graphics layer: extruded text (the project's display font), an extruded icon or
// logo (filled SVG), the user's own model (.glb in brand/), or a bar chart. Lit like a studio,
// in the design's colors, moved by frame only (never by its own clock: renders would flicker).
// Captions.tsx builds 3D captions on the same pieces (loadFont, extrude).
import React, { useEffect, useMemo, useState } from "react";
import { continueRender, delayRender, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { ThreeCanvas } from "@remotion/three";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import { SVGLoader } from "three/examples/jsm/loaders/SVGLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import opentype from "opentype.js";
import { applyCase, color, design, Role } from "./design";
import { useSpring } from "./motion";
import { ICONS } from "../data/icons";
import { FONT_FILES } from "../data/fonts";

export type Move = "turn" | "spin" | "float" | "push" | "orbit" | "still";
export type Finish = "matte" | "gloss" | "metal";
export type ThreeProps = {
  model: "text" | "icon" | "logo" | "glb" | "bars";
  text?: string; name?: string; src?: string;      // text, icon name, or brand/<file> for logo and glb
  values?: number[]; labels?: string[]; highlight?: number; suffix?: string;   // bars
  move?: Move; finish?: Finish; colorToken?: string; depth?: number;
  startFrame: number; widthPx: number; heightPx: number;
};

/** Loads something async once, holding the render until it's in. */
export const useAsset = <T,>(key: string, load: () => Promise<T>): T | null => {
  const [v, setV] = useState<T | null>(null);
  const [h] = useState(() => delayRender(`3d: ${key}`));
  useEffect(() => {
    load().then((x) => { setV(x); continueRender(h); }).catch((e) => { console.error(e); continueRender(h); });
  }, [key]);
  return v;
};

/** The font file of a design role (fonts.py downloads it), or undefined. */
export const fontFile = (role: Role): string | undefined => {
  const f = design.fonts[role] ?? design.fonts.display ?? design.fonts.text;
  return f ? FONT_FILES[`${f.family}|${f.weight}`] : undefined;
};
const fonts: Record<string, Promise<opentype.Font>> = {};
export const loadFont = (file: string): Promise<opentype.Font> =>
  (fonts[file] ??= fetch(staticFile(file)).then((r) => r.arrayBuffer()).then((b) => opentype.parse(b)));

/** A string as an SVG path in a font, `size` px tall (baseline at y=0). */
export const textSvg = (font: opentype.Font, s: string, size = 100, x = 0): string =>
  `<svg xmlns="http://www.w3.org/2000/svg"><path d="${font.getPath(s, x, 0, size).toPathData(2)}"/></svg>`;

const svgOf = async (p: ThreeProps): Promise<string> => {
  if (p.model === "icon") return ICONS[p.name ?? ""] ?? "";
  if (p.model === "logo") return (await fetch(staticFile(`brand/${p.src}`))).text();
  const file = fontFile("display");
  if (!file) throw new Error("3d text: the display font has no file (fonts.py)");
  return textSvg(await loadFont(file), applyCase(p.text ?? ""));
};

// ponytail: filled shapes only; a stroke-drawn icon (Lucide) extrudes as nothing — use a filled
// set (Phosphor fill/duotone, Simple Icons) for 3D, or SVGLoader.pointsToStroke if it's ever needed.
/** Extrudes an SVG's filled shapes, `depth` × its height deep, centered. With `unit`, the depth and
 *  bevel scale with `unit` instead and the geometry keeps the SVG's own coordinates (y up): text
 *  laid out word by word. ExtrudeGeometry's groups: 0 = the faces, 1 = the sides. */
export const extrude = (svg: string, depth: number, unit?: number): THREE.BufferGeometry | null => {
  const shapes = new SVGLoader().parse(svg).paths.flatMap((pth) => SVGLoader.createShapes(pth));
  if (!shapes.length) return null;
  let h = unit ?? 0;
  if (!unit) {
    const flat = new THREE.ShapeGeometry(shapes);
    flat.computeBoundingBox();
    h = Math.max(flat.boundingBox!.max.y - flat.boundingBox!.min.y, 1e-6);
  }
  const g = new THREE.ExtrudeGeometry(shapes, { depth: depth * h, bevelEnabled: true, bevelThickness: 0.04 * h,
    bevelSize: 0.02 * h, bevelSegments: 4, curveSegments: 16 });
  g.rotateX(Math.PI);          // SVG's y points down; a rotation, not a mirror, keeps faces facing out
  if (!unit) g.center();
  return g;
};

/** A studio's light without an HDRI file: reflections for gloss and metal. */
export const Studio: React.FC = () => {
  const { gl, scene } = useThree();
  useMemo(() => {
    const pm = new THREE.PMREMGenerator(gl);
    scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;
  }, [gl, scene]);
  return (<>
    <ambientLight intensity={0.25} />
    <directionalLight position={[3, 4, 5]} intensity={1.6} />
    <directionalLight position={[-4, 1, -3]} intensity={0.8} />
  </>);
};

const finishOf = (f: Finish) => (f === "metal" ? { metalness: 1, roughness: 0.25 }
  : f === "gloss" ? { metalness: 0, roughness: 0.15 } : { metalness: 0, roughness: 0.75 });

/** The move: rotation, offset and scale from the entry spring `s` and the seconds since it. */
const motionOf = (move: Move, s: number, t: number) => ({
  rotX: move === "float" ? Math.sin(t * 1.1) * 0.08 : 0,
  rotY: move === "spin" ? t * Math.PI * 0.6 : move === "turn" ? (1 - s) * -Math.PI * 0.75
    : move === "float" || move === "orbit" ? Math.sin(t * (move === "orbit" ? 0.9 : 1.4)) * (move === "orbit" ? 0.6 : 0.18)
    : 0,
  y: move === "float" ? Math.sin(t * 2) * 0.04 : 0,
  z: move === "push" ? (1 - s) * -6 : 0,
  k: move === "push" ? 1 : 0.6 + 0.4 * s,
});

/** Scale so a w×h object's larger side fills ~80% of the 2-unit view of a canvas of this aspect. */
const fitOf = (w: number, h: number, aspect: number) => 1.6 / Math.max(w / aspect, h, 1e-6);

const useEntry = (p: ThreeProps) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Weight by size: a big object enters heavier (motion.ts).
  const s = Math.min(1.05, useSpring(p.startFrame, { size: Math.max(p.widthPx, p.heightPx) }));
  return { s, t: Math.max(0, frame - p.startFrame) / fps, frame, fps };
};

const Model: React.FC<{ p: ThreeProps; obj: THREE.Object3D | THREE.BufferGeometry }> = ({ p, obj }) => {
  const { s, t } = useEntry(p);
  const fit = useMemo(() => {
    const box = obj instanceof THREE.BufferGeometry ? (obj.computeBoundingBox(), obj.boundingBox!)
      : new THREE.Box3().setFromObject(obj);
    const sz = box.getSize(new THREE.Vector3());
    return fitOf(sz.x, sz.y, p.widthPx / p.heightPx);
  }, [obj]);
  const m = motionOf(p.move ?? "turn", s, t);
  return (
    <group position={[0, m.y, m.z]} rotation={[m.rotX, m.rotY, 0]} scale={fit * m.k}>
      {obj instanceof THREE.BufferGeometry ? (
        <mesh geometry={obj}>
          <meshStandardMaterial color={color(p.colorToken ?? "accent")} {...finishOf(p.finish ?? "gloss")} />
        </mesh>) : <primitive object={obj} />}
    </group>
  );
};

const canvas = (p: ThreeProps, children: React.ReactNode) => (
  <ThreeCanvas width={p.widthPx} height={p.heightPx} gl={{ alpha: true, antialias: true }}
    camera={{ position: [0, 0, 5], fov: 25 }} style={{ background: "transparent" }}>
    <Studio />
    {children}
  </ThreeCanvas>
);

const Object3D: React.FC<ThreeProps> = (p) => {
  const key = `${p.model}:${p.text ?? p.name ?? p.src}`;
  const obj = useAsset<THREE.Object3D | THREE.BufferGeometry | null>(key, async () =>
    p.model === "glb" ? (await new GLTFLoader().loadAsync(staticFile(`brand/${p.src}`))).scene
      : extrude(await svgOf(p), p.depth ?? 0.3));
  return canvas(p, obj && <Model p={p} obj={obj} />);
};

/** Bars grow one after another; labels under them, values on top (the text font, if it has a file).
 *  `highlight` (an index) puts that bar in the color and the rest in ink. Tilted so the depth shows. */
const BarsScene: React.FC<{ p: ThreeProps; font: opentype.Font | null }> = ({ p, font }) => {
  const { s, t, frame, fps } = useEntry(p);
  const v = p.values ?? [];
  const max = Math.max(...v, 1e-6);
  const BW = 1, GAP = 0.5, H = 4, TXT = 0.55;          // bar width, gap, tallest bar, text height
  const W = v.length * BW + (v.length - 1) * GAP;
  const txt = (s_: string) => (font ? extrude(textSvg(font, s_), 0.15) : null);
  const geo = useMemo(() => ({
    labels: (p.labels ?? []).map((l) => txt(applyCase(l))),
    values: v.map((x) => txt(`${+x.toFixed(2)}${p.suffix ?? ""}`)),
  }), [font]);
  const sizeOf = (g: THREE.BufferGeometry | null) =>
    g ? (g.computeBoundingBox(), g.boundingBox!.getSize(new THREE.Vector3())) : new THREE.Vector3(1, 1, 1);
  const textScale = (g: THREE.BufferGeometry | null) => { const z = sizeOf(g); return Math.min(TXT / z.y, BW * 1.3 / z.x); };
  const m = motionOf(p.move ?? "still", s, t);
  const fit = fitOf(W, H + TXT * 3, p.widthPx / p.heightPx);
  const mat = (c: string) => <meshStandardMaterial color={color(c)} {...finishOf(p.finish ?? "gloss")} />;
  return (
    <group position={[0, m.y, m.z]} rotation={[0.18 + m.rotX, -0.35 + m.rotY, 0]} scale={fit * m.k}>
      <group position={[-W / 2 + BW / 2, -H / 2, 0]}>
        {v.map((x, i) => {
          const c = p.highlight === undefined || p.highlight === i ? p.colorToken ?? "accent" : "ink";
          const g = Math.min(1, useSpring(p.startFrame + Math.round(i * design.motion.stagger * 2 * fps)));
          const h = Math.max(1e-3, (x / max) * H * g);
          const lab = geo.labels[i], val = geo.values[i];
          return (
            <group key={i} position={[i * (BW + GAP), 0, 0]}>
              <mesh position={[0, h / 2, 0]}><boxGeometry args={[BW, h, BW]} />{mat(c)}</mesh>
              {val && <mesh geometry={val} position={[0, h + TXT * 0.9, 0]} scale={textScale(val) * Math.min(1, g * 1.2)}>
                {mat(c)}</mesh>}
              {lab && <mesh geometry={lab} position={[0, -TXT * 1.1, BW / 2]} scale={textScale(lab)}>{mat("ink")}</mesh>}
            </group>
          );
        })}
      </group>
    </group>
  );
};

const Bars3D: React.FC<ThreeProps> = (p) => {
  const file = fontFile("text");
  const font = useAsset(`font:${file}`, () => (file ? loadFont(file) : Promise.resolve(null)));
  return canvas(p, <BarsScene p={p} font={font} />);
};

export const Three3D: React.FC<ThreeProps> = (p) => (p.model === "bars" ? <Bars3D {...p} /> : <Object3D {...p} />);
