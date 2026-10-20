// The graphics layer: every element of this video's scene plan (src/data/graphics.json),
// drawn with the kit and this video's design. Transparent everywhere else.
import React, { useEffect, useState } from "react";
import { AbsoluteFill, Sequence, continueRender, delayRender, useCurrentFrame, useVideoConfig } from "remotion";
import plan from "./data/graphics.json";
import { fontsReady } from "./data/fonts";
import { color, design, font, applyCase } from "./kit/design";
import { At, enterStyle, secondsOf, spokenTimes, useExit, useSpring } from "./kit/motion";
import { KineticText } from "./kit/KineticText";
import { Surface } from "./kit/Surface";
import { Counter, Icon, Wipe } from "./kit/Pieces";
import { CUSTOM } from "./custom";
import { Captions, CaptionStyle } from "./kit/Captions";

type El = {
  type: "headline" | "label" | "counter" | "list" | "cta" | "icon" | "custom";
  from?: At; until?: At;
  x?: number; y?: number; width?: number; size?: number; align?: "left" | "center" | "right";
  rotate?: number; surface?: boolean | Record<string, unknown>; enter?: any; unit?: any;
  [k: string]: any;
};

const Placed: React.FC<{ el: El; startFrame: number; endFrame?: number; children: React.ReactNode }> = ({
  el, startFrame, endFrame, children }) => {
  const { width, height } = useVideoConfig();
  const exit = useExit(endFrame);
  const x = (el.x ?? 0.5) * width;
  const y = (el.y ?? 0.5) * height;
  const w = (el.width ?? 0.8) * width;
  const ax = el.align === "left" ? "0%" : el.align === "right" ? "-100%" : "-50%";
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w,
      transform: `translate(${ax}, -50%) rotate(${el.rotate ?? 0}deg) scale(${0.96 + 0.04 * exit})`,
      opacity: exit, display: "flex", flexDirection: "column",
      alignItems: el.align === "left" ? "flex-start" : el.align === "right" ? "flex-end" : "center" }}>
      {children}
    </div>
  );
};

