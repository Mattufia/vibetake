# Profile: podcast-interview

Conversations with two or more voices: podcasts, interviews, panels.

- **Structure**: full episode in order; for clips, one complete idea per clip,
  with a hook from inside the conversation.
- **Pacing**: respect natural pauses and reactions; only cut long dead air and
  derailments. Clips for a feed follow the `talking-head` profile.
- **Cleanup**: light in the full episode (stumbles, long fillers), full in clips.
- **Picture**: with a camera per person, choose the angle per segment by hand (the one
  speaking, or a reaction) — `speakers.md` says who says each line.
- **Music**: intro/outro only; none under the conversation.
- **Subtitles**: `.srt` for the full episode; burned-in for clips.
- **Graphics**: usually measured; each person identified (name and role) the first time they speak; clips for a feed follow `talking-head`.
- **Traps**: crosstalk — don't cut one voice in the middle of another's sentence;
  levels per voice must match.
- **Color**: Basic: matching the cameras is the priority (two angles that don't match are very visible — one
  match group; `--no-skin` if the angles show different people); Full if skin is off.
- **Framing**: several people in one shot → `reframe.py` keeps the group when it fits and
  otherwise follows who speaks, sentence by sentence (SKILL.md section 3).
