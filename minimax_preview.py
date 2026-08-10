"""Live sampling preview for MiniMax H3.

Why this exists
---------------
ComfyUI core does ship `latent_rgb_factors` for MiniMaxH3Video, so previews are not
missing — but `Latent2RGBPreviewer` renders `x0[0, :, 0]`, i.e. the first latent frame
only. You watch a single still while a five-second shot is being sampled.

KJNodes' Preview Override does the good version of this (in-node preview, optional full
VAE decode), but its video paths are gated on `_is_ltx_latent_format` /
`_is_ltx2_diffusion_model`, and nothing there unpacks H3's packed AV latent — so on
MiniMax it falls through to the same single frame. This node is the H3 equivalent.

The one non-obvious detail
--------------------------
`CFGGuider.sample` packs the video and audio streams into ONE flat tensor and only then
wraps the callback with the nested view. That wrapper sits *behind* an OUTER_SAMPLE
wrapper in the call chain, so what reaches this callback is the flat pack, not the
NestedTensor — `_video_stream` unpacks it with core's own `unpack_latents`.

The audio half — a second node
------------------------------
H3 generates stereo sound in the same forward pass, and every preview in the ecosystem
throws it away: core's `prepare_callback` keeps `x0.tensors[0]` and nothing else, and
`BinaryEventTypes` has no audio member for it to travel on even if it did. So
`MiniMaxH3AudioPreview` decodes the other stream itself and sends it on its own event.

It is deliberately its own node rather than four more widgets on this one: whatever you
watch the frames with — this node, KJNodes' Preview Override with `taeh3` — the sound
attaches next to it instead of dragging a second video preview along. `_audio_stream` is
`_video_stream`'s twin, taking the last packed stream instead of the first, and there is no
tiny decoder for audio the way `taeh3` is one for video — nor does there need to be: a
three-second window costs ~110 MB and a fraction of a second through the real audio VAE.
"""

import base64
import io as _io
import logging
import math
import struct
import time
import wave
from urllib.parse import quote

import torch
import torch.nn.functional as F
from aiohttp import web
from PIL import Image

import comfy.model_management
import comfy.patcher_extension
import comfy.utils
import latent_preview
import server
from comfy_api.latest import io
from protocol import BinaryEventTypes

try:
    # Handles `latent.is_nested -> unbind()[-1]`, picks the VAE's output sample rate and
    # builds the AUDIO dict. Reusing it keeps the [B,2,L]/movedim shape dance out of here.
    from comfy_extras.nodes_audio import vae_decode_audio
except ImportError:                                   # pre-helper ComfyUI
    vae_decode_audio = None

log = logging.getLogger(__name__)

EVENT = "minimax_h3_preview"
AUDIO_EVENT = "minimax_h3_audio_preview"
DECODE_FAST = "latent2rgb (fast)"
DECODE_VAE = "vae (quality)"
PLAYBACK_TRUE = "true speed"
PLAYBACK_SOURCE = "source fps"
MODEL_FPS = 24.0            # H3's native output rate
AUDIO_LATENT_FPS = 40.0     # core: audio_t = round(duration * 40)
AUDIO_CHANNELS = 32         # the audio stream's latent channel count, per MiniMaxH3AV
ENVELOPE_BUCKETS = 192      # waveform strip resolution; a few hundred bytes of JSON
TARGET_NODE = "node"
TARGET_SAMPLER = "sampler (VHS)"
TARGET_BOTH = "both"


def _video_stream(x0, latent_shapes=None):
    """Pull the [B, C, T, h, w] video latent out of whatever the sampler handed us."""
    if x0 is None:
        return None
    if getattr(x0, "is_nested", False):
        return x0.tensors[0]
    if x0.ndim == 5:
        return x0
    if latent_shapes and len(latent_shapes) > 1:
        return comfy.utils.unpack_latents(x0, list(latent_shapes))[0]
    return None


def _audio_stream(x0, latent_shapes=None):
    """Pull the [B, 32, 2, T] audio latent out of whatever the sampler handed us.

    The mirror of `_video_stream`: video is the first packed stream and 5D, audio is the
    last and 4D. The channel/stereo check is what keeps a plain 4D image latent from being
    mistaken for sound on some other model.
    """
    if x0 is None:
        return None
    if getattr(x0, "is_nested", False):
        # Core reaches into these two different ways — `.tensors[0]` in prepare_callback,
        # `.unbind()[-1]` in vae_decode_audio. Accept either rather than bet on one.
        streams = getattr(x0, "tensors", None)
        if streams is None:
            streams = x0.unbind()
        return streams[-1]
    if x0.ndim == 4 and x0.shape[1] == AUDIO_CHANNELS and x0.shape[2] == 2:
        return x0
    if latent_shapes and len(latent_shapes) > 1:
        return comfy.utils.unpack_latents(x0, list(latent_shapes))[1]
    return None


