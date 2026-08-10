"""Offline checks for the audio half of minimax_preview.py — no server, no GPU, no model.

    <ComfyUI>/venv/bin/python test_preview.py

Unlike `test_plan.py` this one needs torch, because what it protects is tensor plumbing:
which of H3's two packed streams gets picked up, how a window in seconds becomes latent
frames at 40 fps, and whether the bytes handed to the browser are a real audio file. The
ComfyUI modules `minimax_preview` imports are stubbed below — none of them are involved in
the parts under test, and stubbing them is what keeps this runnable without a server.

The stream index is the check that earns its keep. Video and audio differ by one index in
a packed latent, and getting it wrong does not raise — it decodes silence, or noise, and
reads as a model problem. Run this after any change to the audio path.
"""
import importlib.util
import io
import logging
import os
import sys
import types
import wave

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import torch
except ImportError:
    print("SKIP  test_preview.py needs torch — run it with the python that runs ComfyUI:\n"
          "        <ComfyUI>/venv/bin/python test_preview.py")
    sys.exit(0)


# ---------------------------------------------------------------- stub ComfyUI
def _module(name, **attrs):
    """Register a stub module — and hang it off its parent.

    `import comfy.utils` finds the entry in sys.modules, but `comfy.utils.unpack_latents`
    then reads an *attribute* of `comfy`, which only the real import machinery sets.
    """
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    if "." in name:
        parent, _, leaf = name.rpartition(".")
        setattr(sys.modules[parent], leaf, m)
    return m


def _unpack_latents(packed, shapes):
    """Stand-in for comfy.utils.unpack_latents: split a flat pack back into its streams."""
    out, offset = [], 0
    for shape in shapes:
        n = 1
        for dim in shape:
            n *= dim
        out.append(packed[..., offset:offset + n].reshape((packed.shape[0],) + tuple(shape)))
        offset += n
    return out


def _vae_decode_audio(vae, samples):
    """Stand-in for comfy_extras.nodes_audio.vae_decode_audio, same contract."""
    latent = samples["samples"]
    if getattr(latent, "is_nested", False):
        latent = latent.unbind()[-1]
    return {"waveform": vae.decode(latent),
            "sample_rate": getattr(vae, "audio_sample_rate", 32000)}


_module("comfy")
_module("comfy.utils", unpack_latents=_unpack_latents)
_module("comfy.model_management",
        OOM_EXCEPTION=getattr(torch.cuda, "OutOfMemoryError", MemoryError))
_module("comfy.patcher_extension",
        WrappersMP=types.SimpleNamespace(OUTER_SAMPLE="outer_sample"),
        add_wrapper_with_key=lambda *a, **k: None,
        get_all_wrappers=lambda *a, **k: [])
_module("latent_preview", LatentPreviewer=type("LatentPreviewer", (), {
    "decode_latent_to_preview_image": lambda self, fmt, x0: None}))

class _Routes:
    """Collects the routes the module registers, so the test can assert one was."""

    def __init__(self):
        self.registered = []

    def get(self, path):
        self.registered.append(path)
        return lambda fn: fn


_ROUTES = _Routes()
_module("server", PromptServer=types.SimpleNamespace(
    instance=types.SimpleNamespace(routes=_ROUTES, send_sync=lambda *a, **k: None)))
_module("aiohttp", web=types.SimpleNamespace(Response=lambda **kw: kw))
_module("protocol", BinaryEventTypes=types.SimpleNamespace(PREVIEW_IMAGE=1))
_module("comfy_extras")
_module("comfy_extras.nodes_audio", vae_decode_audio=_vae_decode_audio)
_module("comfy_api")
_io_stub = types.SimpleNamespace(
    ComfyNode=type("ComfyNode", (), {}),
    Schema=lambda **kw: kw, NodeOutput=lambda *a, **k: None,
    Hidden=types.SimpleNamespace(unique_id="unique_id"))
for _name in ("Model", "Vae", "Combo", "Int", "Float", "Boolean", "Image", "Latent"):
    setattr(_io_stub, _name, types.SimpleNamespace(Input=lambda *a, **k: None,
                                                  Output=lambda *a, **k: None))
_module("comfy_api.latest", io=_io_stub)

spec = importlib.util.spec_from_file_location("minimax_preview",
                                              os.path.join(HERE, "minimax_preview.py"))
prev = importlib.util.module_from_spec(spec)
sys.modules["minimax_preview"] = prev
spec.loader.exec_module(prev)

_results = []


def check(name, got, want):
    _results.append((got == want, name, got, want))


