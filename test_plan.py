"""Offline checks for minimax_plan.py — the whole planner, no server, no pixels.

`minimax_plan` imports nothing but json and logging on purpose, so this runs anywhere:

    python test_plan.py

Every consumer of the planner depends on it agreeing with itself — the Director encodes
what the live COMPILED PROMPT panel shows, and the panel is only trustworthy because both
come through here. That is what these checks protect.

Run it after any change to minimax_plan.py, before committing.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("minimax_plan",
                                              os.path.join(HERE, "minimax_plan.py"))
plan = importlib.util.module_from_spec(spec)
sys.modules["minimax_plan"] = plan
spec.loader.exec_module(plan)

FPS = 24.0
_results = []


def check(name, got, want):
    ok = got == want
    _results.append((ok, name, got, want))
    return ok


def check_in(name, needle, haystack):
    ok = needle in haystack
    _results.append((ok, name, "present" if ok else "MISSING: %r" % needle, "present"))
    return ok


def check_not_in(name, needle, haystack):
    ok = needle not in haystack
    _results.append((ok, name, "absent" if ok else "PRESENT: %r" % needle, "absent"))
    return ok


def img(start, length, name="a.png", prompt="", end_frame=False):
    return {"type": "image", "start": start, "length": length, "imageFile": name,
            "fileName": name, "prompt": prompt, "isEndFrame": end_frame}


def tl(segments, ref_mode="OFF", **extra):
    d = {"reference_mode": ref_mode, "prompt_format": "minimax",
         "global_prompt": "a woman walks through a market", "segments": segments}
    d.update(extra)
    return d


def compile(tdata, duration_f=288, **kw):
    return plan.plan_timeline(tdata, 0, duration_f, FPS, **kw)


# ---------------------------------------------------------------- frame grid
check("align_frame_count(0)", plan.align_frame_count(0), 5)
check("align_frame_count(5)", plan.align_frame_count(5), 5)
check("align_frame_count(6)", plan.align_frame_count(6), 22)
check("align_frame_count(96)", plan.align_frame_count(96), 107)
check("align_frame_count(360)", plan.align_frame_count(360), 362)
check("align_frame_count is idempotent",
      plan.align_frame_count(plan.align_frame_count(101)), plan.align_frame_count(101))

# ---------------------------------------------------------------- fmt_seconds
check("fmt_seconds(0)", plan.fmt_seconds(0.0), "0s")
check("fmt_seconds(6)", plan.fmt_seconds(6.0), "6s")
check("fmt_seconds(1.5)", plan.fmt_seconds(1.5), "1.5s")
check("fmt_seconds(1.04) rounds", plan.fmt_seconds(1.04), "1s")

# ---------------------------------------------------------------- mode switch
check("no images -> t2va", compile(tl([]))["mode"], "t2va")
check("opening image -> fl2va", compile(tl([img(0, 144)]))["mode"], "fl2va")
check("refs on -> ref2va", compile(tl([img(0, 144)], ref_mode="ON"))["mode"], "ref2va")
check("ref_mode_from OFF", plan.ref_mode_from({"reference_mode": "OFF"}), False)
check("ref_mode_from ON", plan.ref_mode_from({"reference_mode": "ON"}), True)
check("ref_mode_from missing key", plan.ref_mode_from({}), False)

# ---------------------------------------------------------------- keyframe roles
roles = lambda p: [e["role"] for e in p["events"]]

check("one clip spanning the window is the opening frame, not the closing one",
      roles(compile(tl([img(0, 288)]))), [plan.ROLE_FIRST])
check("two back-to-back images -> first + last",
      roles(compile(tl([img(0, 144, "a.png"), img(144, 144, "b.png")]))),
      [plan.ROLE_FIRST, plan.ROLE_LAST])
check("an image short of the end is a middle",
      roles(compile(tl([img(0, 144, "a.png"), img(144, 141, "b.png")]))),
      [plan.ROLE_FIRST, plan.ROLE_MIDDLE])
check("isEndFrame wins over position",
      roles(compile(tl([img(0, 144, "a.png", end_frame=True)]))), [plan.ROLE_LAST])
check("a third image in the middle stays a middle",
      roles(compile(tl([img(0, 96, "a.png"), img(96, 96, "b.png"), img(192, 96, "c.png")]))),
      [plan.ROLE_FIRST, plan.ROLE_MIDDLE, plan.ROLE_LAST])

# ------------------------------------------------- issue #4: ref2va wording
# The reference guide gives the phrasing for concrete frame anchors verbatim: "the shot
# begins from <Picture 1>", "the shot's keyframe corresponds to <Picture 2>", "the shot
# ends on <Picture 3>". FL2VA's "opening frame" / "closing frame" belongs to the other
# guide and must not appear here.
# Reported case: two 6 s images, the second flush with the end of a 12 s window.
flush = compile(tl([img(0, 144, "a.png", prompt="she enters"),
                    img(144, 144, "b.png", prompt="she arrives")], ref_mode="ON"))
short = compile(tl([img(0, 144, "a.png", prompt="she enters"),
                    img(144, 141, "b.png", prompt="she arrives")], ref_mode="ON"))
noshot_slots = compile(tl([img(0, 144, "a.png"), img(144, 144, "b.png")], ref_mode="ON"))

check_not_in("ref2va never says 'opening frame'", "opening frame", flush["prompt"])
check_not_in("ref2va never says 'closing frame'", "closing frame", flush["prompt"])
check_in("the first image opens its shot",
         "[Shot 1] begins from <Picture 1>.", flush["prompt"])
# The reported case: a hard cut. The second image starts shot 2 at 6s; it does not end it.
check_in("the second image opens ITS shot rather than ending the video",
         "[Shot 2] begins from <Picture 2>.", flush["prompt"])
check_not_in("nothing 'ends on' unless the user said so", "ends on", flush["prompt"])
check("the phrasing no longer depends on where a segment stops",
      flush["prompt"], short["prompt"])
check_not_in("picture notes carry no filenames — the guide has no such notion",
             "a.png", flush["prompt"])

# "ends on" is reserved for a segment explicitly flagged as the end frame
ends = compile(tl([img(0, 144, "a.png", prompt="she enters"),
                   img(144, 144, "b.png", prompt="she arrives", end_frame=True)],
                  ref_mode="ON"))
check_in("an explicit end frame does say 'ends on'",
         "[Shot 2] ends on <Picture 2>.", ends["prompt"])
check("and it is the slot that takes the segment's last frame",
      [s.get("keyframe") for s in ends["ref_image_slots"]],
      [plan.ROLE_FIRST, plan.ROLE_LAST])
check("without the flag every timeline picture is a shot opener",
      [s.get("keyframe") for s in flush["ref_image_slots"]],
      [plan.ROLE_FIRST, plan.ROLE_FIRST])

# an image whose own segment carries no text has no shot number in the body to point at
noshot = compile(tl([img(0, 144, "a.png"), img(144, 144, "b.png")], ref_mode="ON"))
check_in("an untexted image is a composition anchor, with its time",
         "<Picture 1> is a composition anchor at 0s.", noshot["prompt"])
check_not_in("no shot number is invented when the body has none",
             "[Shot", noshot["prompt"])
mixed = compile(tl([img(0, 96, "a.png", prompt="she enters"),
                    img(96, 96, "b.png"),
                    img(192, 96, "c.png", prompt="she leaves")], ref_mode="ON"))
check_in("shot numbers follow the body, which counts only shots with text",
         "[Shot 2] begins from <Picture 3>.", mixed["prompt"])
check_in("the untexted middle image falls back to a composition anchor",
         "<Picture 2> is a composition anchor at 4s.", mixed["prompt"])
# the role on the slot picks which frame of a video segment is taken, and whether it is
# fitted to the canvas (minimax_director.py, ref_image_tensors). It follows the same rule
# as the wording, so the prompt describes the frame that actually gets encoded.
check("an image with no text of its own carries no keyframe flag",
      [s.get("keyframe") for s in noshot_slots["ref_image_slots"]], [None, None])

# fl2va must be untouched by that change — there the anchors are real
fl = compile(tl([img(0, 144, "a.png"), img(144, 144, "b.png")]))
check_in("fl2va still emits the alignment instruction",
         "aligns with the 0.00-second mark", fl["prompt"])
check_not_in("fl2va has no <Picture> reference notes", "<Picture", fl["prompt"])

# ------------------------------------------------- issue #6: the S.SS mark
# The alignment line names the effective duration. It must never name a mark past the
# end of the clip it describes, so it is floored to the hundredth, not rounded.
inst = plan.alignment_instruction
check_in("124 frames report 5.16, not 5.17", "5.16-second mark", inst(True, True, 1, 124 / 24.0))
check_not_in("5.17 is outside the video and must not appear",
             "5.17", inst(True, True, 1, 124 / 24.0))
check_in("an exact duration keeps its hundredth", "12.25-second mark",
         inst(True, True, 1, 294 / 24.0))
check_in("8s stays 8.00", "8.00-second mark", inst(True, True, 1, 192 / 24.0))
check_in("the closing-only case floors too", "13.66-second mark",
         inst(False, True, 3, 328 / 24.0))
for frames in (5, 22, 107, 124, 141, 209, 226, 311, 328, 345):
    text = inst(True, True, 1, frames / 24.0)
    mark = float(text.split("aligns with the ")[-1].split("-second")[0])
    check("%d frames: the mark stays inside the video" % frames,
          mark <= frames / 24.0 + 1e-9, True)
check("the opening-only case names no duration at all",
      "second mark" in inst(True, False, 1, 124 / 24.0), False)
check("no keyframe at all yields no instruction", inst(False, False, 1, 5.0), "")

# ---------------------------------------------------------------- reference ordinals
chars = [{"images": [{"b64": "x", "name": "c.png"}], "description": "a woman in a red coat"}]
withchar = compile(tl([img(0, 144, "a.png", prompt="she enters"),
                       img(144, 141, "b.png", prompt="she arrives")],
                      ref_mode="ON", characters=chars))
check("a character takes <Picture 1> ahead of the timeline",
      withchar["ref_image_slots"][0]["source"], "char")
check_in("timeline images number after the character",
         "[Shot 1] begins from <Picture 2>", withchar["prompt"])
check_in("the character becomes a named subject",
         "<Subject 1> is the character shown in <Picture 1>.", withchar["prompt"])
check("ref_images input slots sit between character and timeline",
      [s["source"] for s in compile(tl([img(0, 144)], ref_mode="ON", characters=chars),
                                    extra_ref_image_count=2)["ref_image_slots"]],
      ["char", "input", "input", "timeline"])

# issue #8: those input images used to be numbered and never described
described = compile(tl([img(0, 144, "a.png", prompt="she enters")], ref_mode="ON"),
                    extra_ref_image_count=3,
                    ref_image_notes="the kitchen set\n\na storyboard reference for the opening")
check_in("a note describes the input image it belongs to",
         "<Picture 1> is the kitchen set.", described["prompt"])
check_in("a blank line is a placeholder, so line 3 stays with image 3",
         "<Picture 3> is a storyboard reference for the opening.", described["prompt"])
check_not_in("the skipped image gets no invented note", "<Picture 2> is", described["prompt"])
check("described or not, every input image still takes a slot",
      len(described["ref_image_slots"]), 4)
check_not_in("no notes at all means no extra lines",
             "<Picture 1> is the", compile(tl([img(0, 144, "a.png", prompt="x")],
                                              ref_mode="ON"),
                                           extra_ref_image_count=2)["prompt"])
check_in("a trailing full stop in the note is not doubled",
         "<Picture 1> is the kitchen set.",
         compile(tl([img(0, 144, "a.png", prompt="x")], ref_mode="ON"),
                 extra_ref_image_count=1,
                 ref_image_notes="the kitchen set.")["prompt"])

many = compile(tl([img(i * 20, 20, "%d.png" % i) for i in range(14)], ref_mode="ON"))
check("reference images are capped at the model card's limit",
      len(many["ref_image_slots"]), plan.MAX_REF_IMAGES)

# ---------------------------------------------------------------- @char substitution
sub = compile(tl([img(0, 144, "a.png", prompt="@char1 turns around")],
                 ref_mode="ON", characters=chars))
check_in("@char1 resolves to the subject in ref2va", "<Subject 1> turns around", sub["prompt"])
check_not_in("no raw @char1 survives", "@char1", sub["prompt"])
sub_off = compile(tl([img(0, 144, "a.png", prompt="@char1 turns around")], characters=chars))
check_in("@char1 resolves to the description with refs off",
         "a woman in a red coat turns around", sub_off["prompt"])

# the slot count belongs to the editor, so the tag substitution follows it
check("a fourth slot's tag resolves too",
      plan.substitute_char_tags("@char4 waves", {4: "the dog"}), "the dog waves")
check("double digits are not eaten by the single-digit tag",
      plan.substitute_char_tags("@char10 and @char1",
                                {1: "one", 10: "ten"}), "ten and one")
check("an unresolved tag is left alone",
      plan.substitute_char_tags("@char7 waits", {1: "one"}), "@char7 waits")
many_chars = [{"images": [{"b64": "x", "name": "%d.png" % i}], "description": "subject %d" % i}
              for i in range(1, 6)]
five = compile(tl([img(0, 144, "a.png", prompt="@char5 arrives")], ref_mode="ON",
                  characters=many_chars))
check("five character slots all get a picture",
      [s["source"] for s in five["ref_image_slots"]][:5], ["char"] * 5)
check_in("the fifth slot resolves in a shot prompt", "<Subject 5> arrives", five["prompt"])

# ------------------------------------------------- issue #7: soundscape / music
audio = tl([img(0, 144)], ref_mode="ON")
audio["global_prompt"] = ("a woman walks through a market\n"
                          "Audio: Market chatter and footsteps on stone.\n"
                          "Music: A slow solo piano, no swell.")
lifted = compile(audio)
check_in("an Audio: line becomes overall_soundscape",
         "overall_soundscape: Market chatter and footsteps on stone.", lifted["prompt"])
check_in("a Music: line becomes non_diegetic_music",
         "non_diegetic_music: A slow solo piano, no swell.", lifted["prompt"])
check_not_in("the lifted lines leave the description",
             "Audio: Market chatter", lifted["prompt"].split("overall_soundscape")[0])

passed = compile(tl([img(0, 144)], ref_mode="ON"),
                 soundscape="Quiet indoor room tone throughout.", music="N/A")
check_in("the soundscape parameter fills overall_soundscape",
         "overall_soundscape: Quiet indoor room tone throughout.", passed["prompt"])
check_in("the music parameter fills non_diegetic_music",
         "non_diegetic_music: N/A", passed["prompt"])

both = compile(audio, soundscape="Explicit wins.")
check_in("an explicit parameter beats the lifted line",
         "overall_soundscape: Explicit wins.", both["prompt"])

plain = compile(tl([img(0, 144)], ref_mode="ON"))
check_not_in("no empty soundscape section when there is nothing to say",
             "overall_soundscape:", plain["prompt"])

# the editor's two boxes live in the timeline, so both consumers read one value
from_tl = compile(tl([img(0, 144)], ref_mode="ON",
                     overall_soundscape="Rain on a tin roof throughout.",
                     non_diegetic_music="N/A"))
check_in("the timeline's overall_soundscape reaches the prompt",
         "overall_soundscape: Rain on a tin roof throughout.", from_tl["prompt"])
check_in("the timeline's non_diegetic_music reaches the prompt",
         "non_diegetic_music: N/A", from_tl["prompt"])
check("an explicit argument overrides the timeline",
      "overall_soundscape: Passed in." in
      compile(tl([img(0, 144)], ref_mode="ON",
                 overall_soundscape="From the timeline."),
              soundscape="Passed in.")["prompt"], True)
check("a blank argument does not blank the timeline value",
      "overall_soundscape: From the timeline." in
      compile(tl([img(0, 144)], ref_mode="ON",
                 overall_soundscape="From the timeline."), soundscape="  ")["prompt"], True)
lifted_vs_box = compile(tl([img(0, 144)], ref_mode="ON",
                           overall_soundscape="From the box.",
                           global_prompt="a shot\nAudio: From the prompt line."))
check_in("the box wins over an Audio: line in the prompt",
         "overall_soundscape: From the box.", lifted_vs_box["prompt"])
check("the sound boxes do not switch in retake mode",
      compile({"reference_mode": "ON", "prompt_format": "minimax",
               "overall_soundscape": "One value for the whole timeline.",
               "retakeMode": True,
               "retakeVideo": {"imageFile": "b.mp4", "videoDurationFrames": 480},
               "retakeStart": 0, "retakeLength": 96,
               "retakePrompt": "she stumbles", "segments": []},
              duration_f=96)["prompt"].count("One value for the whole timeline."), 1)

check("split_audio_music leaves a prompt without labels alone",
      plan.split_audio_music("just a description"), ("just a description", "", ""))
check("split_audio_music takes a label only at the start of a line",
      plan.split_audio_music("a car with no audio: here")[1], "")

# ------------------------------------------- issue #10: whose voice is <Audio N>?
two_chars = [{"images": [{"b64": "x", "name": "c1.png"}], "description": "a woman"},
             {"images": [{"b64": "x", "name": "c2.png"}], "description": "a man"}]


def audio(subject=None):
    seg = {"audioFile": "voice.wav", "fileName": "voice.wav", "start": 0, "length": 288}
    if subject is not None:
        seg["subject"] = subject
    return seg


loose_audio = compile(tl([img(0, 288, "a.png", prompt="they talk")], ref_mode="ON",
                         characters=two_chars, audioSegments=[audio()]),
                      use_custom_audio=True)
check_in("an unassigned clip keeps the general wording",
         "<Audio 1> is a reference audio clip: follow its voice and timbre.",
         loose_audio["prompt"])
check_in("and is kept in the general retention line",
         "Keep the voice and timbre of <Audio 1>.", loose_audio["prompt"])

bound = compile(tl([img(0, 288, "a.png", prompt="they talk")], ref_mode="ON",
                   characters=two_chars,
                   audioSegments=[audio(subject=2)]),
                use_custom_audio=True)
check_in("a clip tied to a subject uses the guide's own sentence",
         "<Audio 1> is the voice-timbre reference for <Subject 2> (S2).", bound["prompt"])
check_in("and says so once more where it matters",
         "<Subject 2> speaks with the voice from <Audio 1>.", bound["prompt"])
check_not_in("a bound clip is not repeated as an anonymous one",
             "Keep the voice and timbre of", bound["prompt"])

mixed_audio = compile(tl([img(0, 288, "a.png", prompt="they talk")], ref_mode="ON",
                         characters=two_chars,
                         audioSegments=[audio(subject=1), audio()]),
                      use_custom_audio=True)
check_in("one bound clip names its subject",
         "<Audio 1> is the voice-timbre reference for <Subject 1> (S1).",
         mixed_audio["prompt"])
check_in("the other stays general", "Keep the voice and timbre of <Audio 2>.",
         mixed_audio["prompt"])
check("a subject of 0 or nonsense is treated as unassigned",
      (plan._audio_subject_slot({"subject": 0}), plan._audio_subject_slot({"subject": ""}),
       plan._audio_subject_slot({"subject": "none"}), plan._audio_subject_slot({}),
       plan._audio_subject_slot({"subject": "2"})),
      (None, None, None, None, 2))
check("a clip pointing at a slot with no images binds nothing",
      "voice-timbre reference" in
      compile(tl([img(0, 288, "a.png", prompt="x")], ref_mode="ON",
                 characters=two_chars, audioSegments=[audio(subject=3)]),
              use_custom_audio=True)["prompt"], False)

# ---------------------------------------------------------------- comfyui format
cf = compile(tl([img(0, 144, "a.png"), img(144, 141, "b.png")], ref_mode="ON",
                prompt_format="comfyui"))
check_in("the comfyui format keeps its own reference-notes line",
         "Reference notes: <Picture 1> is a composition anchor at 0s", cf["prompt"])
check_not_in("the comfyui format has no minimax sections",
             "subject_definitions:", cf["prompt"])
check("an unknown format falls back to minimax",
      compile(tl([img(0, 144)], prompt_format="nonsense"))["prompt"],
      compile(tl([img(0, 144)], prompt_format="minimax"))["prompt"])

# ---------------------------------------------------------------- retake
retake_tl = tl([img(0, 288, "a.png", prompt="she walks")], retakeMode=True,
               retakeVideo={"imageFile": "base.mp4", "videoDurationFrames": 480},
               retakeStart=48, retakeLength=96, retakePrompt="she stumbles")
r = plan.retake_state(retake_tl)
check("retake_state reads the marked range", (r["start"], r["length"]), (48, 96))
check("retake_state is None when the mode is off", plan.retake_state(tl([])), None)
check("retake_state is None without a base video",
      plan.retake_state({"retakeMode": True, "retakeVideo": {}}), None)
rp = plan.plan_timeline(retake_tl, 48, 96, FPS)
check_in("the retake prompt replaces the timeline text", "she stumbles", rp["prompt"])
check("a retake counts as having a keyframe", rp["mode"], "fl2va")

# ---------------------------------------------------------------- windowing
check("a segment outside the window is ignored",
      len(compile(tl([img(400, 96)]), duration_f=288)["events"]), 0)
check("overlaps() is half-open at the start",
      plan.overlaps({"start": 288, "length": 96}, 0, 288), False)
check("overlaps() catches a segment straddling the end",
      plan.overlaps({"start": 240, "length": 96}, 0, 288), True)

# ---------------------------------------------------------------- degenerate input
check("an empty timeline still yields a prompt", compile(tl([]))["prompt_is_fallback"], False)
check("no prompt anywhere falls back to 'video'",
      plan.plan_timeline({}, 0, 288, FPS)["prompt"], "video")
check("parse_timeline survives broken json", plan.parse_timeline("{not json"), {})
check("parse_timeline on an empty string", plan.parse_timeline(""), {})
ok_tiny = True
try:
    plan.plan_timeline(tl([img(0, 144)]), 0, 1, FPS)
except Exception:
    ok_tiny = False
check("a one-frame window does not raise", ok_tiny, True)

# ---------------------------------------------------------------- report
failed = [r for r in _results if not r[0]]
for ok, name, got, want in _results:
    if not ok:
        print("FAIL  %s\n        got:  %r\n        want: %r" % (name, got, want))
print("\n%d checks, %d passed, %d failed" %
      (len(_results), len(_results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