def audio_scale_of(guider, streams=0):
    """The factor the sampler carries the audio stream at, so a decode can undo it.

    H3 denoises audio and video on different flow shifts, and ComfyUI reconciles that by
    carrying the audio latent scaled onto the video schedule — `ModelSamplingAV.audio_scale`
    is `shift / audio_shift`, so 12/3 = 4 with the Director's defaults. In sampler space the
    audio target is that many times the latent the VAE expects, and core's own decode path
    divides it back out (`MiniMaxH3._scale_audio_slice`).

    Skipping it hands BigVGAN a latent several times too large, which comes back as loud
    noise at every step of the schedule, while the video stream — carried at 1.0 — decodes
    perfectly and so points at nothing.

    `MiniMaxH3.audio_scale()` is the authority, but it answers 1.0 whenever the model's
    `latent_shapes` is unset, which is not something a preview can rely on mid-sampling. When
    the pack demonstrably has two streams and the accessor still says 1.0, ask
    `model_sampling` directly rather than silently decoding at the wrong scale.
    """
    model = getattr(getattr(guider, "model_patcher", None), "model", None)
    if model is None:
        return 1.0

    scale = None
    try:
        scale = float(model.audio_scale())
    except Exception:
        pass
    if (scale is None or scale == 1.0) and streams > 1:
        try:
            scale = float(model.model_sampling.audio_scale)
        except Exception:
            pass
    return scale if scale and scale > 0.0 else 1.0


def audio_latent_window(latent, window_seconds):
    """The first `window_seconds` of a [B, 32, 2, T] audio latent. 0 keeps the whole clip.

    Head, not tail: the preview animation loops from the start of the shot, so the opening
    seconds are the ones that line up with the frames you are watching.
    """
    if window_seconds <= 0:
        return latent
    frames = max(1, int(round(window_seconds * AUDIO_LATENT_FPS)))
    if frames >= latent.shape[-1]:
        return latent
    return latent[..., :frames].contiguous()


def _decode_audio(audio_vae, latent, window_seconds, audio_scale=1.0):
    """[B, 32, 2, T] in sampler space -> ({"waveform": [B, 2, L], "sample_rate": int})."""
    latent = audio_latent_window(latent, window_seconds).to(torch.float32)
    if audio_scale != 1.0:
        latent = latent / audio_scale
    if vae_decode_audio is not None:
        return vae_decode_audio(audio_vae, {"samples": latent})
    waveform = audio_vae.decode(latent)                # [B, L, 2] — decode moves channels last
    if waveform.ndim == 3:
        waveform = waveform.movedim(-1, 1)
    return {"waveform": waveform.to(torch.float32).cpu(),
            "sample_rate": int(getattr(audio_vae, "audio_sample_rate", 32000))}


def _stereo(waveform):
    """[B, C, L] or [C, L] -> a single [2, L] float32 CPU clip. Level untouched."""
    wav = waveform[0] if waveform.ndim == 3 else waveform
    wav = wav.to(torch.float32).cpu()
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    return wav[:2].contiguous()


def _normalise(wav, peak):
    """Scale a clip into [-1, 1] rather than clipping it.

    A mid-sampling x0 is not a finished latent, and a vocoder handed an off-distribution one
    answers with something far outside [-1, 1]. Clipping that produces a saturated square
    wave: audible as noise, and a solid rectangle in the envelope — which hides the one
    number that would have explained it. Scaling keeps the shape, and the logged peak says
    how far out the decode was.
    """
    if peak <= 1.0:
        return wav
    return wav / peak


def _envelope(wav, buckets=ENVELOPE_BUCKETS):
    """Per-bucket peak of |sample|, one row per channel, for the waveform strip.

    Worth its few hundred bytes on its own: browsers refuse to play sound until the page
    has been interacted with, and a strip you can always see is the difference between
    "the audio preview is broken" and "the audio preview is muted".
    """
    n = int(wav.shape[-1])
    if n == 0:
        return []
    step = max(1, math.ceil(n / max(1, buckets)))
    padded = F.pad(wav.abs(), (0, step * math.ceil(n / step) - n))
    peaks = padded.reshape(wav.shape[0], -1, step).amax(dim=-1).clamp(0, 1)
    return [[round(float(v), 3) for v in row] for row in peaks]


def _encode_wav(wav, sample_rate):
    """[2, L] -> 16-bit PCM WAV bytes, through the standard library.

    Compressing this through `av` would save ~95% of it, but the MP3 that produced — even
    following ComfyUI's own SaveAudioMP3 recipe — came out as a file the browser refused,
    with nothing in the Python log to show for it. Any codec added here needs its output
    verified, not assumed. WAV's bytes can be checked offline, and are.
    """
    pcm = (wav.movedim(0, -1) * 32767.0).round().clamp(-32768, 32767).to(torch.int16)
    buf = _io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(int(wav.shape[0]))
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.contiguous().numpy().tobytes())
    return buf.getvalue()


# node_id -> (mime, bytes). One live clip per node: each update replaces the last, so this
# holds a few hundred KB per audio preview node in the graph and nothing after the tab
# closes the workflow.
_AUDIO_CLIPS = {}
AUDIO_ROUTE = "/minimax_h3/audio_preview"


