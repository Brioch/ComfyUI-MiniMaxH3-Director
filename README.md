# ComfyUI MiniMax H3 Director

**A timeline editor for [MiniMax H3](https://huggingface.co/Comfy-Org/MiniMax-H3) inside ComfyUI.**
Drag images, videos and music onto tracks, trim them on a ruler, write a prompt per shot,
press Run. Instead of one prompt box for a whole clip you get a storyboard — and you can
see the exact prompt the model will receive while you are still editing it.

[![license](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-%E2%89%A5%200.30.0-1a1a1a)](https://github.com/comfyanonymous/ComfyUI)
[![version](https://img.shields.io/badge/version-0.2.0-brightgreen)](CHANGELOG.md)

![The MiniMax H3 Director node](docs/images/director-node.png)

<!-- TODO: demo video -->

> This is the [LTX Director](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI)
> timeline editor by **WhatDreamsCost**, ported to MiniMax H3. Same editing, new backend.
> See [Credits](#credits).

---

## Contents

- [News](#news)
- [Why](#why)
- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Installation](#installation)
- [Models](#models)
- [Quick start](#quick-start)
- [The timeline](#the-timeline)
- [Prompt format](#prompt-format)
- [Live preview while sampling](#live-preview-while-sampling)
- [Writing the prompt for you](#writing-the-prompt-for-you)
- [Retake Mode](#retake-mode)
- [Longer than 15 seconds](#longer-than-15-seconds)
- [Troubleshooting](#troubleshooting)
- [Reporting a bug](#reporting-a-bug)
- [Contributing](#contributing)
- [Credits](#credits)
- [License](#license)

---

## News

**0.2.0** · 2026-08-09 — full reference mode. A reference is no longer always a character
and no longer always followed exactly: subject slots carry a **kind** (scene, prop, style,
…) and every reference carries a **retention marker** from `fully_preserved` to
`weak_reference`, plus a box to write the sentence that follows it in your own words.
Timeline images can be frame anchors, storyboard references, or subject-defining images
that get no `<Picture>` entry at all. Prompts gain a `summary` section with a derived
`[task type]` prefix, and `retention_analysis` uses the guide's own line format. Dialogue
written as `@ref1 says: …` is given speaker IDs and `<d>` tags for you. The reference panel
resizes, with the extra height going to the image previews.

**0.1.5** · 2026-08-06 — picture notes in `Refs ON` now use the reference guide's own
phrasing for frame anchors: `[Shot 1] begins from <Picture 1>`, `ends on`, and
`The keyframe of [Shot 2] corresponds to …`.

**0.1.4** · 2026-08-06 — `overall_soundscape` and `non_diegetic_music` have their own boxes
under the Global Prompt, and the alignment line's end mark can no longer name a moment past
the end of the video.

**0.1.3** · 2026-08-06 — new **MiniMax H3 Enhance Prompt** node: a local vision model turns
reference images plus a one-line idea into a prompt for the Director, and hands the same
images on so it describes exactly what H3 will condition on.

**0.1.2** · 2026-08-06 — reference images are numbered along the timeline again, and prompts
in `Refs OFF` now carry the image-alignment instruction MiniMax's guide requires. The
Director Chain node is withdrawn until it can actually be operated.

**0.1.1** · 2026-08-04 — only the checkpoint the toolbar asks for is loaded, instead of both
model inputs reading ~42 GB of weights to use half of them.

**0.1.0** · 2026-08-04 — first public release: the LTX Director timeline editor by
WhatDreamsCost, ported to MiniMax H3.

Full history in the [changelog](CHANGELOG.md).

---

## Why

MiniMax H3 generates video **and** audio jointly, takes reference images, videos and audio,
and anchors on a first and last frame. All of that is reachable through core ComfyUI
nodes — but you address it by hand-writing a storyboard prompt, counting frames onto a
17k+5 grid, and wiring conditioning nodes for every reference.

This node turns that into an editor. Segments on a track become shots with timestamps.
Images dropped on the track become keyframes or `<Picture i>` references. Audio becomes
either a reference or the muxed soundtrack. The prompt is compiled for you, live, and you
can read it before you spend a render on it.

## What you get

Four nodes, category **MiniMax H3**:

| Node | What it does |
|---|---|
| **MiniMax H3 Director** | The timeline. Outputs a patched `model`, the compiled `positive` conditioning, an empty joint AV `latent`, the muxed `combined_audio`, plus `fps` / `width` / `height` / `length` / `prompt` / `retake_info`. |
| **MiniMax H3 Preview Override** | Watch the whole shot denoise, not a single frozen frame. |
| **MiniMax H3 Retake Stitch** | Splices a regenerated range back into the base video. |
| **MiniMax H3 Enhance Prompt** | A local vision model writes the prompt from your reference images. |

Editing features carried over from LTX Director: main track, reference-video track, audio
track, ruler in seconds or frames, drag / resize / copy / paste, prompt zones per segment,
waveform preview, filename labels, gear menu, workspace folder, chunked upload for large
videos, drag-and-drop straight onto the node, and the `@ref1` … `@ref9` subject
slots including the optional local VLM "Analyze" button (Ollama / LM Studio /
any OpenAI-compatible endpoint) with automatic VRAM release before a run.

## Requirements

* **ComfyUI ≥ 0.30.0** — H3 support, `comfy_api.latest` and the packed AV latent all
  landed in 0.30. Older builds will fail to load the nodes.
* **Python 3.10+** (ComfyUI's own environment; the portable build's `python_embeded` is fine).
* **No extra pip packages.** Everything the nodes import ships with ComfyUI already.
* **VRAM:** the fp8 checkpoints are ~21 GB on disk. 16 GB VRAM works with ComfyUI's
  offloading at 480p–768p; below that expect heavy swapping. The text encoder is a
  separate ~15 GB load.
* **Disk:** budget ~60 GB if you want both model paths plus the text encoder and VAEs.

## Installation

### Via ComfyUI Manager (recommended)

1. Open **Manager → Custom Nodes Manager**
2. Search for **MiniMax H3 Director**
3. **Install**, then restart ComfyUI and reload the browser tab.

Not listed yet? Use **Manager → Install via Git URL** and paste:

```
https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director
```

### Manual

Clone into your `custom_nodes` folder and restart:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director
```

On the Windows portable build the folder is
`ComfyUI_windows_portable\ComfyUI\custom_nodes`.

There is **nothing to pip install** — the package declares no third-party dependencies.

Then restart ComfyUI **and hard-reload the browser** (Ctrl+F5). The timeline is a
frontend extension; a stale cached `.js` is the single most common "node looks broken"
report.

### Updating

```bash
cd ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-Director
git pull
```

## Models

Download from [🤗 Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3).
The example workflow carries download links, so ComfyUI can offer to fetch them for you.

**Put the files directly in these folders — no subfolder.** A file at
`models/diffusion_models/MiniMax-H3/…` will not match the example workflow.

```
ComfyUI/models/
├── diffusion_models/
│   ├── minimax_h3_fl2va_pruned_fp8_scaled.safetensors     21 GB   ← text/keyframe path
│   └── minimax_h3_ref2va_pruned_fp8_scaled.safetensors    21 GB   ← reference path
├── text_encoders/
│   └── qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors       15 GB
└── vae/
    ├── minimax_h3_video_vae_fp16.safetensors             4.9 GB
    └── minimax_h3_audio_vae_fp32.safetensors             0.6 GB
```

**The two diffusion checkpoints are not interchangeable — they are separate trainings:**

| Toolbar switch | Checkpoint | Use it for |
|---|---|---|
| **Refs OFF** | `minimax_h3_fl2va_*` | text→video, and first/last keyframes from the timeline |
| **Refs ON** | `minimax_h3_ref2va_*` | subject slots, reference images, reference videos, reference audio |

Connect both to the Director's two model inputs and the toolbar switch picks the right
one. Connecting only one is fine — the node warns rather than silently using the wrong path.

Only the selected checkpoint is ever read from disk: the model inputs are lazy, so with
the toolbar on **Refs OFF** the `ref2va` loader never runs at all. Wiring both costs you
disk space, not RAM.

Other quantisations on the repo work too: `*_bf16` (66 GB, best quality),
`*_int8_convrot` (34 GB), `*_pruned_int8_convrot` (21 GB). The text encoder also comes as
`_bf16` and `_int8_convrot` if `nvfp4_awq` does not run on your GPU.

## Quick start

1. **Workflow → Open** → `example_workflows/MiniMax H3 Director.json`
   (or drag [`docs/images/workflow-overview.png`](docs/images/workflow-overview.png)
   onto the canvas — the same graph is embedded in that screenshot)
2. Fix any red nodes — usually the model dropdowns, if your filenames differ.
3. Double-click a segment on the main track and type what should happen.
4. Drag an image onto the track for a first-frame anchor (optional).
5. **Run.**

Read the **COMPILED PROMPT** panel under the timeline before running: it shows the exact
text the model will get, the shot count, the frame count, the reference tally and the
`detailed_description` word count, plus warnings for the things that silently bite.

The word count is there because the guide suggests **350–500 words** for generation tasks,
which is more than most people write. It is a figure, not a verdict — being under it is
perfectly normal for a short clip, and the guide itself warns against "mechanical
word-count adherence". Only going *past* 500 raises a warning.

Defaults that matter, if you wire it yourself:

* `CLIPLoader` **type must be `minimax`**.
* Sampler `res_multistep`, scheduler `simple`, ~20 steps, through `BasicGuider` (no CFG).
  For reference-heavy `ref2va` prompts, `beta` or `normal` often beats `simple`.
* The joint latent goes to **both** `VAEDecode` (video VAE) **and** `VAEDecodeAudio`
  (audio VAE); each pulls its own half out. `CreateVideo` muxes them.
* Length snaps up to H3's 17k+5 frame grid at 24 fps — 5 s becomes 124 frames (5.17 s).
* Native canvas is a 768 px short edge, capped at 768×1344.

```
UNETLoader ×2 ─┐
CLIPLoader   ─┼→ MiniMax H3 Director ─┬→ model ──→ BasicGuider ─→ SamplerCustomAdvanced
VAELoader ×2 ─┘                       ├→ positive ┘                      │
                                      ├→ latent ─────────────────────────┘
                                      ├→ combined_audio → CreateVideo.audio
                                      └→ fps ───────────→ CreateVideo.fps
```

![The example workflow](docs/images/workflow-overview.png)

The example packs the sampler and the two decoders into subgraphs so the graph stays
readable; open them if you want to change sampler, scheduler or steps.

## The timeline

| Track | Drop this | Becomes |
|---|---|---|
| **Main** | images | first/last keyframe (Refs OFF) or `<Picture i>` (Refs ON) |
| **Main** | prompt zones | `[Shot N]` entries with timestamps |
| **Reference video** | video clips | `<Video k>` motion/style references |
| **Audio** | music, SFX | `<Audio j>` reference and/or the muxed soundtrack |
| **Subject slots** | images | `<Subject N>` definitions — people, scenes, props, styles |

### Reference limits

From MiniMax's own model card — not from ComfyUI's node signatures, which are looser.
These are enforced, with a warning naming exactly what was dropped:

| Limit | Value |
|---|---|
| Reference images | ≤ 9 — the subject slots *and* the `ref_images` input share this pool |
| Reference videos | ≤ 3 clips, each 2–15 s, **≤ 15 s total** |
| Reference audio | ≤ 3 clips |
| **All types together** | **≤ 12 files** |

Output envelope: 4–15 s at 24 fps. Aspect ratios 21:9, 16:9, 4:3, 1:1, 3:4, 9:16.

**Video formats**: anything your ComfyUI can decode. The editor previews a reference video
in the browser, which is fussier than the renderer — HEVC, ProRes and 10-bit footage inside
an ordinary `.mp4` or `.mov` are commonly refused. When that happens the server reads the
file instead and the clip lands on the track as usual; you may lose the filmstrip preview,
never the clip. If the server cannot read it either, you get a message saying so.

Anything you drop on a track is uploaded to `ComfyUI/input/whatdreamscost/`. That is the
same folder LTX Director uses, deliberately — if you run both, assets and saved timelines
carry over between them.

### Subject slots and the Analyze button

Drop an image into a slot and write `@ref1` in a prompt; it expands to `<Subject 1>`
(MiniMax notation) or `<Picture 1>` (ComfyUI notation) and the image is attached as a
reference. This is the **Refs ON (ref2va)** path. `@char1` … `@char3` still work, so
prompts written against the old three-slot panel keep resolving.

A slot is **not** only for characters. The reference guide defines `<Subject N>` as any
reusable visible content — "people, animals, or objects; scenes, backgrounds, or
environments; clothing, props, interfaces, or visual effects; styles, actions,
expressions, or poses" — so each slot carries a **kind** telling the prompt what it is:

| Control | What it does |
|---|---|
| **kind** | Supplies the noun in `<Subject N> is the environment shown in <Picture 1>.` A typed description replaces it entirely. |
| **retention** | How closely to follow it. Written into `retention_analysis` verbatim. |

Slots start at three and a new empty one appears as you fill them, up to the nine-image
cap.

### How closely a reference is followed

Every reference carries a **retention marker** — the guide's term for "exactly or
loosely". These are fixed English values written straight into the prompt, so the
dropdowns show them under their own names rather than friendlier ones:

| Marker | Meaning |
|---|---|
| `fully_preserved` | The defined role of the referenced content is fully preserved |
| `partially_preserved` | Still used, but some defined characteristics change |
| `attribute_transfer` | Its characteristics move onto a different target subject |
| `weak_reference` | Broad similarity in style, category, composition or atmosphere only |

Audio has its own set, because copying a signal and imitating one are different jobs:
`fully_copy`, `partially_copy`, `reference`, `weak_reference`.

Right-click any reference — a timeline image, a reference video, an audio clip — to set
its marker. Subject slots have theirs in the panel.

### Saying what is retained

The marker is only half the line. After it comes a sentence naming what actually has to
survive, and the guide's own example is specific rather than generic:

```
<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - the Samoyed's thick white
fur, pointed ears, dark nose, and curved tail are retained.
```

Every reference has boxes for exactly that — up to two, one per section of the prompt it
feeds:

| Box | Becomes |
|---|---|
| **describes** | the reference's line in `subject_definitions` — what the thing *is* |
| **retained** | the sentence after the marker in `retention_analysis` — what must *survive* |

Subject slots carry both in the panel. Timeline images, reference videos and audio clips
carry theirs in the properties panel when selected. Leave either empty and a sentence is
generated instead — the boxes are overrides, never obligations.

**describes** is what lets you write relationships the timeline cannot work out. The guide
links a voice reference to the speaker it belongs to:

```
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
```

Type the part after `is` — the label is added for you, so it cannot come out wrong or
doubled. Paste a whole line that already starts with its label and it is taken as written.

Frame anchors and storyboard references are the one exception: they have **retained**
alone. Their declaration states where the image sits in the video (`<Picture 2> is the
first frame of [Shot 1].`), which the timeline already knows.

### Dialogue

A line in a shot prompt that **starts** with a reference tag and contains a colon is
dialogue. Everything between the tag and the colon is how it is delivered:

```
@ref1 exclaims with light annoyance: Hey! Watch your dog!
```

becomes

```
<Subject 1> (S1) exclaims with light annoyance, <d>[English] Hey! Watch your dog!</d>
```

`(S1)` is a **speaker ID**. You never write one: they are handed out in the order people
actually speak across the whole timeline, and the same speaker keeps the same ID at every
later line — so a subject who talks in shots 1 and 3 is `(S1)` in both. They are also kept
out of `retention_analysis`, which the guide forbids; type one into a **retained** box and
the preview says so.

| Write | For |
|---|---|
| `@ref1 says: …` | a subject from the panel — the delivery defaults to `says` |
| `@ref1 [French] murmure: …` | another language; `[English]` is assumed |
| `@voice(a low male narrator) says: …` | someone with no panel slot. Reuse the same description and they keep one ID |
| `@audio2: …` | words carried by a reused track. Names `<Audio 2>` as the source and gets **no** speaker ID, per the guide |

Only a line that *starts* with a tag counts, so prose that merely mentions `@ref1` or
contains a colon is left alone — the same rule the `Audio:` / `Music:` lines follow. A shot
whose only content is a spoken line is still a numbered shot.

`<scenetrans>` and `<cutoff>`, for dialogue crossing a cut or speech that is cut short, are
passed through untouched if you type them.

### Resizing the panel

The reference panel drags from the strip along its bottom edge, like the prompt and global
prompt panels. All the extra height goes to the image previews rather than the text boxes,
so drag it taller when you need to actually see what you are referencing. The height is
remembered per node.

### What an image is *for*

The guide only gives an image its own `<Picture N>` entry when the image really is a
frame. Right-click a timeline image to say which of the three it is:

| Used as | Result |
|---|---|
| **frame anchor** (default) | `<Picture 2> is the first frame of [Shot 1].` — its position on the timeline decides first / last / keyframe |
| **storyboard** | `<Picture 3> is a storyboard reference for [Shot 2], defining its viewpoint, subject placement, and shot order.` |
| **defines a subject** | No `<Picture>` entry at all. Cited inside a `<Subject N>` line instead, exactly as the guide requires for an image that "is used only to define a character, scene, costume, or style". |

A subject-only image also stops being a keyframe, so it is no longer fitted to the output
canvas — the full reference reaches the model instead of a cropped one.

**Analyze** is optional and off the critical path. It sends the slot image to a local
vision model and pastes back a one-line description, so `@ref1` still means something in
**Refs OFF** mode, where H3 gets no image at all. Nothing is installed for you and nothing
is sent anywhere unless you press the button.

To use it, run a vision model locally and point the gear menu's provider row at it:

| Provider | Default URL | Set up |
|---|---|---|
| Ollama | `http://127.0.0.1:11434` | `ollama pull qwen2.5vl:7b` — any vision model works, the field is free text |
| LM Studio | `http://127.0.0.1:1234` | load a vision model, start the local server |
| Custom | — | any OpenAI-compatible `/v1/chat/completions` endpoint |

With Ollama the node also asks it to unload the model before a render, so the VLM does not
sit in VRAM while H3 samples.

**Keyframes go on the first and last frame only.** H3's `PackedLayout` anchors exactly
those two positions; an image stranded in the middle of a window is reported in the
warnings rather than silently ignored.

## Prompt format

Gear menu → **Prompt Format**. The default is **MiniMax**, the notation from their own
`VIDEO_PROMPT_WRITING_GUIDE`:

```
subject_definitions:
<Subject 1> is a baker in a flour-dusted apron, shown in <Picture 1>.
<Subject 2> is the environment shown in <Picture 2>.
<Picture 3> is the first frame of [Shot 1].

summary: [keyframe completion + reference generation] The target video follows <Subject 1>
opening the bakery.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - the flour-dusted apron and the wire-rimmed glasses are retained.
<Subject 2> (appears in [Shot 1]): weak_reference - only a broad similarity to <Subject 2> in style, category, composition and atmosphere is kept.
<Picture 3> ([Shot 1] first frame): fully_preserved - the framing and composition of <Picture 3> are retained.

detailed_description: Live-action, cinematic. [Shot 1] the baker opens the shutters.
The shot begins from <Picture 3>. [Shot 2] At 00:01.500, <Subject 1> lifts the loaf onto
the counter

overall_soundscape: street ambience, a distant tram
non_diegetic_music: soft piano
```

The first shot carries no timestamp; every later cut carries a strictly increasing
`MM:SS.mmm` one. Sections appear only when there is something real to put in them.

`summary` opens with a **task type** derived from what the references are actually used
for — `keyframe completion`, `reference generation`, `audio reuse`, `audio reference`,
joined with ` + `. A reference video that only supplies camera movement counts as
`reference generation`, never `video editing`; the guide is explicit that "the mere
presence of video or audio does not automatically create a corresponding task type". The
gear menu's **Task Type** field overrides it, which is how you reach `video editing` and
`video continuation` — neither of which this node has a path to produce on its own.

The two sound sections and `summary` have their own boxes under the Global Prompt. What you type there
goes straight into `overall_soundscape` and `non_diegetic_music`. Leave them empty and the
sections are omitted entirely — an empty heading is worse than none.

`Audio:` / `Sound:` / `SFX:` and `Music:` / `Score:` lines written in the prompt text are
still lifted into the same two sections, so older workflows and the Enhance node keep
working. A filled box wins over a lifted line.

**`<Subject N>` vs `<Picture N>`** is worth knowing: the guide reserves `<Subject N>` for
reusable content — a person, a place, a style — and `<Picture N>` for concrete frame
anchors. ComfyUI's tokenizer only ever labels images `<Picture i>`, so
`subject_definitions` binds the two. That is what lets a subject keep one name across
every cut. `@ref1` therefore expands to `<Subject 1>` here.

Note what that means for a subject slot's image: it is passed to the model as
`<Picture 1>`, but it gets **no** `<Picture 1>` declaration of its own. The guide is
explicit — an image used only to define something is cited inside its `<Subject N>` line
instead. Only real frame and storyboard anchors are declared as pictures.

**ComfyUI** switches to `[0s-1.5s] …`, the notation the ComfyUI H3 templates use. Same
timeline, same references, only the wording changes — so it is a fair A/B.

### Why a storyboard and not a per-segment mask

If you know LTX Director: its Prompt Relay builds a cross-attention mask so each segment
gets its own prompt. That cannot port. H3's DiT runs full self-attention over one packed
`[text | cond | audio | video]` sequence with `mask=None` hardcoded, so a relay mask would
have to span the entire sequence — several GB per attention call at 1344×768. The
storyboard is not a workaround: H3's Qwen3-VL encoder was trained on exactly this notation.

## Live preview while sampling

ComfyUI ships `latent_rgb_factors` for H3, so previews work — but `Latent2RGBPreviewer`
renders `x0[0, :, 0]`, the **first latent frame only**. You watch a still image while a
five-second shot is being sampled. KJNodes' Preview Override does the good version of
this, but its video paths are gated on LTX checks and nothing there unpacks H3's packed AV
latent, so on MiniMax it falls through to the same single frame.

**MiniMax H3 Preview Override** goes between the Director's `model` output and the sampler
and renders the whole shot as it denoises.

<img src="docs/images/preview-override-node.png" alt="The Preview Override node" width="360">


| Widget | What it does |
|---|---|
| `decode` | `latent2rgb (fast)` — one matmul, ~10 ms, rough colours. `vae (quality)` — the real decoder, true colours, real cost. |
| `preview_target` | `node` shows it on this node — always available. `sampler (VHS)` puts it in the sampler's usual preview slot and needs [VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) installed; `both` does both. |
| `preview_frames` | Cap on **latent** frames used, thinned evenly across the shot, so it shortens nothing. The main cost knob. |
| `preview_fps` | The shot's frame rate. A FLOAT, so the Director's `fps` output wires straight in. |
| `max_resolution` | Long edge of the preview image, as a **target** — latent2rgb frames arrive at latent size (a 1344×768 shot is an 84×48 grid), so this upscales them smoothly. |
| `webp_quality` | Quality of the animation sent to the browser. |
| `every_n_steps` | Never preview more often than every N sampler steps. |
| `max_preview_overhead` | Share of render time previews may use, in percent (default 25). After a preview costing C seconds the next waits `C·(100/P − 1)` s. 0 disables. |
| `suppress_default_preview` | Hides ComfyUI's built-in single-frame preview. |

The time in the status line (`render 9.9s`) is **server-side**: how long ComfyUI took to
decode, scale and encode that preview. Not browser time, not the sampler. With
`latent2rgb` it is tens of milliseconds; with `vae (quality)` at 1344×768 it can be 20–25 s,
because the real decoder expands 37 latent frames into 124 output frames through a 5 GB
VAE. Capping the frame *rate* would not help — rate only sets playback speed. The cost
knobs are `preview_frames` (try 4–8 for VAE), `max_resolution` and `every_n_steps`.

`vae (quality)` is the answer to "is there a small preview VAE, like LTX 2.3?" — there is
not. MiniMax has not released a TAESD-style decoder (`latent_format.taesd_decoder_name` is
`None`), so the choice is the cheap RGB approximation or the real video VAE.

<details>
<summary>Two non-obvious details, both of which produced real bugs here</summary>

**The latent is not the video.** `CFGGuider.sample` packs video and audio into one flat
tensor and only *then* wraps the callback with the nested view — and that wrapper sits
behind any `OUTER_SAMPLE` wrapper. What reaches a preview is the flat pack, which has to
be unpacked with core's `unpack_latents` first.

**Latent frames are not output frames.** H3 compresses time ~3.35× (17k+5 output frames
become 5k+2 latent frames), so a 124-frame shot is 37 latent frames. Playing those at 24
fps runs the preview three times too fast. The playback rate is derived from the *output*
duration — `shown_frames × fps ÷ output_frames` — so the preview lasts exactly as long as
the finished shot, thinning included.

</details>

## Writing the prompt for you

**MiniMax H3 Enhance Prompt** hands your reference images and a one-line idea to a local
vision model and gets back prompt text shaped for H3. The same images come out of its
`ref_images` output, so what the model described is exactly what H3 conditions on.

<img src="docs/images/enhance-prompt-node.png" alt="The Enhance Prompt node" width="380">

Ready-made graph: `example_workflows/MiniMax H3 Director + Enhance Prompt.json`.

```
LoadImage ─→ image0 ┐
LoadImage ─→ image1 ├→ Enhance Prompt ─┬→ prompt           → Director.global_prompt
                    ┘                  ├→ ref_images       → Director.ref_images
                                       └→ duration_seconds → Director.duration
```

Sockets grow as you connect, up to nine, and close the gap again when you disconnect.

| Widget | What it does |
|---|---|
| `idea` | What you want, in plain words. |
| `preset` | `global` writes scene, style, subjects and lighting and leaves the shots to your timeline. `storyboard` writes the whole shot sequence with timestamps — only for timelines whose segments carry no prompt text, or the two shot numberings collide. |
| `system_prompt` | Overrides the built-in instructions, which follow MiniMax's own prompt-writing guide. |
| `provider` / `base_url` / `model` | Ollama, LM Studio, or any OpenAI-compatible endpoint. `http://` is added if you leave it off; host and port only, no path. |
| `seed` | ComfyUI caches node outputs, so an unchanged input never re-asks the model. Change this to force a fresh answer. |
| `max_words` | Caps the description. MiniMax's guide puts it at 350–500 words. |
| `unload_after` | Frees the vision model's VRAM when done. Leave it on unless you are iterating. |
| `on_error` | `passthrough` hands your raw text on and warns, so a stopped Ollama does not kill a render. |

**It has to be a vision model.** A text-only model ignores your images without saying so.
`qwen2.5vl:7b` is a reasonable Ollama default; anything larger writes noticeably better
prompts. Expect 15–45 s per run, during which the queue is blocked.

**What it deliberately does not write:** section labels, `<Picture N>` numbering, or shot
markers in `global` mode. The Director compiles the structured MiniMax prompt and assigns
the reference numbers — a second set from the model would nest structure inside structure
and collide with the Director's own ordinals. The instructions forbid it and the output is
filtered anyway, because small models do not reliably obey.

**If the VLM and H3 share a GPU**, the vision model is evicted after each run
(`unload_after`). Ollama has no per-request device selection, so to put it on a different
card you set `CUDA_VISIBLE_DEVICES` on the Ollama *service*, not here.

## Retake Mode

Load a base video, turn on **Retake Mode** in the toolbar, mark a range: the Director
regenerates only that range, anchored on the base video's own frames either side of it.
The frame before the range becomes `first_frame`, the frame after becomes `last_frame` —
exactly what H3's first/last anchors are for, so the new material meets the old on both cuts.

Wire the Director's `retake_info` output into **MiniMax H3 Retake Stitch** together with
the decoded images (and audio) to get the full video back: base head + retake + base tail,
video and audio, resampled to 24 fps. `keep_base_audio` keeps the original soundtrack
across the whole thing instead of the generated one.

## Longer than 15 seconds

Not solved yet. There was a **Director Chain** node that rendered a long timeline as a
chain of anchored windows, and its sampling worked — but there was no usable way to hand
it a timeline, so it has been withdrawn rather than shipped as a feature nobody can
operate. The code stays in the repository; the reasoning is written down at the top of
`minimax_chain.py`.

Until it returns, H3's trained range is the limit: 4-15 s per render.

## Troubleshooting

**The nodes do not appear after installing.**
Restart ComfyUI fully and hard-reload the browser (Ctrl+F5). If they still do not appear,
look at the ComfyUI console during startup — an import error is printed there. Check your
ComfyUI version is ≥ 0.30.0.

**The node loads but the timeline is blank / looks like a plain widget list.**
Stale frontend cache. Ctrl+F5. In a private window it will look correct if that is the cause.

**`ERROR: clip input is invalid` / garbage output.**
`CLIPLoader` type must be set to **minimax**, not `stable_diffusion` or anything else.

**`vae.decode()` fails, or the video is noise but the audio is fine.**
The joint latent must go to `VAEDecode` with the **video** VAE and `VAEDecodeAudio` with
the **audio** VAE. Swapping the two VAEs is the usual cause.

**The finished video is a flat, featureless grey, but the audio is fine and the live
preview looked right.**
The latent is good and the video VAE is producing NaN. Flat grey — not noise, not black,
every pixel the same value — is what NaN looks like after clamping. `latent2rgb` previews
keep working because they never touch the VAE.

Confirm it in a minute instead of a full render: set the Preview Override's `decode` to
`vae (quality)`. That runs the same video VAE, so if the preview goes grey too, the VAE
is where it breaks.

The thing to try is precision. `minimax_h3_video_vae_fp16` runs in fp16, and ComfyUI's own
help text for `--fp16-vae` says it "might cause black images". Start ComfyUI with
**`--fp32-vae`**. fp32 is the *only* alternative here: ComfyUI declares this VAE's working
dtypes as `[float16, float32]`, so `--bf16-vae` silently gets you one of those two. The
decoder grows from ~4.9 GB to ~10 GB, which on a 16 GB card means partial offload and a
slower decode; `--cpu-vae` is the slow-but-certain fallback.

Reported once so far, on ROCm/Windows, where fp16 convolution kernels take different code
paths than on CUDA. Not reproduced on CUDA, and the fp32 remedy is not yet confirmed by
the reporter — if you hit this, please say whether it helped.

**Out of memory.**
Lower the resolution first (768 short edge is native, but 480 works), then `length`. With
`vae (quality)` previews, lower `preview_frames` to 4 — a VAE preview allocates as much as
a real decode. Consider the `_pruned_fp8_scaled` checkpoints if you are on `_bf16`.

**"neither model input is connected".**
The Director has two model inputs on purpose: `model (t2v/i2v)` for `fl2va` and
`model (ref2v)` for `ref2va`. Connect at least the one your toolbar switch selects.

**Images in the middle of the timeline seem ignored (Refs OFF).**
They are — H3 anchors first and last frame only. Switch to **Refs ON** and they become
`<Picture i>` references instead, or move them to the window edges.

**The generated clip is longer than I asked for.**
Length snaps up to the 17k+5 grid: 5, 22, 39, 56, 73, 90, 107, 124 … frames. 5 s → 124
frames → 5.17 s. This is the model's grid, not a bug.

## Reporting a bug

Open an [issue](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director/issues). The three
things that make a report fixable:

1. the **full traceback** from the ComfyUI console (not just the last line),
2. the **workflow JSON** (Workflow → Export), and
3. which **model files** you loaded.

The issue form asks for exactly these. For anything about dragging, resizing or the
preview window, add the **browser** console (F12 → Console) too.

## Contributing

Pull requests are welcome, and so are reports from hardware this has never run on — every
line of it was verified on a single NVIDIA card, so ROCm and Apple silicon are unknown
territory. [CONTRIBUTING.md](CONTRIBUTING.md) has the layout, the three checks to run
before submitting, and the handful of rules that exist because breaking them caused a real
bug.

## Credits

The timeline editor is **[LTX Director](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI)
by [WhatDreamsCost](https://github.com/WhatDreamsCost)** — the editing model, the track
layout, the interaction design and the bulk of the frontend code are theirs. The CS fork
that this one branched from is by **[CGlide](https://github.com/CGlide)**.

This project is that editor with a MiniMax H3 backend: new conditioning, storyboard prompt
compilation, packed AV latents, preview and Retake — by
[seesee75](https://github.com/seesee75-commits).

MiniMax H3 by [MiniMax](https://huggingface.co/MiniMaxAI), ComfyUI packaging by
[Comfy-Org](https://huggingface.co/Comfy-Org).

## License

**GPL-3.0**, inherited from LTX Director — see [LICENSE](LICENSE). If you fork this,
your fork is GPL-3.0 too, and it must stay open.
