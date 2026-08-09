# Changelog

## 0.2.0

Full-reference mode, from
[`references/ref-en.txt`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt).
Until now a reference image was always a character, always followed as tightly as
possible, and a timeline image was always a frame anchor. The guide describes far more
than that, and two of its sections were structurally missing from the output.

- **A reference no longer has to be a character.** The guide defines `<Subject N>` as
  "people, animals, or objects; scenes, backgrounds, or environments; clothing, props,
  interfaces, or visual effects; styles, actions, expressions, or poses". Each slot now
  carries a **kind** that supplies the noun when no description is written, so a slot can
  hold a location or a look instead of a face. The panel grows from three slots to nine as
  you fill it, and `@ref1` … `@ref9` address them. `@char1` … `@char3` still resolve.

- **A reference no longer has to be followed exactly.** Every reference carries one of the
  guide's four **retention markers**, written into `retention_analysis` verbatim because
  the guide calls them "fixed English values in the output format":

  | Marker | Meaning |
  |---|---|
  | `fully_preserved` | The defined role of the referenced content is fully preserved |
  | `partially_preserved` | Still used, some defined characteristics changed |
  | `attribute_transfer` | Its characteristics move to a different target subject |
  | `weak_reference` | Broad similarity in style, category, composition or atmosphere only |

  Audio uses its own set — `fully_copy`, `partially_copy`, `reference`, `weak_reference` —
  because copying a signal and imitating one are different jobs. An off-spec value coming
  from an edited timeline is clamped rather than passed through into the prompt.

- **An image only gets a `<Picture N>` entry when it really is one.** The guide: "If an
  image is used only to define a character, scene, costume, or style, do not create a
  standalone picture entry. Instead, cite the image source inside the corresponding
  `<Subject N>` definition." A timeline image can now be a **frame anchor** (unchanged),
  a **storyboard** reference, or **subject-defining** — the last getting no picture entry
  and, because it is no longer a keyframe, no longer cropped to the output canvas either.

- **`subject_definitions` declares every label; `retention_analysis` scores every label.**
  Both sections were previously incomplete: pictures were never declared, and retention
  was prose. The two sections now follow the guide's shapes, and a subject's
  `(appears in [Shot 1], [Shot 3])` is read back off the shot text rather than assumed.

  ```
  0.1.5   subject_definitions: <Subject 1> is the character shown in <Picture 1>.
          retention_analysis: Keep the identity, face and clothing of <Subject 1>
                              consistent across every shot. [Shot 1] begins from <Picture 2>.

  0.2.0   subject_definitions: <Subject 1> is a woman in a red coat, shown in <Picture 1>.
                               <Picture 2> is the first frame of [Shot 1].
          retention_analysis:
          <Subject 1> (appears in [Shot 1]): fully_preserved - the identity, face and
            clothing of <Subject 1> are retained.
          <Picture 2> ([Shot 1] first frame): fully_preserved - the framing and
            composition of <Picture 2> are retained.
  ```

- **New `summary` section with a derived `[task type]` prefix** — `keyframe completion`,
  `reference generation`, `audio reuse`, `audio reference`, combined with ` + ` in the
  guide's own order. It is derived from what the references are *used for*, not from what
  is connected: the guide warns that "the mere presence of video or audio does not
  automatically create a corresponding task type", so a reference video supplying only
  camera movement stays `reference generation`. The gear menu's **Task Type** field
  overrides it, which is the only way to reach `video editing` and `video continuation` —
  neither of which this node can produce on its own.

- Frame anchors are also named inside their shot, as the guide's section 5.3 asks:
  `[Shot 1] she enters. The shot begins from <Picture 2>.` The phrase goes after the
  shot's own text, because a later shot opens `At 00:05.000, ` and a capitalised clause
  cannot continue out of that comma.

- **The chain node and the Director now share one reference loader.** The chain had grown
  its own copy that ignored the `ref_images` socket entirely and never fitted a keyframe to
  the canvas, so a chained render silently dropped references the Director would have sent.

- **The live preview says when it cannot count.** Images arriving on the `ref_images`
  socket are an upstream batch that does not exist until the graph runs, so the preview
  could not number around them and silently showed `<Picture 2>` where the render would
  send `<Picture 5>`. It now warns instead of quietly disagreeing.

- Removed a note-pruning path that parsed `<Picture N>` back out of finished sentences to
  drop trimmed references. Declarations are now built after the caps have trimmed, so
  there is nothing to prune — and the per-type caps made that path unreachable anyway
  (`images + videos` is at most 12 on its own, so the audio bucket always absorbs the
  excess).

## 0.1.5