def _register_audio_route():
    """Serve the clip over HTTP rather than embedding it in the page.

    Both ways of putting audio *in* the document were refused by the browser for bytes that
    are a valid WAV — a data: URI as "media resource not suitable", a blob: URL as "failed to
    open channel". That pattern is a CSP or an extension restricting media schemes, not a
    codec problem, and no amount of re-encoding fixes it. An ordinary same-origin GET with a
    Content-Type is what core's PreviewAudio relies on; nothing about it is unusual enough to
    be blocked, and it keeps half a megabyte of base64 off the websocket as well.
    """
    try:
        routes = server.PromptServer.instance.routes
    except Exception as e:
        log.warning("[MiniMaxDirector] could not register the audio preview route (%r) — "
                    "the audio preview will have nothing to fetch.", e)
        return

    @routes.get(AUDIO_ROUTE)
    async def _audio_preview(request):
        clip = _AUDIO_CLIPS.get(request.query.get("node_id", ""))
        if clip is None:
            return web.Response(status=404, text="no audio preview clip for this node")
        mime, data = clip
        # no-store because the URL is per-update anyway; range requests are unnecessary for
        # a few hundred KB the element can buffer in one go.
        return web.Response(body=data, content_type=mime,
                            headers={"Cache-Control": "no-store"})


_register_audio_route()


def _pick_frames(video, max_frames):
    """Evenly thin [B, C, T, h, w] down to at most max_frames along T."""
    t = video.shape[2]
    if max_frames <= 0 or t <= max_frames:
        return video
    idx = torch.linspace(0, t - 1, max_frames).round().long().unique()
    return video[:, :, idx]


def pixel_frames_from_latent_t(latent_t):
    """How many output frames one H3 video latent covers.

    The latent is compressed ~3.35x in time (core's `video_latent_t`: 17k+5 pixel frames
    become 5k+2 latent frames), so a preview that plays latent frames at the video's fps
    runs more than three times too fast. Inverting that mapping is what keeps the preview
    honest about the shot's real speed.
    """
    if latent_t <= 2:
        return 5
    k, remainder = divmod(int(latent_t) - 2, 5)
    if remainder == 0:
        return 17 * k + 5
    return max(1, int(round(latent_t * 17.0 / 5.0)))   # off-grid latent: approximate


class _RGBFactors:
    def __init__(self, latent_format):
        factors = getattr(latent_format, "latent_rgb_factors", None)
        if factors is None:
            raise ValueError("latent format has no latent_rgb_factors")
        # stored as an F.linear weight: [out=3, in=C] -> channel count is shape[1]
        self.w = torch.tensor(factors, device="cpu").transpose(0, 1)
        bias = getattr(latent_format, "latent_rgb_factors_bias", None)
        self.b = torch.tensor(bias, device="cpu") if bias is not None else None

    def __call__(self, video):
        """[B, C, T, h, w] -> [N, h, w, 3] in 0..1"""
        chans = self.w.shape[1]
        moved = video.movedim(2, 1)                       # [B, T, C, h, w]
        # flatten batch-major; take the shape AFTER the movedim, not the caller's
        x = moved.reshape((-1,) + tuple(moved.shape[-3:]))[:, :chans].to(torch.float32)
        w = self.w.to(dtype=x.dtype, device=x.device)
        b = self.b.to(dtype=x.dtype, device=x.device) if self.b is not None else None
        return ((F.linear(x.movedim(1, -1), w, bias=b) + 1.0) / 2.0).clamp(0, 1)


def _vae_decode(vae, video):
    """Full-quality decode of [B, C, T, h, w] -> [N, h, w, 3] in 0..1."""
    images = vae.decode(video)
    if images.ndim == 5:                       # [B, T, h, w, 3]
        images = images.reshape(-1, *images.shape[-3:])
    return images.clamp(0, 1).to(torch.float32).cpu()


def _to_pil(images, max_res):
    """Render frames at `max_res` on the long edge — a target, not just a ceiling.

    latent2rgb frames arrive at latent resolution (a 1344x768 shot is an 84x48 grid), so
    without an upscale the preview is a postage stamp; with a nearest-neighbour upscale it
    is a mosaic. Smooth interpolation reads as "approximate", which is what it is — switch
    decode to 'vae (quality)' for real detail.
    """
    out = []
    for frame in images:
        arr = (frame * 255.0).to(torch.uint8).cpu().numpy()
        img = Image.fromarray(arr)
        if max_res > 0:
            longest = max(img.width, img.height)
            if longest != max_res and longest > 0:
                scale = max_res / float(longest)
                size = (max(1, int(round(img.width * scale))),
                        max(1, int(round(img.height * scale))))
                img = img.resize(size, Image.LANCZOS if scale < 1.0 else Image.BICUBIC)
        out.append(img)
    return out


def _encode_animated_webp(frames, fps, quality):
    if not frames:
        return None
    buf = _io.BytesIO()
    try:
        frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:],
                       duration=max(1, int(round(1000 / max(1, fps)))), loop=0,
                       quality=quality, method=0)
    except Exception as e:
        log.warning("[MiniMaxDirector] animated WebP encode failed: %s", e)
        return None
    return base64.b64encode(buf.getvalue()).decode("ascii")


def throttle_gap(cost_seconds, max_overhead_percent):
    """How long to wait after a preview that took `cost_seconds`.

    A full VAE decode of a 1344x768 shot can cost tens of seconds — once per step that is
    minutes of pure overhead. Rather than guess a step interval, hold previews to a share
    of wall-clock: to spend at most P percent of the time previewing, a render costing C
    must be followed by C*(100/P - 1) seconds of actual sampling.
    """
    if max_overhead_percent <= 0 or cost_seconds <= 0:
        return 0.0
    return cost_seconds * (100.0 / float(max_overhead_percent) - 1.0)