# H3's two streams at the shapes core builds them: video [B,24,T,h,w], audio [B,32,2,T].
VIDEO_SHAPE = (24, 7, 6, 8)
AUDIO_SHAPE = (32, 2, 40)                        # 40 latent frames = one second
VIDEO = torch.zeros((1,) + VIDEO_SHAPE)
AUDIO = torch.ones((1,) + AUDIO_SHAPE)           # ones, so a mix-up shows up as silence


class _Nested:
    """What the sampler hands the callback when it has not been flattened."""

    is_nested = True

    def __init__(self, *streams):
        self.tensors = list(streams)


class _UnbindOnly:
    """The same thing on a build that only exposes unbind()."""

    is_nested = True

    def __init__(self, *streams):
        self._streams = list(streams)

    def unbind(self):
        return list(self._streams)


class _FakeAudioVAE:
    """Shaped like the real one: [B,32,2,T] -> [B,2,T*800].

    Records the latent it was handed, so a check can confirm the sampler's audio_scale was
    divided out before the decode rather than trusting the call to have happened.
    """

    latent_channels = 32
    audio_sample_rate = 32000

    def __init__(self):
        self.seen = None

    def decode(self, z):
        self.seen = z
        return torch.zeros(z.shape[0], 2, z.shape[-1] * 800)


class _BrokenVAE:
    latent_channels = 32

    def decode(self, z):
        raise ValueError("decoder exploded")


# ---------------------------------------------------------------- stream picking
check("nested latent: audio is the last stream, not the first",
      tuple(prev._audio_stream(_Nested(VIDEO, AUDIO)).shape), tuple(AUDIO.shape))
check("nested latent without .tensors: unbind() is used instead",
      tuple(prev._audio_stream(_UnbindOnly(VIDEO, AUDIO)).shape), tuple(AUDIO.shape))
check("nested latent: video is still the first stream",
      tuple(prev._video_stream(_Nested(VIDEO, AUDIO)).shape), tuple(VIDEO.shape))

PACKED = torch.cat([VIDEO.reshape(1, -1), AUDIO.reshape(1, -1)], dim=-1)
SHAPES = [VIDEO_SHAPE, AUDIO_SHAPE]
check("flat pack: audio unpacks at index 1",
      tuple(prev._audio_stream(PACKED, SHAPES).shape), tuple(AUDIO.shape))
check("flat pack: the audio found is the audio packed",
      bool(torch.equal(prev._audio_stream(PACKED, SHAPES), AUDIO)), True)
check("flat pack: video still unpacks at index 0",
      bool(torch.equal(prev._video_stream(PACKED, SHAPES), VIDEO)), True)

check("a bare audio latent passes through",
      tuple(prev._audio_stream(AUDIO).shape), tuple(AUDIO.shape))
check("a video-only 5D latent has no audio", prev._audio_stream(VIDEO), None)
check("a 4D image latent is not mistaken for audio",
      prev._audio_stream(torch.zeros(1, 16, 64, 64)), None)
check("no latent, no audio", prev._audio_stream(None), None)
check("a flat pack with no shapes cannot be unpacked", prev._audio_stream(PACKED), None)

# ---------------------------------------------------------------- window maths
check("40 latent frames per second", prev.AUDIO_LATENT_FPS, 40.0)
check("a 0.5s window of a 1s latent is 20 frames",
      prev.audio_latent_window(AUDIO, 0.5).shape[-1], 20)
check("the window is taken from the head",
      bool(torch.equal(prev.audio_latent_window(AUDIO, 0.5), AUDIO[..., :20])), True)
check("window 0 keeps the whole clip", prev.audio_latent_window(AUDIO, 0).shape[-1], 40)
check("a window longer than the clip keeps the whole clip",
      prev.audio_latent_window(AUDIO, 99).shape[-1], 40)
check("a tiny window still yields one frame",
      prev.audio_latent_window(AUDIO, 0.001).shape[-1], 1)

# ---------------------------------------------------------------- the audio scale
# ModelSamplingAV carries the audio stream at shift/audio_shift so both streams share one
# schedule, and the Director's defaults make that 12/3. Decoding without dividing it out is
# what turned every clip into loud noise while the video stream looked perfectly fine.
def guider(scale=None, says=None):
    """A stand-in guider. `scale` is what model_sampling carries, `says` what the model's
    accessor reports — core's returns 1.0 whenever its latent_shapes is unset."""
    sampling = types.SimpleNamespace(audio_scale=1.0 if scale is None else scale)
    model = types.SimpleNamespace(model_sampling=sampling)
    if says is not None:
        model.audio_scale = lambda: says
    return types.SimpleNamespace(model_patcher=types.SimpleNamespace(model=model))