const Element: React.FC<{ el: El }> = ({ el }) => {
  const { fps, height } = useVideoConfig();
  const start = Math.round(secondsOf(el.from, 0) * fps);
  const end = el.until !== undefined ? Math.round(secondsOf(el.until) * fps) : undefined;
  const sizePx = (el.size ?? 0.05) * height;
  const { width: W } = useVideoConfig();
  const pad = el.surface ? 2 * (design.surface.padding * sizePx) : 0;
  const maxW = (el.width ?? 0.8) * W - pad;
  const cardP = useSpring(start);
  const subP = useSpring(start + 8);
  const wrapSurface = (node: React.ReactNode) =>
    el.surface ? (
      <div style={enterStyle(cardP, el.enter === "mask" ? "scale" : el.enter ?? design.motion.enter, sizePx)}>
        <Surface sizePx={sizePx} override={typeof el.surface === "object" ? (el.surface as any) : undefined}>
          {node}
        </Surface>
      </div>
    ) : node;

  let body: React.ReactNode = null;
  if (el.type === "headline") {
    const lines: string[] = el.lines ?? [el.text ?? ""];
    const words = lines.join(" ").split(/\s+/).filter(Boolean);
    const frames = el.sync ? spokenTimes(words, secondsOf(el.from, 0)).map((t) => (t === null ? null : Math.round(t * fps))) : undefined;
    body = wrapSurface(<KineticText lines={lines} startFrame={start + (el.surface ? 4 : 0)} sizePx={sizePx}
      role={el.role ?? "display"} colorToken={el.color ?? "ink"} highlight={el.highlight ?? []}
      unit={el.unit ?? "word"} enter={el.enter} wordFrames={frames} align={el.align ?? "center"} maxWidthPx={maxW} />);
  } else if (el.type === "label") {
    body = wrapSurface(
      <div style={{ textAlign: el.align ?? "left" }}>
        <KineticText lines={[el.title]} startFrame={start + 3} sizePx={sizePx} role="display" colorToken={el.color ?? "ink"}
          highlight={el.highlight ?? []} align={el.align ?? "left"} enter={el.enter} maxWidthPx={maxW} />
        {el.subtitle && <div style={{ ...font("text"), fontSize: sizePx * 0.55, color: color(el.subColor ?? "muted"),
          marginTop: sizePx * 0.15, ...enterStyle(subP, "rise", sizePx * 0.5) }}>{applyCase(el.subtitle)}</div>}
      </div>);
  } else if (el.type === "counter") {
    body = wrapSurface(<Counter to={el.to} from={el.fromValue} startFrame={start} seconds={el.seconds} sizePx={sizePx}
      prefix={el.prefix} suffix={el.suffix} decimals={el.decimals} colorToken={el.color ?? "ink"} />);
  } else if (el.type === "list") {
    body = wrapSurface(
      <div style={{ display: "flex", flexDirection: "column", gap: sizePx * 0.4 }}>
        {(el.items ?? []).map((it: any, i: number) => {
          const f = Math.round(secondsOf(it.at, secondsOf(el.from, 0) + i * 0.5) * fps);
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: sizePx * 0.4 }}>
              {it.icon && <Icon name={it.icon} sizePx={sizePx * 1.1} startFrame={f} colorToken={it.iconColor ?? "accent"}
                anim={it.iconAnim ?? "draw"} />}
              <KineticText lines={[it.text]} startFrame={f + 3} sizePx={sizePx} role={el.role ?? "text"}
                colorToken={el.color ?? "ink"} highlight={it.highlight ?? []} align="left" enter={el.enter} />
            </div>);
        })}
      </div>);
  } else if (el.type === "cta") {
    body = wrapSurface(
      <div style={{ display: "flex", alignItems: "center", gap: sizePx * 0.45 }}>
        {el.icon && <Icon name={el.icon} sizePx={sizePx * (el.iconScale ?? 1.8)} startFrame={start + 2}
          colorToken={el.iconColor} anim={el.iconAnim ?? "pop"} />}
        <KineticText lines={el.lines ?? [el.text]} startFrame={start + 6} sizePx={sizePx} role={el.role ?? "display"}
          colorToken={el.color ?? "ink"} highlight={el.highlight ?? []} align="left" enter={el.enter}
          maxWidthPx={maxW - (el.icon ? sizePx * ((el.iconScale ?? 1.8) + 0.45) : 0)} />
      </div>);
  } else if (el.type === "icon") {
    body = <Icon name={el.name} sizePx={sizePx} startFrame={start} colorToken={el.color} anim={el.anim ?? "pop"} />;
  } else if (el.type === "custom") {
    const C = CUSTOM[el.component];
    body = C ? <C {...(el.props ?? {})} startFrame={start} endFrame={end} sizePx={sizePx} /> : null;
  }
  if (el.wipe) body = <Wipe startFrame={start} from={el.wipe}>{body}</Wipe>;
  return <Placed el={el} startFrame={start} endFrame={end}>{body}</Placed>;
};

export const Graphics: React.FC<{ captionStyle: CaptionStyle }> = ({ captionStyle }) => {
  const { fps } = useVideoConfig();
  // Nothing is drawn (or measured) before the project's fonts are in: text fits by measuring them.
  const [ready, setReady] = useState(false);
  const [handle] = useState(() => delayRender("project fonts"));
  useEffect(() => {
    fontsReady.then(() => { setReady(true); continueRender(handle); });
  }, [handle]);
  if (!ready) return null;
  const els = ((plan as any).elements ?? []) as El[];
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {els.map((el, i) => {
        const start = Math.max(0, Math.round(secondsOf(el.from, 0) * fps) - 2);
        const end = el.until !== undefined ? Math.round(secondsOf(el.until) * fps) + 1 : undefined;
        return (
          <Sequence key={i} from={0} durationInFrames={end !== undefined ? end : undefined} layout="none">
            <Visible from={start}><Element el={el} /></Visible>
          </Sequence>
        );
      })}
      <Captions style={captionStyle} />
    </AbsoluteFill>
  );
};

const Visible: React.FC<{ from: number; children: React.ReactNode }> = ({ from, children }) => {
  const frame = useCurrentFrame();
  return frame >= from ? <>{children}</> : null;
};