class _VHSStreamer:
    """Streams individual frames to VideoHelperSuite's animated latent-preview player."""

    def __init__(self, rate):
        self.rate = max(1, int(rate))
        self.first = True
        self.last_time = 0.0
        self.cursor = 0

    def send(self, pil_frames, rate=None):
        srv = server.PromptServer.instance
        total = len(pil_frames)
        if total == 0:
            return 0
        if rate and self.first:
            # locked in with the handshake — the player is told the rate exactly once
            self.rate = max(1, int(round(rate)))
        now = time.time()
        count = int((now - self.last_time) * self.rate)
        self.last_time += count / self.rate
        if count > total:
            count = total
        elif count <= 0:
            return 0
        if self.first:
            self.first = False
            srv.send_sync("VHS_latentpreview",
                          {"length": total, "rate": self.rate, "id": srv.last_node_id})
            self.last_time = now + 1.0 / self.rate

        order = [(self.cursor + i) % total for i in range(count)]
        node_id = (srv.last_node_id or "").encode("ascii")
        for i in order:
            message = _io.BytesIO()
            message.write((1).to_bytes(length=4, byteorder="big") * 2)
            message.write(i.to_bytes(length=4, byteorder="big"))
            message.write(struct.pack("16p", node_id))
            pil_frames[i].save(message, format="JPEG", quality=95)
            srv.send_sync(BinaryEventTypes.PREVIEW_IMAGE, message.getvalue(), srv.client_id)
        self.cursor = (self.cursor + count) % total
        return count


class _OuterSampleWrapper:
    def __init__(self, node_id, decode_mode, vae, max_resolution, preview_frames,
                 preview_fps, webp_quality, every_n_steps, suppress_default, target,
                 max_overhead=25, playback=None):
        self.node_id = node_id
        self.decode_mode = decode_mode
        self.vae = vae
        self.max_resolution = int(max_resolution)
        self.preview_frames = int(preview_frames)
        self.preview_fps = float(preview_fps)
        self.webp_quality = int(webp_quality)
        self.every_n_steps = max(1, int(every_n_steps))
        self.suppress_default = bool(suppress_default)
        self.target = target
        self.max_overhead = max(0, min(100, int(max_overhead)))
        self.playback = playback or PLAYBACK_TRUE

    def _rate_for(self, shown, pixel_frames):
        """Frames per second to play `shown` images at.

        Two honest answers, and the node used to pick one for you:

        `true speed` spreads the images across the shot's real duration, so the preview
        lasts as long as the finished clip. With latent2rgb that caps out at
        preview_fps / 3.35 — one image per latent frame, and H3 compresses time by that
        much — which looks like a setting being ignored if nobody says so.

        `source fps` plays them at preview_fps flat. That is what ComfyUI's own preview and
        the other packs do, and it is why they show a round 24: the motion reads at normal
        speed but the clip is over in a third of the time. Useful for judging movement,
        misleading about timing.
        """
        if self.playback == PLAYBACK_SOURCE:
            return max(0.1, self.preview_fps)
        return max(0.1, self.preview_fps * shown / max(1, pixel_frames))

    def _send_to_node(self, b64, n_frames, step, total_steps, ms, rate):
        server.PromptServer.instance.send_sync(EVENT, {
            "node_id": self.node_id, "webp": b64, "frames": n_frames,
            "fps": round(float(rate), 2), "source_fps": round(float(self.preview_fps), 2),
            "step": step, "total_steps": total_steps, "ms": ms, "mode": self.decode_mode,
            "playback": self.playback,
        })

    def __call__(self, executor, noise, latent_image, sampler, sigmas, denoise_mask,
                 callback, disable_pbar, seed, **kwargs):
        guider = executor.class_obj
        latent_shapes = kwargs.get("latent_shapes")
        latent_format = guider.model_patcher.model.latent_format

        to_rgb = None
        if self.decode_mode == DECODE_FAST or self.vae is None:
            try:
                to_rgb = _RGBFactors(latent_format)
            except Exception as e:
                log.warning("[MiniMaxDirector] preview unavailable: %s", e)

        vhs = _VHSStreamer(self.preview_fps) if self.target in (TARGET_SAMPLER, TARGET_BOTH) else None
        to_node = self.target in (TARGET_NODE, TARGET_BOTH)

        # Core's previewer is built before we are reached, so suppression has to happen on
        # the class it goes through. Restored in the finally below, always.
        original_decode = latent_preview.LatentPreviewer.decode_latent_to_preview_image
        if self.suppress_default:
            latent_preview.LatentPreviewer.decode_latent_to_preview_image = \
                lambda self_, preview_format, x0: None

        original_cb = callback
        state = {"warned": False, "sent": 0, "cost": 0.0, "finished": 0.0, "anim": 0.0,
                 "throttle_logged": False, "cap_logged": False}
        log.info("[MiniMaxDirector] preview: %s, target=%s, <=%d frames @%d fps, max %dpx.",
                 self.decode_mode, self.target, self.preview_frames, self.preview_fps,
                 self.max_resolution)

        def _should_skip(now):
            gap = throttle_gap(state["cost"], self.max_overhead)
            if to_node:
                # Replacing the <img> restarts the animation from frame one. Send a new one
                # every step and a five-second loop never gets past its first second — it
                # reads as a stuck, crawling preview. Let each animation play through.
                gap = max(gap, state["anim"])
            return gap > 0 and (now - state["finished"]) < gap

        def combined(step, x0, x, total_steps):
            if (to_rgb is not None or self.vae is not None) and x0 is not None \
                    and step % self.every_n_steps == 0 and not _should_skip(time.time()):
                t0 = time.time()
                try:
                    video = _video_stream(x0, latent_shapes)
                    if video is not None and video.ndim == 5:
                        pixel_frames = pixel_frames_from_latent_t(int(video.shape[2]))
                        video = _pick_frames(video, self.preview_frames)
                        if self.decode_mode == DECODE_VAE and self.vae is not None:
                            images = _vae_decode(self.vae, video)
                        else:
                            images = to_rgb(video)
                        frames = _to_pil(images, self.max_resolution)
                        # The shot lasts pixel_frames / fps seconds. Spread however many
                        # frames we ended up with across exactly that long. Counting them
                        # after the decode matters: latent2rgb yields one image per latent
                        # frame, the VAE expands each latent frame into ~3.35 of them.
                        rate = self._rate_for(len(frames), pixel_frames)
                        if not state["cap_logged"] and self.playback == PLAYBACK_TRUE \
                                and rate < self.preview_fps - 0.05:
                            state["cap_logged"] = True
                            log.info("[MiniMaxDirector] preview plays at %.1f fps, not %.0f: "
                                     "%d frame(s) spread over the shot's %.2fs so it lasts as "
                                     "long as the finished clip. %s Switch playback to '%s' to "
                                     "play them at %.0f fps instead — the motion reads normally, "
                                     "the clip ends early.",
                                     rate, self.preview_fps, len(frames),
                                     pixel_frames / MODEL_FPS,
                                     "latent2rgb has one image per latent frame, so it cannot "
                                     "exceed %.1f fps here." % (self.preview_fps * 5.0 / 17.0)
                                     if self.decode_mode != DECODE_VAE else
                                     "Raising preview_frames raises it.",
                                     PLAYBACK_SOURCE, self.preview_fps)
                        if to_node:
                            b64 = _encode_animated_webp(frames, rate, self.webp_quality)
                            if b64:
                                self._send_to_node(b64, len(frames), step + 1, total_steps,
                                                   int((time.time() - t0) * 1000), rate)
                        if vhs is not None:
                            vhs.send(frames, rate)
                        state["sent"] += len(frames)
                        state["cost"] = time.time() - t0
                        state["finished"] = time.time()
                        state["anim"] = len(frames) / max(0.1, rate) if to_node else 0.0
                        if self.max_overhead > 0 and state["cost"] > 1.0 \
                                and not state["throttle_logged"]:
                            state["throttle_logged"] = True
                            log.info("[MiniMaxDirector] a preview costs %.1fs; holding it to "
                                     "%d%% of the render, so previews will be spaced ~%.0fs "
                                     "apart. Lower preview_frames or max_resolution for more "
                                     "of them.", state["cost"], self.max_overhead,
                                     state["cost"] * (100.0 / self.max_overhead - 1.0))
                except Exception as e:
                    # never take the generation down over a preview
                    if not state["warned"]:
                        state["warned"] = True
                        log.warning("[MiniMaxDirector] preview failed, continuing without "
                                    "it: %r", e, exc_info=True)
            if original_cb is not None:
                original_cb(step, x0, x, total_steps)

        try:
            out = executor(noise, latent_image, sampler, sigmas, denoise_mask, combined,
                           disable_pbar, seed, **kwargs)
        finally:
            latent_preview.LatentPreviewer.decode_latent_to_preview_image = original_decode
        log.info("[MiniMaxDirector] preview rendered %d frames.", state["sent"])
        return out


