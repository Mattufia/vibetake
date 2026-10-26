// A 3D object in the graphics layer: extruded text (the project's display font), an extruded
// icon or logo (filled SVG), or the user's own model (.glb in brand/). Lit like a studio, in the
// design's colors, moved by frame only (never by its own clock: renders would flicker).
import React, { useEffect, useMemo, useState } from "react";
import { continueRender, delayRender, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { ThreeCanvas } from "@remotion/three";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import { SVGLoader } from "three/examples/jsm/loaders/SVGLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import opentype from "opentype.js";
import { applyCase, color, design } from "./design";
import { useSpring } from "./motion";
import { ICONS } from "../data/icons";
import { FONT_FILES } from "../data/fonts";

export type Move = "turn" | "spin" | "float" | "push" | "orbit" | "still";
export type Finish = "matte" | "gloss" | "metal";
export type ThreeProps = {
  model: "text" | "icon" | "logo" | "glb";
  text?: string; name?: string; src?: string;      // text, icon name, or brand/<file> for logo and glb
  move?: Move; finish?: Finish; colorToken?: string; depth?: number;
  startFrame: number; widthPx: number; heightPx: number;
};

/** Loads something async once, holding the render until it's in. */
const useAsset = <T,>(key: string, load: () => Promise<T>): T | null => {
  const [v, setV] = useState<T | null>(null);
  const [h] = useState(() => delayRender(`3d: ${key}`));
  useEffect(() => {
    load().then((x) => { setV(x); continueRender(h); }).catch((e) => { console.error(e); continueRender(h); });
  }, [key]);
  return v;
};

const svgOf = async (p: ThreeProps): Promise<string> => {
  if (p.model === "icon") return ICONS[p.name ?? ""] ?? "";
  if (p.model === "logo") return (await fetch(staticFile(`brand/${p.src}`))).text();
  const f = design.fonts.display ?? design.fonts.text;
  const file = f && FONT_FILES[`${f.family}|${f.weight}`];
  if (!file) throw new Error("3d text: the display font has no file (fonts.py)");
  const font = opentype.parse(await (await fetch(staticFile(file))).arrayBuffer());
  const d = font.getPath(applyCase(p.text ?? ""), 0, 0, 100).toPathData(2);
  return `<svg xmlns="http://www.w3.org/2000/svg"><path d="${d}"/></svg>`;
};

// ponytail: filled shapes only; a stroke-drawn icon (Lucide) extrudes as nothing — use a filled
// set (Phosphor fill/duotone, Simple Icons) for 3D, or SVGLoader.pointsToStroke if it's ever needed.
const extrude = (svg: string, depth: number): THREE.BufferGeometry | null => {
  const shapes = new SVGLoader().parse(svg).paths.flatMap((pth) => SVGLoader.createShapes(pth));
  if (!shapes.length) return null;
  const flat = new THREE.ShapeGeometry(shapes);
  flat.computeBoundingBox();
  const b = flat.boundingBox!;
  const h = Math.max(b.max.y - b.min.y, 1e-6);
  const g = new THREE.ExtrudeGeometry(shapes, { depth: depth * h, bevelEnabled: true, bevelThickness: 0.04 * h,
    bevelSize: 0.02 * h, bevelSegments: 4, curveSegments: 16 });
  g.scale(1, -1, 1);           // SVG's y points down
  g.center();
  return g;
};

/** A studio's light without an HDRI file: reflections for gloss and metal. */
const Studio: React.FC = () => {
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

const Model: React.FC<{ p: ThreeProps; obj: THREE.Object3D | THREE.BufferGeometry }> = ({ p, obj }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Weight by size: a big object enters heavier (motion.ts).
  const s = Math.min(1.05, useSpring(p.startFrame, { size: Math.max(p.widthPx, p.heightPx) }));
  const t = Math.max(0, frame - p.startFrame) / fps;
  const move = p.move ?? "turn";
  // Fit: the object's larger side fills ~80% of a 2-unit view.
  const fit = useMemo(() => {
    const box = obj instanceof THREE.BufferGeometry ? (obj.computeBoundingBox(), obj.boundingBox!)
      : new THREE.Box3().setFromObject(obj);
    const sz = box.getSize(new THREE.Vector3());
    const aspect = p.widthPx / p.heightPx;
    return 1.6 / Math.max(sz.x / aspect, sz.y, 1e-6);
  }, [obj]);
  const rotY = move === "spin" ? t * Math.PI * 0.6 : move === "turn" ? (1 - s) * -Math.PI * 0.75
    : move === "float" || move === "orbit" ? Math.sin(t * (move === "orbit" ? 0.9 : 1.4)) * (move === "orbit" ? 0.6 : 0.18)
    : 0;
  const y = move === "float" ? Math.sin(t * 2) * 0.04 : 0;
  const z = move === "push" ? (1 - s) * -6 : 0;
  const k = fit * (move === "push" ? 1 : 0.6 + 0.4 * s);
  return (
    <group position={[0, y, z]} rotation={[move === "float" ? Math.sin(t * 1.1) * 0.08 : 0, rotY, 0]} scale={k}>
      {obj instanceof THREE.BufferGeometry ? (
        <mesh geometry={obj}>
          <meshStandardMaterial color={color(p.colorToken ?? "accent")} {...finishOf(p.finish ?? "gloss")} />
        </mesh>) : <primitive object={obj} />}
    </group>
  );
};

export const Three3D: React.FC<ThreeProps> = (p) => {
  const key = `${p.model}:${p.text ?? p.name ?? p.src}`;
  const obj = useAsset<THREE.Object3D | THREE.BufferGeometry | null>(key, async () =>
    p.model === "glb" ? (await new GLTFLoader().loadAsync(staticFile(`brand/${p.src}`))).scene
      : extrude(await svgOf(p), p.depth ?? 0.3));
  return (
    <ThreeCanvas width={p.widthPx} height={p.heightPx} gl={{ alpha: true, antialias: true }}
      camera={{ position: [0, 0, 5], fov: 25 }} style={{ background: "transparent" }}>
      <Studio />
      {obj && <Model p={p} obj={obj} />}
    </ThreeCanvas>
  );
};
