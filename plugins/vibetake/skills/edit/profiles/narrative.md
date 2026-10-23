# Profile: narrative

Acted scenes: sketches, short films, acted ads (UGC with a situation, a "POV"),
anything where people play a moment instead of talking to the viewer.

- **Structure**: the scene as written — setup, turn, payoff. In an acted ad the
  call to action closes it; the hook is the situation itself, not a headline line.
- **Pacing**: the performance sets it. **`cutlist.py --pace natural --fillers keep`**:
  pauses, looks, walks and silences stay as recorded — they're timing, not dead air.
  Cut between takes and scenes, never inside a beat.
- **Cleanup**: none inside a performance. "Ah", "eh", sighs and stumbles are acting;
  a reaction is often the joke. Retakes: keep the best take, judged on performance,
  not only on the words.
- **Silent stretches (S…)**: action by default — keep them. Shorten one only when the
  user asks or it's clearly empty (camera rolling before "action", after "cut").
- **Music**: under the scene, never over the dialogue's timing; often none until the
  payoff.
- **Subtitles**: burned-in for feeds (starts muted), sentence style or highlight; off
  while a graphic says the same.
- **Graphics**: usually measured — a title, a reveal, the call to action. Nothing over the
  faces during a reaction.
- **Framing**: `reframe.py` fixed or operator; punch-ins only on cuts the scene
  already has, never invented inside a take (`--punch off` inside long takes).
- **Traps**: the automatic rules of talking heads (tight pauses, fillers out) break
  the timing. When the user says "don't cut that", protect it (`cutlist.py --protect`).
- **Color**: Basic or Full natural; a look only if the piece asks for one.
