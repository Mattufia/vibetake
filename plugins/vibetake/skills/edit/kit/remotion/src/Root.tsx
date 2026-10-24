import React, { useEffect, useState } from "react";
import { Composition, Still, continueRender, delayRender } from "remotion";
import { z } from "zod";
import { Graphics } from "./Graphics";
import { captionStyleSchema } from "./kit/Captions";
import cues from "./data/cues.json";
import coverPlan from "./data/covers.json";
import { fontsReady } from "./data/fonts";
import { COVERS } from "./covers";
import { COVER_SIZES, CoverProps, KEEP, Platform } from "./kit/cover";

// The graphics layer: transparent, exactly as long as the edit (size, fps, length from
// cues.json), captions on top (captionStyle: editable in Studio, saved here). Plus one still per cover concept and platform (covers.json).
const CoverHost: React.FC<CoverProps & { component: string }> = ({ component, ...p }) => {
  const [ready, setReady] = useState(false);
  const [handle] = useState(() => delayRender("project fonts"));
  useEffect(() => { fontsReady.then(() => { setReady(true); continueRender(handle); }); }, [handle]);
  const C = COVERS[component];
  if (!ready) return null;
  return C ? <C {...p} /> : null;
};

export const Root: React.FC = () => (
  <>
    <Composition id="Graphics" component={Graphics} width={cues.width} height={cues.height} fps={cues.fps}
      durationInFrames={Math.max(1, cues.frames)} schema={z.object({ captionStyle: captionStyleSchema })}
      defaultProps={{ captionStyle: { animation: "pop", yShift: 0, scale: 1, highlightColor: "", textColor: "", box: false, upper: false } }} />
    {((coverPlan as any).concepts ?? []).flatMap((c: any) =>
      ((c.platforms?.length ? c.platforms : (coverPlan as any).platforms) ?? []).map((pl: Platform) => {
        const size = COVER_SIZES[pl];
        // A short's concepts carry its name: "1" of the main video and "1" of a short never clash.
        const id = `Cover-${c.short ? c.short + "-" : ""}${c.id}-${pl}`;
        return (
          <Still key={id} id={id} component={CoverHost as any}
            width={size.width} height={size.height}
            defaultProps={{ component: c.component, platform: pl, width: size.width, height: size.height, keep: KEEP[pl] }} />
        );
      }))}
  </>
);
