import React from "react";
import { Composition } from "remotion";
import { Graphics } from "./Graphics";
import cues from "./data/cues.json";

// One composition: the whole graphics layer, transparent, exactly as long as the edit.
// Its size, fps and length come from cues.json (graphics.py cues), so it always matches.
export const Root: React.FC = () => (
  <Composition
    id="Graphics"
    component={Graphics}
    width={cues.width}
    height={cues.height}
    fps={cues.fps}
    durationInFrames={Math.max(1, cues.frames)}
  />
);
