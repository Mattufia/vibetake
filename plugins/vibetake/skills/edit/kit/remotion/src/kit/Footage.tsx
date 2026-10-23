// The edit's own picture (graded, with b-roll, no graphics or subtitles), frame-locked to the
// timeline, so a stage can place it anywhere: inside a window, a split, the top half.
// graphics.py sync puts it in public/footage.mp4 whenever the plan has a stage.
// ponytail: frame-locked only inside a Sequence that starts at 0 (as Graphics draws every element);
// a component that nests it in a later Sequence must pass startFrom itself.
import React from "react";
import { OffthreadVideo, staticFile } from "remotion";

export const Footage: React.FC<{ fit?: "cover" | "contain"; position?: string; style?: React.CSSProperties }> = ({
  fit = "cover", position = "50% 50%", style }) => (
  <OffthreadVideo src={staticFile("footage.mp4")} muted
    style={{ width: "100%", height: "100%", objectFit: fit, objectPosition: position, display: "block", ...style }} />
);