check("the model's own accessor is the authority",
      prev.audio_scale_of(guider(4.0, says=12.0 / 3.0), 2), 4.0)
check("an accessor answering 1.0 on a two-stream pack does not end it",
      prev.audio_scale_of(guider(4.0, says=1.0), 2), 4.0)
check("model_sampling is read when there is no accessor",
      prev.audio_scale_of(guider(4.0), 2), 4.0)
check("no audio shift means no rescaling", prev.audio_scale_of(guider(), 2), 1.0)
check("a single-stream latent is never rescaled",
      prev.audio_scale_of(guider(4.0), 1), 1.0)
check("a model that knows nothing about it is left alone",
      prev.audio_scale_of(types.SimpleNamespace(), 2), 1.0)
check("a nonsense scale is ignored rather than dividing by zero",
      prev.audio_scale_of(guider(0.0, says=0.0), 2), 1.0)

vae = _FakeAudioVAE()
prev._decode_audio(vae, AUDIO * 4.0, 0, audio_scale=4.0)
check("the scale is divided out before the decode",
      bool(torch.allclose(vae.seen, AUDIO)), True)
prev._decode_audio(vae, AUDIO, 0, audio_scale=1.0)
check("scale 1.0 leaves the latent untouched",
      bool(torch.allclose(vae.seen, AUDIO)), True)

# ---------------------------------------------------------------- stereo + envelope
check("[B,C,L] loses its batch dim", tuple(prev._stereo(torch.zeros(1, 2, 800)).shape), (2, 800))
check("mono is doubled into stereo", tuple(prev._stereo(torch.zeros(1, 800)).shape), (2, 800))
check("more than two channels are cut to two",
      tuple(prev._stereo(torch.zeros(1, 4, 800)).shape), (2, 800))
check("the level is left alone here, not clipped",
      float(prev._stereo(torch.full((1, 2, 8), 4.0)).max()), 4.0)

# Clipping an over-range clip is what made noise sound like noise and drew the envelope as a
# solid rectangle; scaling keeps the shape so the waveform still says something.
check("an over-range clip is scaled to fit",
      float(prev._normalise(torch.full((2, 8), 4.0), 4.0).max()), 1.0)
check("scaling preserves the shape",
      [round(float(v), 3) for v in prev._normalise(torch.tensor([[2.0, -4.0]]), 4.0)[0]],
      [0.5, -1.0])
check("a clip already in range is untouched",
      float(prev._normalise(torch.full((2, 8), 0.25), 0.25).max()), 0.25)

spiked = torch.zeros(2, 8000)
spiked[0, 100] = 1.0                             # one spike, left channel, first bucket
env = prev._envelope(spiked, buckets=8)
check("one row per channel", len(env), 2)
check("bucket count honoured", len(env[0]), 8)
check("the spike lands in the first bucket", env[0][0], 1.0)
check("silence reads as silence", max(env[1]), 0.0)
check("an empty clip has no envelope", prev._envelope(torch.zeros(2, 0)), [])
check("fewer samples than buckets still works",
      len(prev._envelope(torch.zeros(2, 3), 8)[0]), 3)

# ---------------------------------------------------------------- what the browser gets
# 0.5s of a 32 kHz stereo tone, loud enough that a silent output would show up as one.
tone = torch.sin(torch.arange(0, 16000, dtype=torch.float32) * 0.05).repeat(2, 1) * 0.5
raw = prev._encode_wav(tone, 32000)

# One format, deterministically, so these checks can verify the actual bytes that reach the
# browser rather than trusting a codec to have produced something playable.
check("the clip is served from a registered route",
      prev.AUDIO_ROUTE in _ROUTES.registered, True)
check("WAV magic", raw[:4], b"RIFF")
check("the header declares WAVE", raw[8:12], b"WAVE")
with wave.open(io.BytesIO(raw)) as w:
    check("WAV is stereo", w.getnchannels(), 2)
    check("WAV is 16-bit", w.getsampwidth(), 2)
    check("WAV keeps the sample rate", w.getframerate(), 32000)
    check("WAV keeps every sample", w.getnframes(), 16000)
    frames = w.readframes(w.getnframes())
check("every sample is present in the data chunk", len(frames), 16000 * 2 * 2)
check("the tone is not silence", max(frames) > 0, True)

# `--write-clip` drops the encoded clip next to this file: ground truth for "encoder or
# browser?" — if the file plays in a media player, the encoder is fine.
if "--write-clip" in sys.argv:
    path = os.path.join(HERE, "audio_preview_check.wav")
    with open(path, "wb") as f:
        f.write(raw)
    print("wrote %s — %d bytes, a 0.5s 32 kHz stereo tone.\n"
          "  If that plays in a media player, the encoder is fine.\n" % (path, len(raw)))

