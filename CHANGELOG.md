# Changelog

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
