// This video's cover concepts: one component per concept, written for it (a free canvas:
// any composition, the kit's pieces, inline SVG, the cut-out person, its own colors).
// Listed in 02-work/covers.json as {"id": "1", "component": "Name", "idea": "…"}; each
// receives CoverProps (platform, width, height, keep = the area that survives the crop).
import type React from "react";
import type { CoverProps } from "../kit/cover";

export const COVERS: Record<string, React.FC<CoverProps>> = {};