# ---------------------------------------------------------------- the audio gate
def wrapper(vae=None, **kw):
    opts = dict(node_id="1", audio_vae=vae if vae is not None else _FakeAudioVAE(),
                window_seconds=3.0, start_at_percent=50, every_n_steps=1, max_overhead=15)
    opts.update(kw)
    return prev._AudioOuterSampleWrapper(**opts)


def state(**kw):
    s = {"off": False, "warned": False, "logged": False, "sent": 0, "seq": 0,
         "cost": 0.0, "finished": 0.0, "clip": 0.0, "steps": 0, "due": 0, "scale": None}
    s.update(kw)
    return s


on = wrapper()
check("no audio before the gate", on._due(4, 20, state()), False)
check("audio once the gate is passed", on._due(9, 20, state()), True)
check("audio on the last step", on._due(19, 20, state()), True)
check("a disabled run stays silent", on._due(19, 20, state(off=True)), False)
check("0% starts from the first step",
      wrapper(start_at_percent=0)._due(0, 20, state()), True)
check("100% waits for the last step",
      wrapper(start_at_percent=100)._due(18, 20, state()), False)
check("every_n_steps skips the steps in between",
      wrapper(every_n_steps=4)._due(11, 20, state()), False)
check("every_n_steps keeps the ones it lands on",
      wrapper(every_n_steps=4)._due(12, 20, state()), True)

# ---------------------------------------------------------------- the throttle
check("nothing to wait for on the first update", on._skip(100.0, state()), False)
check("a clip still playing holds the next update",
      on._skip(100.0, state(finished=99.0, clip=3.0)), True)
check("once it has played through, the next may go",
      on._skip(103.5, state(finished=100.0, clip=3.0)), False)
check("an expensive decode is spaced by the overhead budget",
      on._skip(105.0, state(finished=100.0, cost=1.0)), True)  # 15% -> ~5.7s gap
check("overhead 0 means only the play-through wait",
      wrapper(max_overhead=0)._skip(101.0, state(finished=100.0, cost=99.0)), False)

# ---------------------------------------------------------------- the payload
s = state()
payload = wrapper(window_seconds=0.5)._payload(PACKED, SHAPES, s)
check("a payload is produced", sorted(payload or {}),
      ["audio_mime", "envelope", "kb", "seconds", "url"])
check("the window decides the duration, not the latent", payload["seconds"], 0.5)
check("the envelope has both channels", len(payload["envelope"]), 2)
check("the url points at the route, for this node, with a sequence number",
      payload["url"], prev.AUDIO_ROUTE + "?node_id=1&seq=1")
check("the bytes are parked for the route to serve",
      prev._AUDIO_CLIPS["1"][1][:4], b"RIFF")
check("the parked clip is the whole clip",
      len(prev._AUDIO_CLIPS["1"][1]), 44 + 16000 * 2 * 2)
check("the payload's size matches what was parked",
      payload["kb"], len(prev._AUDIO_CLIPS["1"][1]) // 1024)
check("a second update gets a fresh url so nothing is answered from cache",
      wrapper(window_seconds=0.5)._payload(PACKED, SHAPES, s)["url"],
      prev.AUDIO_ROUTE + "?node_id=1&seq=2")
check("window 0 decodes the whole second",
      wrapper(window_seconds=0)._payload(PACKED, SHAPES, state())["seconds"], 1.0)

# ---------------------------------------------------------------- failures stay silent
# The next check makes the decoder throw on purpose, and the code under test logs that with
# exc_info — a real traceback in the output of a passing test reads as a failure, so mute it.
logging.disable(logging.CRITICAL)
try:
    s = state()
    check("a decode failure returns no payload rather than raising",
          wrapper(vae=_BrokenVAE())._payload(PACKED, SHAPES, s), None)
    check("and it turns audio off for the rest of the run", s["off"], True)

    s = state()
    check("a video-only latent yields no payload", on._payload(VIDEO, None, s), None)
    check("and that is not treated as a failure", s["off"], False)
finally:
    logging.disable(logging.NOTSET)

# ---------------------------------------------------------------- report
failed = [r for r in _results if not r[0]]
for ok, name, got, want in _results:
    if not ok:
        print("FAIL  %s\n        got:  %r\n        want: %r" % (name, got, want))
print("\n%d checks, %d passed, %d failed" %
      (len(_results), len(_results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