class _AudioOuterSampleWrapper:
    """Decodes the audio stream on the way through sampling and streams it to its node.

    Its own wrapper under its own key, so it chains with whatever draws the frames — this
    pack's Preview Override or KJNodes' with `taeh3` — instead of replacing it.
    """

    def __init__(self, node_id, audio_vae, window_seconds, start_at_percent,
                 every_n_steps, max_overhead):
        self.node_id = node_id
        self.audio_vae = audio_vae
        self.window_seconds = max(0.0, float(window_seconds))
        self.start_at_percent = max(0, min(100, int(start_at_percent)))
        self.every_n_steps = max(1, int(every_n_steps))
        self.max_overhead = max(0, min(100, int(max_overhead)))

    def _due(self, step, total_steps, state):
        """Whether to spend a decode on this step.

        Early steps are not worth hearing — the audio stream is still mostly noise — and
        this decode is the one that can evict the model it is listening to, so it stays
        behind an explicit share of the schedule.
        """
        if state["off"] or step % self.every_n_steps:
            return False
        return (step + 1) >= total_steps * self.start_at_percent / 100.0

    def _skip(self, now, state):
        """Throttle: the overhead budget, but never shorter than the clip being played.

        Replacing the clip restarts it, so sending one every step means never hearing past
        its first moment — the same argument the video preview makes about its animation.
        """
        gap = max(throttle_gap(state["cost"], self.max_overhead), state["clip"])
        return gap > 0 and (now - state["finished"]) < gap

    def _payload(self, x0, latent_shapes, state, audio_scale=1.0):
        """Decode to a clip the route can serve + a peak envelope, or None. Never raises."""
        try:
            latent = _audio_stream(x0, latent_shapes)
            if latent is None or latent.ndim != 4:
                if not state["warned"]:
                    state["warned"] = True
                    log.info("[MiniMaxDirector] no audio stream in this latent — the audio "
                             "preview needs MiniMax H3's packed audio+video latent.")
                return None
            audio = _decode_audio(self.audio_vae, latent, self.window_seconds, audio_scale)
            wav = _stereo(audio["waveform"])
            peak = float(wav.abs().max()) if wav.numel() else 0.0
            wav = _normalise(wav, peak)
            sample_rate = int(audio.get("sample_rate") or 32000)
            data = _encode_wav(wav, sample_rate)
            seconds = wav.shape[-1] / float(max(1, sample_rate))
            # Park the bytes for the route to serve, and hand the node a fresh URL each time
            # so nothing can be answered from cache.
            _AUDIO_CLIPS[self.node_id] = ("audio/wav", data)
            state["seq"] += 1
            if not state["logged"]:
                state["logged"] = True
                # The peak is the number that tells you whether the decode landed: a real
                # waveform sits inside +/-1, and a latent decoded at the wrong scale does not.
                log.info("[MiniMaxDirector] audio preview: %.2fs of %d Hz stereo per update, "
                         "audio_scale %.3f, peak %.2f%s, %d KB served from %s.",
                         seconds, sample_rate, audio_scale, peak,
                         " (scaled down to fit)" if peak > 1.0 else "",
                         len(data) // 1024, AUDIO_ROUTE)
            return {"url": "%s?node_id=%s&seq=%d" % (AUDIO_ROUTE,
                                                     quote(str(self.node_id)), state["seq"]),
                    "audio_mime": "audio/wav", "kb": len(data) // 1024,
                    "seconds": round(seconds, 2), "envelope": _envelope(wav)}
        except comfy.model_management.OOM_EXCEPTION:
            state["off"] = True
            log.warning("[MiniMaxDirector] the audio preview ran out of memory — it is off "
                        "for the rest of this run and the render continues. Lower "
                        "window_seconds, or take the node out.")
        except Exception as e:
            state["off"] = True
            log.warning("[MiniMaxDirector] audio preview failed, the render continues "
                        "without it: %r", e, exc_info=True)
        return None

    def __call__(self, executor, noise, latent_image, sampler, sigmas, denoise_mask,
                 callback, disable_pbar, seed, **kwargs):
        latent_shapes = kwargs.get("latent_shapes")
        guider = executor.class_obj
        original_cb = callback
        state = {"off": False, "warned": False, "logged": False, "sent": 0, "seq": 0,
                 "cost": 0.0, "finished": 0.0, "clip": 0.0, "steps": 0, "due": 0,
                 "scale": None}
        log.info("[MiniMaxDirector] audio preview: %s from %d%% of the steps, <=%d%% "
                 "overhead.",
                 "the whole clip" if self.window_seconds <= 0
                 else "%.1fs" % self.window_seconds,
                 self.start_at_percent, self.max_overhead)

        def combined(step, x0, x, total_steps):
            state["steps"] += 1
            if x0 is not None and self._due(step, total_steps, state):
                state["due"] += 1
                if not self._skip(time.time(), state):
                    t0 = time.time()
                    if state["scale"] is None:
                        state["scale"] = audio_scale_of(
                            guider, len(latent_shapes) if latent_shapes else 0)
                    payload = self._payload(x0, latent_shapes, state, state["scale"])
                    if payload:
                        payload.update({"node_id": self.node_id, "step": step + 1,
                                        "total_steps": total_steps,
                                        "ms": int((time.time() - t0) * 1000)})
                        server.PromptServer.instance.send_sync(AUDIO_EVENT, payload)
                        state["sent"] += 1
                        state["clip"] = float(payload["seconds"])
                    state["cost"] = time.time() - t0
                    state["finished"] = time.time()
            if original_cb is not None:
                original_cb(step, x0, x, total_steps)

        out = executor(noise, latent_image, sampler, sigmas, denoise_mask, combined,
                       disable_pbar, seed, **kwargs)
        self._report(state, latent_shapes)
        return out

    def _report(self, state, latent_shapes):
        """Say what happened, and when nothing happened say which gate stopped it.

        A preview that silently shows nothing is indistinguishable from a preview that is
        broken, so every path out of here ends in a line you can act on.
        """
        if state["sent"]:
            log.info("[MiniMaxDirector] audio preview sent %d update(s).", state["sent"])
        elif state["steps"] == 0:
            log.warning("[MiniMaxDirector] audio preview: the sampler never called back, so "
                        "nothing was decoded. The node is in the graph, but is its 'model' "
                        "output the one actually wired into the sampler?")
        elif state["off"]:
            log.warning("[MiniMaxDirector] audio preview: nothing was sent — it switched "
                        "itself off after the failure logged above.")
        elif state["due"] == 0:
            log.warning("[MiniMaxDirector] audio preview: no step passed the gate over %d "
                        "sampled step(s), so nothing was sent. start_at_percent=%d%% waits "
                        "for step %d, and every_n_steps=%d has to land on it.",
                        state["steps"], self.start_at_percent,
                        int(state["steps"] * self.start_at_percent / 100.0) or 1,
                        self.every_n_steps)
        else:
            log.warning("[MiniMaxDirector] audio preview: %d step(s) were due but no audio "
                        "stream was found in the latent (latent_shapes %s), so nothing was "
                        "sent. This needs MiniMax H3's packed audio+video latent — the "
                        "Director's, or EmptyMiniMaxH3LatentAV — not a video-only one.",
                        state["due"], "present" if latent_shapes else "absent")


class MiniMaxH3AudioPreview(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AudioPreviewCS",
            display_name="MiniMax H3 Audio Preview",
            category="MiniMax H3",
            description=(
                "Hear the shot while it denoises. H3 generates stereo audio in the same "
                "forward pass as the picture, and ComfyUI's preview discards it: "
                "prepare_callback keeps x0.tensors[0] and drops the audio stream, and the "
                "preview socket has no audio event to carry it anyway. This node decodes "
                "that stream itself and shows a waveform you can play. It is separate from "
                "the video preview on purpose — chain it with either this pack's Preview "
                "Override or KJNodes' with taeh3. Wire it anywhere between the Director's "
                "model output and the sampler."
            ),
            inputs=[
                io.Model.Input("model", tooltip="Model to attach the audio preview to."),
                io.Vae.Input("audio_vae",
                             tooltip="minimax_h3_audio_vae. The real decoder — H3's audio "
                                     "has no tiny equivalent of taeh3, and does not need "
                                     "one: a three-second window is ~110 MB and a fraction "
                                     "of a second."),
                io.Float.Input("window_seconds", default=3.0, min=0.0, max=15.0, step=0.5,
                               tooltip="Seconds of audio to decode, from the START of the "
                                       "shot — the part that lines up with a preview "
                                       "animation looping from frame one. Cost scales with "
                                       "it. 0 decodes the whole clip (~520 MB at 15s)."),
                io.Int.Input("start_at_percent", default=50, min=0, max=100, step=5,
                             tooltip="Don't decode until this share of the steps is done. "
                                     "Early on the audio stream is still mostly noise, so "
                                     "the decodes would be spent on hiss."),
                io.Int.Input("every_n_steps", default=1, min=1, max=50, step=1,
                             optional=True,
                             tooltip="Never update more often than every N sampler steps."),
                io.Int.Input("max_preview_overhead", default=15, min=0, max=100, step=5,
                             optional=True,
                             tooltip="Cap on how much of the render time this may use, in "
                                     "percent. Updates are also never sent faster than the "
                                     "clip plays, so you always hear one through. 0 "
                                     "disables the cap."),
            ],
            outputs=[io.Model.Output(tooltip="Model with the audio preview attached.")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, model, audio_vae, window_seconds=3.0, start_at_percent=50,
                every_n_steps=1, max_preview_overhead=15) -> io.NodeOutput:
        # Two VAE sockets in one workflow is an easy mix-up, and it would otherwise land
        # mid-sample as a shape error. The audio VAE is the one with 32 latent channels;
        # H3's video VAE has 24.
        if getattr(audio_vae, "latent_channels", None) != AUDIO_CHANNELS:
            raise ValueError(
                "MiniMax H3 Audio Preview: 'audio_vae' has %s latent channels, not %d — "
                "that looks like the video VAE. Wire minimax_h3_audio_vae there."
                % (getattr(audio_vae, "latent_channels", "unknown"), AUDIO_CHANNELS)
            )

        m = model.clone()
        wrapper = _AudioOuterSampleWrapper(
            str(cls.hidden.unique_id), audio_vae, window_seconds, start_at_percent,
            every_n_steps, max_preview_overhead)

        # Its own key, so this chains with the video preview's wrapper instead of
        # displacing it. Registered where CFGGuider actually looks — see the video node.
        comfy.patcher_extension.add_wrapper_with_key(
            comfy.patcher_extension.WrappersMP.OUTER_SAMPLE,
            "minimax_h3_audio_preview", wrapper, m.model_options, is_model_options=True)

        registered = comfy.patcher_extension.get_all_wrappers(
            comfy.patcher_extension.WrappersMP.OUTER_SAMPLE, m.model_options,
            is_model_options=True)
        if wrapper not in registered and hasattr(m, "add_wrapper_with_key"):
            log.info("[MiniMaxDirector] using ModelPatcher-side wrapper registration.")
            m.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.OUTER_SAMPLE,
                                   "minimax_h3_audio_preview", wrapper)
        return io.NodeOutput(m)


class MiniMaxH3PreviewOverride(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PreviewOverrideCS",
            display_name="MiniMax H3 Preview Override",
            category="MiniMax H3",
            description=(
                "Live preview of the whole shot while it denoises, shown on this node. "
                "Core previews only ever draw the first latent frame; MiniMax H3's packed "
                "audio+video latent also has to be unpacked first, which the LTX preview "
                "nodes do not do. Wire between the Director's model output and the sampler. "
                "For the shot's sound, add MiniMax H3 Audio Preview — core's preview "
                "discards that half of the latent."
            ),
            inputs=[
                io.Model.Input("model", tooltip="Model to attach the preview to."),
                io.Vae.Input("vae", optional=True,
                             tooltip="minimax_h3_video_vae. Only needed for decode='vae (quality)'."),
                io.Combo.Input("decode", options=[DECODE_FAST, DECODE_VAE], default=DECODE_FAST,
                               tooltip="latent2rgb is a single matmul — effectively free, rough colours. "
                                       "vae is the real decoder: true colours, but it costs real time per "
                                       "preview, so raise every_n_steps with it."),
                io.Combo.Input("preview_target", options=[TARGET_NODE, TARGET_SAMPLER, TARGET_BOTH],
                               default=TARGET_NODE,
                               tooltip="Where the preview appears: on this node, in the sampler's usual "
                                       "preview slot (needs VideoHelperSuite), or both."),
                io.Int.Input("max_resolution", default=512, min=64, max=2048, step=32,
                             tooltip="Long edge of the preview image. With latent2rgb the source "
                                     "is latent-sized (a 1344x768 shot is an 84x48 grid), so this "
                                     "upscales — smooth, but soft. Use decode='vae (quality)' when "
                                     "you need to judge detail."),
                io.Int.Input("preview_frames", default=24, min=1, max=512, step=1,
                             tooltip="How many frames of the shot to show. Frames are thinned evenly, "
                                     "so this caps the cost without cropping the timeline."),
                io.Float.Input("preview_fps", default=24.0, min=1.0, max=60.0, step=1.0,
                               tooltip="The shot's own frame rate — 24 for H3. FLOAT so the "
                                       "Director's 'fps' output can be wired straight in. "
                                       "Whether the preview actually plays at this rate "
                                       "depends on 'playback'; with 'true speed' it is a "
                                       "ceiling, not a promise."),
                io.Int.Input("webp_quality", default=80, min=1, max=100, step=1, optional=True,
                             tooltip="WebP quality of the animation sent to the node."),
                io.Int.Input("every_n_steps", default=1, min=1, max=50, step=1, optional=True,
                             tooltip="Never preview more often than every N sampler steps."),
                io.Int.Input("max_preview_overhead", default=25, min=0, max=100, step=5, optional=True,
                             tooltip="Cap on how much of the render time previews may use, in "
                                     "percent. A full VAE decode can cost tens of seconds per "
                                     "preview; this spaces them out automatically instead of "
                                     "stalling the run. 0 disables the cap."),
                io.Boolean.Input("suppress_default_preview", default=True, optional=True,
                                 tooltip="Hide ComfyUI's built-in single-frame preview while this runs."),
                # NEW WIDGETS GO LAST. ComfyUI serialises widget values positionally, so
                # inserting one in the middle shifts every value after it in workflows that
                # were saved before it existed — this one first landed between preview_fps
                # and webp_quality and handed a saved 80 to a combo that has no such option.
                io.Combo.Input("playback", options=[PLAYBACK_TRUE, PLAYBACK_SOURCE],
                               default=PLAYBACK_TRUE, optional=True,
                               tooltip="'true speed' spreads the sampled frames across the "
                                       "shot's real duration, so the preview lasts exactly as "
                                       "long as the finished clip — but with latent2rgb that "
                                       "caps at preview_fps / 3.35, because there is one image "
                                       "per latent frame and H3 compresses time by that much. "
                                       "'source fps' plays them at preview_fps flat, like "
                                       "ComfyUI's own preview: motion reads at normal speed, "
                                       "the clip ends early. Judge timing with the first, "
                                       "movement with the second."),
            ],
            outputs=[io.Model.Output(tooltip="Model with the preview attached.")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, model, decode=DECODE_FAST, preview_target=TARGET_NODE, max_resolution=512,
                preview_frames=24, preview_fps=24.0, playback=PLAYBACK_TRUE, webp_quality=80,
                every_n_steps=1, max_preview_overhead=25, suppress_default_preview=True,
                vae=None) -> io.NodeOutput:
        if decode == DECODE_VAE and vae is None:
            raise ValueError(
                "MiniMax H3 Preview Override: decode is set to 'vae (quality)' but no VAE is "
                "connected. Wire minimax_h3_video_vae into 'vae', or switch decode back to "
                "'latent2rgb (fast)'."
            )

        m = model.clone()
        wrapper = _OuterSampleWrapper(
            str(cls.hidden.unique_id), decode, vae, max_resolution, preview_frames,
            preview_fps, webp_quality, every_n_steps, suppress_default_preview,
            preview_target, max_preview_overhead, playback)

        # Register where the sampler actually looks: CFGGuider reads
        #   get_all_wrappers(OUTER_SAMPLE, self.model_options, is_model_options=True)
        # `clone()` deep-copies model_options, so this cannot leak into the source patcher.
        comfy.patcher_extension.add_wrapper_with_key(
            comfy.patcher_extension.WrappersMP.OUTER_SAMPLE,
            "minimax_h3_preview", wrapper, m.model_options, is_model_options=True)

        registered = comfy.patcher_extension.get_all_wrappers(
            comfy.patcher_extension.WrappersMP.OUTER_SAMPLE, m.model_options,
            is_model_options=True)
        if wrapper not in registered and hasattr(m, "add_wrapper_with_key"):
            # a build that still reads the patcher-side dict; checking first avoids
            # registering twice and firing the preview for every step twice over
            log.info("[MiniMaxDirector] using ModelPatcher-side wrapper registration.")
            m.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.OUTER_SAMPLE,
                                   "minimax_h3_preview", wrapper)
        return io.NodeOutput(m)