- **Picture notes in `Refs ON` use the reference guide's own phrasing**
  ([#4](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/4)).
  `VIDEO_PROMPT_WRITING_GUIDE_ref_en.md` gives it verbatim — "the shot begins from
  `<Picture 1>`", "the shot's keyframe corresponds to `<Picture 2>`", "the shot ends on
  `<Picture 3>`" — and asks for a standalone `<Picture N>` exactly when an image "serves
  as a shot's first frame, keyframe, last frame, edited keyframe, or composition anchor".
  So ref2va does carry frame anchors in its notation; what it does not carry is FL2VA's
  vocabulary. 0.1.4 dropped the anchors along with the wrong words, which threw away
  information the guide wants stated:

  ```
  0.1.3   <Picture 2> is the opening frame.                    FL2VA's words
  0.1.4   <Picture 2> is the timeline image at 0s (a.png).      no anchor at all
  0.1.5   [Shot 1] begins from <Picture 2>.                     the ref guide's words
  ```

  Middle images keep their timestamp: `The keyframe of [Shot 2] corresponds to
  <Picture 3>, at 6s.` Shots are numbered the way the body numbers them — counting only
  shots that carry text — and an image whose segment has no text gets shot-free phrasing
  rather than a number the reader cannot find. Filenames are gone from the notes; the
  guide has no such notion and the model gains nothing from `b.png`.
- The phrasing still does not flip on where a segment happens to end, which was the
  reported bug: an image flush with the window and the same image three frames shorter
  now differ only in the role the guide would give them anyway.
- **When a sound box wins over an `Audio:` / `Music:` line, the log says so.** That line
  may be work the Enhance node's vision model just did, and discarding it in silence was
  wrong even though the precedence is right.

## 0.1.4

- **`overall_soundscape` and `non_diegetic_music` have their own boxes** under the Global
  Prompt, which is what both prompting guides ask for
  ([#7](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/7)). They
  live in the timeline, so the COMPILED PROMPT panel and the node read one and the same
  value — node widgets would have meant a third copy to keep in step. Empty boxes emit no
  section at all. `Audio:` / `Music:` lines in the prompt text are still lifted into the
  same two sections, so older workflows and the Enhance node are unaffected; a filled box
  wins over a lifted line. The boxes do not switch with Retake Mode: re-rolling a range
  does not change what the room sounds like.
- **`Refs ON` no longer calls a timeline image an opening or closing frame**
  ([#4](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/4)). ref2va
  has no keyframe slot, so that wording promised an anchor the checkpoint cannot honour.
  Worse, it depended on where a segment happened to end: an image flush with the end of
  the window read as a closing frame, the same image three frames shorter read as a
  timeline image, and nudging the segment was the only way to get sane wording. Every
  timeline image is now described by the time it sits at. The role is still tracked
  internally, where it decides which frame of a *video* segment is used.
- **The alignment line's end mark is floored to the hundredth, not rounded**
  ([#6](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/6)). 124
  frames last 5.166667 s and were reported as `5.17`, a moment past the end of the clip
  being described. Ten of the twenty-four valid frame counts up to 16 s rounded that way.
  Prompts for those lengths change by one hundredth, so a fixed seed will not reproduce a
  0.1.3 render exactly.
- **A connected `duration` of 0 now fails with a message that names it**, instead of
  clamping to one timeline frame and rendering five in silence. That is what an upstream
  node hands over when its own value was never set, and 0.1.3's new `duration_seconds`
  output is meant to be wired exactly there. `end` before `start` and a negative `start`
  are refused the same way.
- The over-length warning no longer points at the Director Chain node, which was withdrawn
  in 0.1.2, and quotes the trained range as 4-15 s to match the model card.
- `test_plan.py` ships with the package: 86 offline checks over the planner, no server
  needed.

## 0.1.3

- **New node: MiniMax H3 Enhance Prompt.** A local vision model (Ollama / LM Studio / any
  OpenAI-compatible endpoint) turns up to nine reference images plus a one-line idea into
  prompt text for the Director's `global_prompt`, and passes the same images on to
  `ref_images` so it describes exactly what H3 will condition on. `duration_seconds` is an
  output too, so it is typed once
  ([#1](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/1)).
  Image sockets grow as you connect and close their gap again when you disconnect.
- The vision model is evicted from VRAM when the node finishes, including when the call
  failed part way — that is exactly when one would otherwise be left resident while H3
  starts sampling. Switchable off while iterating.
- Two prompt presets: `global` leaves the shots to your timeline, `storyboard` writes the
  whole shot sequence with timestamps.
- The model's output is filtered against what the Director owns: section labels,
  `<Picture N>` numbering and — in `global` mode — shot markers are removed, the first
  shot's timestamp is dropped in `storyboard` mode, and the length is trimmed to a
  sentence boundary. Small models do not follow those rules from instructions alone;
  measured examples are in the commit history.
- The `Audio:` / `Music:` lines are requested in a second short call when the first answer
  leaves them out. With `qwen3.5:9b` that moved them from 0 of 4 runs to 4 of 4, so the
  Director's `overall_soundscape` and `non_diegetic_music` actually get filled.
- An address without a scheme (`127.0.0.1:11434`) is accepted rather than rejected by the
  HTTP layer, in the node and in the gear menu's Analyze button alike. `on_error =
  passthrough` now catches everything, not just VLM errors — the guard that exists to keep
  a broken endpoint from killing a render was not catching the case that actually happened.

## 0.1.2

- **Reference images are numbered along the timeline.** `<Picture N>` now counts up with
  time — opening frame, whatever sits in between, closing frame. Previously the keyframes
  were assigned in a second pass, so an image dropped in the middle took `<Picture 1>` and
  pushed the opening frame to `<Picture 2>`
  ([#5](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/5)).
  Character slots keep their numbers ahead of the timeline, so a character never renumbers
  when you drop an image on a track.
- **`Refs OFF` prompts carry the image-alignment instruction** the base prompt guide
  requires as their first line, in the exact wording MiniMax documents for I2VA, FL2VA and
  L2VA. T2VA has none, and the reference guide does not ask for one, so `Refs ON` is
  unchanged ([#6](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/6)).
  The duration it names is the effective one, after snapping to the 17k+5 grid.
- **Director Chain is withdrawn.** Its sampling worked, but there was no usable way to
  give it a timeline: the editor attaches only to the Director, which has no
  `timeline_data` output to wire from. Shipping a feature nobody can operate is worse than
  shipping none ([#4](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/4)).
  The code and the full reasoning stay in `minimax_chain.py`.
- Retake Stitch passes the video through unchanged when there is no retake, instead of
  failing.
- Fixed: when the 12-file reference cap trimmed several images, only the first one's note
  was removed from the prompt.

## 0.1.1

- **Only the checkpoint the toolbar asks for is loaded.** Both model inputs are now lazy
  (`check_lazy_status`), so `Refs OFF` never reads `ref2va` and `Refs ON` never reads
  `fl2va`. Before this, ComfyUI resolved both inputs before the node ran and read ~42 GB
  of weights to use half of them — enough to push a 32 GB machine into a page-file crash
  while the text encoder was still loading ([#2](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues/2)).
  Connecting only one model still works, with the same warning as before.

## 0.1.0 — first public release

The LTX Director timeline editor by WhatDreamsCost, ported to MiniMax H3.

**Director**
- Timeline compiles to a storyboard prompt instead of a cross-attention relay mask
  (H3's DiT hardcodes `mask=None` over one packed `[text | cond | audio | video]` sequence).
- Two prompt notations, switchable in the gear menu: **MiniMax** (the notation from
  MiniMax's own prompt-writing guide, default) and **ComfyUI** (`[0s-1.5s] …`).
- Two optional model inputs — `fl2va` and `ref2va` are separate trainings; the toolbar
  switch picks the matching one.
- First/last keyframes only, matching H3's `PackedLayout`. Images elsewhere become
  `<Picture i>` references in Refs ON mode and are reported in the warnings otherwise.
- Reference-video track (`<Video k>`) replaces the IC-LoRA track — no IC-LoRAs exist for H3.
- Native joint audio; imported audio becomes `<Audio j>` and/or is muxed via `combined_audio`.
- Model-card limits enforced: ≤ 9 images, ≤ 3 videos (2–15 s each, ≤ 15 s total),
  ≤ 3 audio clips, ≤ 12 files in total.
- Live **COMPILED PROMPT** panel, served by the same planner the node runs — it cannot
  drift from what is actually encoded. Collapsing it, or switching it off in the gear
  menu, shrinks the node by exactly that much and releases the canvas underneath.

**Preview Override**
- Renders the whole shot while it denoises, instead of core's single first latent frame.
- Unpacks H3's packed AV latent (`unpack_latents`) — the callback receives the flat pack,
  not the nested view.
- Playback rate derived from the *output* duration, so the preview lasts as long as the
  finished shot (H3 compresses time ~3.35×).
- `latent2rgb` or the real video VAE, with a render-time overhead budget.
- `preview_fps` is a FLOAT input, so the Director's `fps` output wires straight in.

**Retake Stitch**
- Regenerate a marked range anchored on the base video's own frames either side, then
  splice head + retake + tail back together, video and audio.

**Director Chain**
- Renders past H3's ~15 s training range by chaining in-range windows, each anchored on
  the previous window's final frame. Samples internally; outputs finished images + audio.

**Removed from the LTX original**
- IC-LoRA track, Prompt Relay, audio inpainting, Licon MSR / Ghost Mask reference modes —
  none of them have a MiniMax H3 equivalent.
