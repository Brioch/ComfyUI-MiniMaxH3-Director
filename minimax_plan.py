"""Timeline -> MiniMax H3 plan, with no pixels touched.

Everything that decides *what* a timeline means — which image is the opening frame,
which becomes a <Picture i> reference, what the storyboard prompt reads like — lives
here and works on metadata alone. Three callers share it:

* the Director node, which then loads exactly the media the plan calls for,
* the /minimax_director/compile_prompt endpoint behind the editor's live prompt
  preview, which loads nothing at all,
* the chain node, which plans one window per shot.

Keeping it in one place is the point: a prompt preview that disagrees with what is
actually sent would be worse than no preview.
"""

import json
import logging
import re

log = logging.getLogger(__name__)

MODEL_FPS = 24.0

# Limits straight from MiniMax's own model card (huggingface.co/MiniMaxAI/MiniMax-H3),
# not from ComfyUI's node signatures — the two do not agree everywhere.
MAX_REF_IMAGES = 9          # "<= 9 images"
MAX_REF_VIDEOS = 3          # "<= 3 clips"
MAX_REF_AUDIOS = 3          # "<= 3 clips"
MAX_REF_FILES = 12          # "at most 12 files in total across all input types"
REF_VIDEO_MIN_SEC = 2.0     # "each clip must be 2-15 seconds long"
REF_VIDEO_MAX_SEC = 15.0
REF_VIDEO_TOTAL_SEC = 15.0  # "total duration <= 15 seconds"
# Output envelope: "4-15 seconds" at 24 fps
TRAINED_MIN_FRAMES = 96
TRAINED_MAX_FRAMES = 360

ROLE_FIRST = "first"
ROLE_LAST = "last"
ROLE_MIDDLE = "middle"

# --- the full-reference vocabulary (references/ref-en.txt) ---------------------------
# The retention markers are "fixed English values in the output format", so they are
# emitted verbatim and never paraphrased. Whatever the editor sends is clamped back to a
# default rather than allowed to reach the prompt as an invented word.
RETENTION_VISIBLE = ("fully_preserved", "partially_preserved",
                     "attribute_transfer", "weak_reference")
RETENTION_AUDIO = ("fully_copy", "partially_copy", "reference", "weak_reference")
# fully_preserved is the right default even for a motion reference: the marker preserves
# the *defined role* of the content, and a reference video's defined role is its camera
# work, not its pixels. Audio defaults to `reference` because nothing here copies a
# signal — the track guides timbre and delivery.
RETENTION_DEFAULT = "fully_preserved"
RETENTION_AUDIO_DEFAULT = "reference"

# <Subject N> is not a character slot. The guide lists "people, animals, or objects;
# scenes, backgrounds, or environments; clothing, props, interfaces, or visual effects;
# styles, actions, expressions, or poses". The noun is only used when a slot has no
# description of its own to put there.
SUBJECT_KINDS = {
    "person": "the person",
    "animal": "the animal",
    "object": "the object",
    "environment": "the environment",
    "clothing": "the outfit",
    "prop": "the prop",
    "interface": "the interface",
    "effect": "the visual effect",
    "style": "the visual style",
    "action": "the action",
    "expression": "the expression",
    "pose": "the pose",
}
SUBJECT_KIND_DEFAULT = "person"

# What "retained" means per kind, so a fully_preserved line says something the model can
# act on instead of repeating the marker back at it. `picture` and `video` are not subject
# kinds; they share the table because they need the same sentence.
RETAINED_DETAIL = {
    "person": "identity, face and clothing",
    "animal": "markings, coat and build",
    "object": "shape, colour and markings",
    "environment": "layout, furnishing and lighting",
    "clothing": "cut, colour and material",
    "prop": "shape, colour and wear",
    "interface": "layout, typography and iconography",
    "effect": "shape, colour and behaviour",
    "style": "palette, texture and grade",
    "action": "timing and body mechanics",
    "expression": "expression and gaze",
    "pose": "posture and limb placement",
    "picture": "framing and composition",
    "storyboard": "viewpoint, subject placement and shot order",
    "video": "camera work and motion",
}

# How a timeline image is used. `auto` keeps the behaviour the timeline already implies —
# classify_events decides whether it opens, closes or anchors a shot. The other two cover
# the guide's remaining cases: a shot-planning image, and an image that only *defines*
# something and therefore earns no standalone <Picture N> entry at all.
REF_ROLE_AUTO = "auto"
REF_ROLE_STORYBOARD = "storyboard"
REF_ROLE_SUBJECT = "subject"
REF_ROLES = (REF_ROLE_AUTO, REF_ROLE_STORYBOARD, REF_ROLE_SUBJECT)

MAX_SUBJECT_SLOTS = 9

TASK_KEYFRAME = "keyframe completion"
TASK_REFERENCE = "reference generation"
TASK_EDITING = "video editing"
TASK_CONTINUATION = "video continuation"
TASK_AUDIO_REUSE = "audio reuse"
TASK_AUDIO_REFERENCE = "audio reference"
# the guide's own table order, so a combined prefix always reads the same way round
TASK_ORDER = (TASK_KEYFRAME, TASK_REFERENCE, TASK_EDITING, TASK_CONTINUATION,
              TASK_AUDIO_REUSE, TASK_AUDIO_REFERENCE)


def align_frame_count(n):
    """H3 only accepts frame counts on the 17k+5 grid."""
    while n % 17 != 5:
        n += 1
    return n


def fmt_seconds(value):
    """0.0 -> '0s', 1.5 -> '1.5s' — the notation used in MiniMax's own templates."""
    rounded = round(float(value), 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return "%ds" % int(round(rounded))
    return "%.1fs" % rounded


def substitute_char_tags(text, replacements):
    """Swap @ref1/@character1/@char1 .. @ref9 for their resolved text.

    A slot is no longer necessarily a character, so @refN is the name that fits what the
    slots now hold. @characterN/@charN keep working for every slot — prompts written
    against the old three-slot panel must not break.

    Highest slot first: @ref1 is a prefix of @ref10 today only in spirit, but @char1 *is*
    a prefix of nothing while @ref1 would be of @ref11 if the cap ever moved. Descending
    order makes that class of bug impossible rather than merely absent.
    """
    if not text:
        return text or ""
    for slot in range(MAX_SUBJECT_SLOTS, 0, -1):
        value = replacements.get(slot)
        if not value:
            continue
        for tag in ("@character%d" % slot, "@char%d" % slot, "@ref%d" % slot):
            text = text.replace(tag, value)
    return text


def sanitize_retention(value, audio=False):
    """Clamp a retention marker to the guide's fixed vocabulary."""
    allowed = RETENTION_AUDIO if audio else RETENTION_VISIBLE
    text = str(value or "").strip().lower()
    if text in allowed:
        return text
    return RETENTION_AUDIO_DEFAULT if audio else RETENTION_DEFAULT


def sanitize_kind(value):
    text = str(value or "").strip().lower()
    return text if text in SUBJECT_KINDS else SUBJECT_KIND_DEFAULT


def sanitize_ref_role(value):
    text = str(value or "").strip().lower()
    return text if text in REF_ROLES else REF_ROLE_AUTO


def overlaps(seg, win_start, win_end):
    start = float(seg.get("start", 0))
    length = float(seg.get("length", 1))
    return start < win_end and start + length > win_start


def parse_timeline(timeline_data):
    try:
        return json.loads(timeline_data) if timeline_data else {}
    except Exception as e:
        log.error("[MiniMaxDirector] timeline_data parse error: %s", e)
        return {}


def ref_mode_from(tdata):
    """Is the toolbar on 'Refs ON (ref2va)'?

    Lives here rather than inline in the planner because the Director's lazy-input check
    has to answer the same question *before* the plan is built — the two must not drift,
    or the node would load one checkpoint and condition for the other.
    """
    return str(tdata.get("reference_mode", "OFF")).upper() != "OFF"


def retake_state(tdata):
    """The retake panel's state, or None when retake mode is off / has no base video."""
    if not tdata.get("retakeMode"):
        return None
    video = tdata.get("retakeVideo") or {}
    if not isinstance(video, dict):
        return None
    name = video.get("imageFile") or video.get("fileName")
    if not name:
        return None
    return {
        "video": name,
        "start": int(float(tdata.get("retakeStart", 0) or 0)),
        "length": max(1, int(float(tdata.get("retakeLength", 0) or 1))),
        "base_frames": int(float(video.get("videoDurationFrames", 0) or 0)),
        "prompt": tdata.get("retakePrompt", "") or "",
    }


FORMAT_MINIMAX = "minimax"
FORMAT_COMFYUI = "comfyui"


def fmt_timestamp(seconds):
    """MM:SS.mmm — the cut-time format MiniMax's prompt guide uses."""
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return "%02d:%06.3f" % (minutes, rest)


def retention_note(label, marker, kind=None, audio=False):
    """The `- …` half of a retention line, for when nothing was written by hand.

    The marker alone is the guide's fixed vocabulary; this is the sentence that says what
    the marker means *for this particular reference*, so the line carries something the
    model can act on instead of repeating the marker back at it.
    """
    if audio:
        return {
            "fully_copy": "%s is reused as the target video's complete final audio track.",
            "partially_copy": "part of %s is copied; other sounds are added, removed or "
                              "replaced.",
            "reference": "the target follows %s without copying the original signal.",
            "weak_reference": "only a broad similarity to %s in category and atmosphere "
                              "is kept.",
        }[marker] % label
    if marker == "fully_preserved":
        detail = RETAINED_DETAIL.get(kind or "")
        if detail:
            return "the %s of %s are retained." % (detail, label)
        return "%s is retained as defined." % label
    return {
        "partially_preserved": "%s is still used, with some of its defined characteristics "
                               "changed.",
        "attribute_transfer": "the characteristics of %s are transferred to a different "
                              "target subject.",
        "weak_reference": "only a broad similarity to %s in style, category, composition "
                          "and atmosphere is kept.",
    }[marker] % label


def picture_texts(ordinal, picture_role, ref_role, shot_no, at):
    """Every wording a standalone <Picture N> needs, derived once from the job it does.

    Four renderings, because the guide asks for the same fact in four places: the
    subject_definitions declaration (2.2), the retention_analysis parenthetical (4.1), the
    in-body phrasing (5.3), and — outside the guide entirely — the flat `Reference notes:`
    line the comfyui format keeps instead of all of the above.

    Deriving them together is the point: they described the same image in three different
    voices when the wording lived in three places, which is how issue #4 happened.
    """
    label = "<Picture %d>" % ordinal
    shot = "[Shot %d]" % shot_no if shot_no else ""

    if ref_role == REF_ROLE_STORYBOARD:
        target = shot or "the target video"
        return {
            "declaration": "%s is a storyboard reference for %s, defining its viewpoint, "
                           "subject placement, and shot order." % (label, target),
            "where": "storyboard reference for %s" % target,
            "phrase": "The shot follows the storyboard reference %s." % label if shot else "",
            "note": "%s is a storyboard reference for %s" % (label, target),
        }
    if picture_role == ROLE_FIRST:
        return {
            "declaration": "%s is the first frame of %s." % (label, shot or "the target video"),
            "where": ("%s first frame" % shot) if shot else "first frame",
            "phrase": ("The shot begins from %s." % label) if shot else "",
            "note": ("%s begins from %s" % (shot, label)) if shot
                    else "The video begins from %s" % label,
        }
    if picture_role == ROLE_LAST:
        return {
            "declaration": "%s is the last frame of %s." % (label, shot or "the target video"),
            "where": ("%s last frame" % shot) if shot else "last frame",
            "phrase": ("The shot ends on %s." % label) if shot else "",
            "note": ("%s ends on %s" % (shot, label)) if shot
                    else "The video ends on %s" % label,
        }
    if shot:
        return {
            "declaration": "%s is a keyframe of %s, at %s." % (label, shot, at),
            "where": "%s keyframe at %s" % (shot, at),
            "phrase": "The shot's keyframe corresponds to %s." % label,
            "note": "The keyframe of %s corresponds to %s, at %s" % (shot, label, at),
        }
    return {
        "declaration": "%s is a composition anchor at %s." % (label, at),
        "where": "composition anchor at %s" % at,
        "phrase": "",
        "note": "%s is a composition anchor at %s" % (label, at),
    }


def _subject_sentence(label, description, kind, pictures):
    """`<Subject 1> is a woman in a red coat, shown in <Picture 1>.`

    The guide's own shape is "<Subject 1> is the young woman in <Picture 1>, with long
    dark hair…" — description first, source second. A written description replaces the
    kind noun entirely, which is what makes the kind dropdown a fallback rather than a
    constraint.
    """
    text = (description or "").strip().rstrip(".")
    if text:
        return "%s is %s, shown in %s." % (label, text, pictures)
    return "%s is %s shown in %s." % (label, SUBJECT_KINDS[kind], pictures)


def _declaration(label, written, generated):
    """`<Label> is <what the user wrote>.`, or the generated sentence when nothing was.

    The label is prepended rather than expected in the text, so what gets typed is the
    predicate — "the voice-timbre reference for <Subject 1> (S1)" — and the label can
    never end up wrong or duplicated. A sentence that already opens with its own label is
    taken as written, since that is what someone pasting the guide's example will do.
    """
    text = (written or "").strip()
    if not text:
        return generated
    if text.startswith(label):
        return text if text.endswith((".", "!", "?")) else text + "."
    return "%s is %s." % (label, text.rstrip("."))


def build_subject_definitions(subject_slots, ref_image_slots, ref_video_segs,
                              ref_audio_segs):
    """Declare every reference label, and record what each needs in retention_analysis.

    The guide's central rule about images lives here: an image only earns a standalone
    <Picture N> entry when it *is* a frame — "a shot's first frame, keyframe, last frame,
    edited keyframe, or composition anchor" — or a storyboard reference. An image that
    merely defines a character, scene, costume or style is cited inside the corresponding
    <Subject N> definition instead. ComfyUI's tokenizer still labels every image
    <Picture i>, so this is about which labels get *declared*, not which exist.

    Returns (lines, subject_of_slot, labels, pic_texts). `labels` is the ledger
    build_retention_analysis walks; `pic_texts` is keyed by <Picture N> ordinal.
    """
    lines = []
    labels = []
    subject_of_slot = {}
    pic_texts = {}
    subject_no = 0

    # --- <Subject N>: the panel slots first, so a subject's number does not move when
    # something is dropped on the timeline
    for slot_index, slot in enumerate(subject_slots):
        ordinals = [i + 1 for i, s in enumerate(ref_image_slots)
                    if s.get("source") == "char" and s.get("slot") == slot_index]
        if not ordinals:
            continue
        subject_no += 1
        subject_of_slot[slot_index + 1] = subject_no
        label = "<Subject %d>" % subject_no
        lines.append(_subject_sentence(
            label, slot.get("description"), slot["kind"],
            " and ".join("<Picture %d>" % o for o in ordinals)))
        labels.append({"label": label, "marker": slot["retention"], "kind": slot["kind"],
                       "note": slot.get("note", ""), "where": "", "audio": False,
                       "subject": subject_no})

    # ...then any timeline image the user marked as defining something rather than
    # anchoring a frame. It gets a subject of its own and no picture entry.
    for i, s in enumerate(ref_image_slots):
        if s.get("source") != "timeline" or s.get("ref_role") != REF_ROLE_SUBJECT:
            continue
        subject_no += 1
        label = "<Subject %d>" % subject_no
        s["subject"] = subject_no
        # `desc` says what the image defines, `note` says what survives into the target —
        # two different sentences in two different sections, so they are two fields. One
        # field would mean a subject-defining image could carry one or the other and never
        # both, and would have to mean something else again on a frame anchor.
        lines.append(_subject_sentence(label, s.get("desc"), s["kind"],
                                       "<Picture %d>" % (i + 1)))
        labels.append({"label": label, "marker": s["retention"], "kind": s["kind"],
                       "note": s.get("note", ""), "where": "", "audio": False,
                       "subject": subject_no})

    # --- <Picture N>: only the real frame and storyboard anchors.
    # Character-slot images are deliberately absent — they are cited above instead. So are
    # images arriving on the ref_images socket: the node knows nothing about them beyond
    # the pixels, and a vacuous "is an additional reference image" line would cost the
    # model attention without telling it anything.
    for i, s in enumerate(ref_image_slots):
        if s.get("source") != "timeline" or s.get("ref_role") == REF_ROLE_SUBJECT:
            continue
        ordinal = i + 1
        texts = picture_texts(ordinal, s.get("picture_role"), s.get("ref_role"),
                              s.get("shot_no"), s.get("at"))
        pic_texts[ordinal] = texts
        lines.append(texts["declaration"])
        labels.append({"label": "<Picture %d>" % ordinal, "marker": s["retention"],
                       "kind": "storyboard" if s.get("ref_role") == REF_ROLE_STORYBOARD
                               else "picture",
                       "note": s.get("note", ""),
                       "where": texts["where"], "audio": False})

    # A <Video N> or <Audio N> declaration is pure prose — unlike a <Picture N>, it states
    # no structural fact the planner already knows — so a written one simply replaces it.
    # That is the only way to reach the guide's own shapes, which name things this node
    # cannot infer: "<Audio 1> is the voice-timbre reference for <Subject 1> (S1)", or
    # "<Video 1> is the source video for the target video edit".
    for i, seg in enumerate(ref_video_segs):
        label = "<Video %d>" % (i + 1)
        lines.append(_declaration(
            label, seg.get("refDesc"),
            "%s is a reference video: follow its motion and camera work." % label))
        labels.append({"label": label, "kind": "video", "audio": False,
                       "marker": sanitize_retention(seg.get("retention")),
                       "note": (seg.get("refNote") or "").strip(),
                       "where": "motion and camera work"})
    for i, seg in enumerate(ref_audio_segs):
        label = "<Audio %d>" % (i + 1)
        marker = sanitize_retention(seg.get("retention"), audio=True)
        # the generated declaration has to agree with the marker: telling the model to
        # follow a clip's timbre while retention_analysis says the signal is copied
        # wholesale describes two different jobs
        lines.append(_declaration(
            label, seg.get("refDesc"),
            "%s is a reference audio clip: %s"
            % (label, "its signal is reused in the target video."
               if marker in ("fully_copy", "partially_copy")
               else "follow its voice and timbre.")))
        labels.append({"label": label, "kind": "audio", "audio": True, "marker": marker,
                       "note": (seg.get("refNote") or "").strip(), "where": ""})

    return lines, subject_of_slot, labels, pic_texts


def build_retention_analysis(labels, subject_shots=None):
    """One line per reference label, in the guide's `label (where): marker - note` shape.

    A subject's parenthetical names the shots it actually appears in, read back off the
    shot text after tag substitution rather than assumed — a subject defined in the panel
    but never mentioned in any shot simply has no shot list to give.
    """
    subject_shots = subject_shots or {}
    lines = []
    for entry in labels:
        where = entry.get("where") or ""
        if entry.get("subject"):
            shots = subject_shots.get(entry["subject"]) or []
            if shots:
                where = "appears in " + ", ".join("[Shot %d]" % n for n in shots)
        note = (entry.get("note") or "").strip()
        if note:
            if not note.endswith((".", "!", "?")):
                note += "."
        else:
            note = retention_note(entry["label"], entry["marker"], entry.get("kind"),
                                  entry.get("audio", False))
        head = "%s (%s)" % (entry["label"], where) if where else entry["label"]
        lines.append("%s: %s - %s" % (head, entry["marker"], note))
    return lines


def task_type_prefix(ref_image_slots, ref_video_segs, ref_audio_segs, override=None):
    """The square-bracketed task type the guide puts at the head of `summary`.

    Derived from the jobs the references actually do, not from what happens to be
    connected: the guide is explicit that "the mere presence of video or audio does not
    automatically create a corresponding task type", and that a reference video supplying
    only camera movement, cuts or rhythm is reference generation, not video editing.

    `video editing` and `video continuation` are therefore never derived — this node has
    no path that edits or continues a source video. The override exists for the day one
    is added, and for anything else the timeline cannot know.
    """
    if (override or "").strip():
        return "[%s]" % override.strip().strip("[]").strip()

    types = set()
    for s in ref_image_slots:
        if s.get("source") == "timeline" and s.get("ref_role") == REF_ROLE_AUTO:
            types.add(TASK_KEYFRAME)
        else:
            types.add(TASK_REFERENCE)
    if ref_video_segs:
        types.add(TASK_REFERENCE)
    for seg in ref_audio_segs:
        types.add(TASK_AUDIO_REUSE
                  if sanitize_retention(seg.get("retention"), audio=True)
                  in ("fully_copy", "partially_copy")
                  else TASK_AUDIO_REFERENCE)

    ordered = [t for t in TASK_ORDER if t in types]
    return ("[%s]" % " + ".join(ordered)) if ordered else ""


def alignment_instruction(has_first, has_last, shot_count, seconds):
    """The image-alignment line the base guide requires as the very first line.

    Quoted verbatim from VIDEO_PROMPT_WRITING_GUIDE_base_en.md, including its own
    inconsistency: I2VA and L2VA bracket the tokens (`<Picture 1>`, `[Shot 1]`) while
    FL2VA does not. The model was trained on that text, so it is reproduced as written
    rather than tidied up.

    T2VA has no instruction, and the reference guide does not ask for one at all, so this
    only applies to the fl2va path.

    S.SS is the *effective* duration — after snapping to the 17k+5 grid, so a 5s request
    reports 5.16, not 5.00. It is floored to the hundredth rather than rounded, because
    rounding can name a mark that is past the end of the video: 124 frames last
    5.166667s, and "5.17" is outside the clip it is describing. Ten of the twenty-four
    valid frame counts up to 16s round that way (issue #6). The multiply-round-then-floor
    keeps binary noise from stealing a hundredth off an exact value like 12.25.
    """
    n = max(1, int(shot_count))
    s = "%.2f" % (int(round(max(0.0, float(seconds)) * 10000)) // 100 / 100.0)
    if has_first and has_last:
        return ("How the reference pictures align with the target video — Picture 1 "
                "(from Shot 1) aligns with the 0.00-second mark of the target video; "
                "Picture 2 (from Shot %d) aligns with the %s-second mark of the target "
                "video." % (n, s))
    if has_first:
        return ("For the target video, at 0.00 seconds into the target video, "
                "<Picture 1> (from [Shot 1]) is fully referenced.")
    if has_last:
        return ("How the reference pictures align with the target video — <Picture 1> "
                "(from [Shot %d]) aligns with the %s-second mark of the target video."
                % (n, s))
    return ""


def compile_storyboard_minimax(global_prompt, shots, soundscape="", music="",
                               subject_lines=None, retention_lines=None,
                               instruction="", summary=""):
    """The notation MiniMax documents in VIDEO_PROMPT_WRITING_GUIDE_*.md.

    `integrated_multimodal_description: [Shot 1] … [Shot 2] At 00:05.000, …`, with the
    first shot carrying no timestamp and every later cut carrying a strictly increasing
    one, followed by the soundscape fields. Sections are only emitted when there is
    something real to put in them — an empty heading is worse than none.

    Section order is the reference guide's: subject_definitions, summary,
    retention_analysis, detailed_description, then the two sound fields.

    subject_definitions and retention_analysis are written one entry per line, the way the
    guide's own example lays them out. Both are lists of records — one per reference label
    — rather than prose, and running them together into a paragraph makes it genuinely
    hard to see where one label's definition ends and the next begins. detailed_description
    stays a single flowing block, because that one really is prose.
    """
    parts = []

    # the guide: "must be the first line of the final prompt, followed by one blank line
    # before the core fields" — joining parts with a blank line gives exactly that
    if (instruction or "").strip():
        parts.append(instruction.strip())

    if subject_lines:
        parts.append("subject_definitions:\n" + "\n".join(subject_lines))
    if (summary or "").strip():
        parts.append("summary: " + summary.strip())
    if retention_lines:
        parts.append("retention_analysis:\n" + "\n".join(retention_lines))

    written = [s for s in shots if shot_has_text(s)]
    body = []
    gp = (global_prompt or "").strip()
    if gp:
        body.append(gp)
    for index, shot in enumerate(written):
        text = shot["prompt"].strip()
        # guide 5.3: a frame anchor is named in the shot itself, in natural phrasing, as
        # well as being declared above. It goes after the shot's own text: a later shot
        # opens "At 00:05.000, " and the sentence has to continue out of that comma, which
        # a capitalised "The shot begins from…" cannot do.
        phrases = " ".join(shot.get("ref_phrases") or [])
        if phrases:
            if text:
                if not text.endswith((".", "!", "?", ",", ";", ":")):
                    text += "."
                text = "%s %s" % (text, phrases)
            elif index:
                # nothing of the user's own to open on, and a later shot opens
                # "At 00:05.000, " — so our own sentence has to continue out of that comma
                text = phrases[0].lower() + phrases[1:]
            else:
                text = phrases
        # the guide's example ends a shot on its spoken line, after the action that
        # motivates it
        spoken = (shot.get("dialogue_text") or "").strip()
        if spoken:
            if text and not text.endswith((".", "!", "?")):
                text += "."
            text = ("%s %s" % (text, spoken)).strip()
        if index == 0:
            body.append("[Shot 1] %s" % text)
        else:
            body.append("[Shot %d] At %s, %s"
                        % (index + 1, fmt_timestamp(shot["start_sec"]), text))
    if body:
        parts.append(("detailed_description: " if subject_lines
                      else "integrated_multimodal_description: ") + " ".join(body))

    if (soundscape or "").strip():
        parts.append("overall_soundscape: " + soundscape.strip())
    if (music or "").strip():
        parts.append("non_diegetic_music: " + music.strip())

    return "\n\n".join(parts).strip()


SOUND_PREFIXES = ("audio:", "sound:", "soundscape:", "sfx:")
MUSIC_PREFIXES = ("music:", "score:", "soundtrack:")


def split_audio_music(text):
    """Lift `Audio: …` / `Music: …` lines out of a prompt into their own fields.

    Everyone writes the soundtrack into the prompt as an "Audio:" line — MiniMax's own
    ComfyUI templates do it too — while the guide wants it in overall_soundscape and
    non_diegetic_music. Only a line that *starts* with one of these labels counts, so
    prose that merely mentions sound is left where it is. Whatever follows such a line
    without its own label belongs to it.
    """
    if not text:
        return "", "", ""
    kept, sound, music = [], [], []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if any(low.startswith(p) for p in SOUND_PREFIXES):
            current = sound
            stripped = stripped.split(":", 1)[1].strip()
        elif any(low.startswith(p) for p in MUSIC_PREFIXES):
            current = music
            stripped = stripped.split(":", 1)[1].strip()
        elif not stripped:
            current = None            # a blank line ends the block
            kept.append(line)
            continue
        elif current is None:
            kept.append(line)
            continue
        if stripped:
            current.append(stripped)
    return "\n".join(kept).strip(), " ".join(sound).strip(), " ".join(music).strip()


# A dialogue line in a shot prompt. Same rule as split_audio_music: it only counts at the
# start of a line, so prose that merely contains an @ref tag or a colon is left alone.
#
#   @ref1 exclaims with light annoyance: Hey! Watch your dog!
#   @ref1 [French] murmure: Bonjour
#   @voice(a low male narrator) says: In the beginning...
#   @audio2: the chorus line carried by the track itself
#
# The clause between the tag and the colon is the delivery, kept verbatim — the guide's own
# example carries the performance there ("says in a casual young male voice with a playful
# tone"), and generating a flat "says" would throw that away.
DIALOGUE_RE = re.compile(
    r"^@(?:ref(?P<slot>\d+)|voice\((?P<voice>[^)]*)\)|audio(?P<audio>\d+))"
    r"\s*(?:\[(?P<lang>[^\]]+)\])?"
    r"\s*(?P<delivery>[^:]*):\s*(?P<line>.+)$", re.IGNORECASE)

DIALOGUE_DEFAULT_LANG = "English"
DIALOGUE_DEFAULT_DELIVERY = "says"

SPEAKER_ID_RE = re.compile(r"\(S\d+\)")

# "Generation tasks typically span 350-500 words", which the preview reports as a figure so
# the range is visible without being nagged about. The guide itself warns against
# "mechanical word-count adherence".
DESCRIPTION_MIN_WORDS = 350
DESCRIPTION_MAX_WORDS = 500


def split_dialogue(text):
    """Lift `@ref1 says: …` lines out of a shot prompt into structured vocal events.

    Returns (remaining prose, events). Speaker IDs are deliberately *not* assigned here:
    the guide numbers them "according to the order of actual vocal events in the target
    video", which is a property of the whole timeline, not of one shot.
    """
    if not text:
        return text or "", []
    kept, events = [], []
    for line in text.splitlines():
        match = DIALOGUE_RE.match(line.strip())
        if not match:
            kept.append(line)
            continue
        events.append({
            "slot": int(match.group("slot")) if match.group("slot") else None,
            "voice": (match.group("voice") or "").strip() or None,
            "audio": int(match.group("audio")) if match.group("audio") else None,
            "language": (match.group("lang") or DIALOGUE_DEFAULT_LANG).strip(),
            "delivery": (match.group("delivery") or "").strip() or DIALOGUE_DEFAULT_DELIVERY,
            "line": match.group("line").strip(),
        })
    return "\n".join(kept).strip(), events


def shot_has_text(shot):
    """Does this shot appear in the body, and therefore carry a [Shot N] number?

    A shot whose only content is dialogue still counts. Testing the prompt alone would
    drop it from the body once the dialogue was lifted out — losing the line and shifting
    every later shot number, including the ones picture notes point at.
    """
    return bool((shot.get("prompt") or "").strip() or shot.get("dialogue"))


def assign_speakers(shots, label_for_slot):
    """Number speakers across the timeline and render each line the way the guide asks.

    `(Sx)` is assigned once per speaker, in the order vocal events actually occur, and
    reused at every later event by that speaker — so the same person keeps one ID across
    cuts, exactly as <Subject N> does. Verbal content carried by reused audio names
    <Audio N> as its source and gets no ID at all: the guide is explicit that a cue inside
    a soundtrack has no independent vocal source to number.
    """
    ids = {}
    for shot in shots:
        rendered = []
        for event in shot.get("dialogue") or []:
            tag = "<d>[%s] %s</d>" % (event["language"], event["line"])
            if event["audio"]:
                rendered.append("<Audio %d> carries %s" % (event["audio"], tag))
                continue
            if event["slot"]:
                who = label_for_slot(event["slot"]) or "an unnamed speaker"
                key = ("slot", event["slot"])
            else:
                who = event["voice"] or "an unnamed speaker"
                key = ("voice", who.strip().lower())
            if key not in ids:
                ids[key] = len(ids) + 1
            rendered.append("%s (S%d) %s, %s" % (who, ids[key], event["delivery"], tag))
        shot["dialogue_text"] = " ".join(rendered)
    return ids


ANALYSIS_DESC_PREFIX = "description:"
ANALYSIS_KEEP_PREFIX = "retained:"


def split_analysis(text):
    """Split the Analyze button's two labelled lines into (description, retained).

    The vision model is asked for `DESCRIPTION:` and `RETAINED:`, which fill the slot's
    two boxes: what the subject *is*, and what has to survive into the target video.

    Local models ignore an output format often enough that the unlabelled case has to be
    the safe one — everything becomes the description, which is where a single blob of
    text was going before there was a second box. A label may also arrive lowercased, in
    bold, or with the model's own preamble above it, so matching is case-insensitive,
    tolerant of `*` and `#` decoration, and picks up wherever the first label appears.

    Lives here rather than in minimax_media so it can be tested without importing
    aiohttp or ComfyUI — the same reason split_audio_music is here.
    """
    if not text:
        return "", ""
    rows = []
    labelled = False
    for line in str(text).splitlines():
        stripped = line.strip().lstrip("*#-• ").strip()
        low = stripped.lower()
        if low.startswith(ANALYSIS_DESC_PREFIX):
            rows.append(("desc", stripped.split(":", 1)[1]))
            labelled = True
        elif low.startswith(ANALYSIS_KEEP_PREFIX):
            rows.append(("keep", stripped.split(":", 1)[1]))
            labelled = True
        else:
            rows.append((None, stripped))

    desc, keep, current = [], [], None
    for kind, value in rows:
        if kind:
            current = desc if kind == "desc" else keep
        elif current is None:
            # Before any label. With labels present this is the model's own preamble
            # ("Sure! Here is the analysis:") and belongs in neither box; with no labels
            # anywhere it is the whole answer, and the description is where a single blob
            # of text went before there was a second box.
            if labelled:
                continue
            current = desc
        value = value.strip().strip("*").strip()
        if value:
            current.append(value)
    return " ".join(desc).strip(), " ".join(keep).strip()


def compile_storyboard(global_prompt, shots, total_seconds):
    """Global block, then timed shot lines in the ComfyUI templates' `[0s-1.5s]` notation."""
    written = [s for s in shots if shot_has_text(s)]

    # Dialogue was lifted out of the prompt before this format ever saw it, so it has to be
    # put back or a spoken line would vanish from a comfyui-format prompt entirely.
    def body(shot):
        parts = [(shot["prompt"] or "").strip(), (shot.get("dialogue_text") or "").strip()]
        return " ".join(p for p in parts if p)

    parts = []
    gp = (global_prompt or "").strip()
    if gp:
        parts.append(gp)

    if len(written) == 1 and written[0]["start_sec"] <= 0.01 and \
            written[0]["end_sec"] >= total_seconds - 0.01:
        # One shot covering the whole window is just a prompt — wrapping it in
        # storyboard syntax would imply a cut that isn't there.
        parts.append(body(written[0]))
    elif written:
        parts.append("Timeline:\n" + "\n".join(
            "[%s-%s] %s" % (fmt_seconds(s["start_sec"]), fmt_seconds(s["end_sec"]), body(s))
            for s in written))

    return "\n\n".join(parts).strip()


def classify_events(events, duration_frames, fps):
    """Assign each main-track image/video segment its keyframe role.

    H3's PackedLayout only anchors keyframes at frame 0 and frame_count-1, so a timeline
    image is either the opening frame, the closing frame, or a reference.
    """
    head_window_f = max(1.0, fps * 0.5)
    have_first = have_last = False
    for ev in events:
        starts_at_head = ev["rel_start_f"] <= head_window_f
        reaches_tail = ev["rel_end_f"] >= duration_frames - 0.5

        # An explicit "end frame" flag always wins. Otherwise an image that merely runs to
        # the end of the window is only a closing frame if it did not also start at the
        # head — one clip spanning the whole timeline is the classic i2v case and means
        # "start from this image".
        if ev["is_end"] or (reaches_tail and not starts_at_head):
            if not have_last or ev["is_end"]:
                ev["role"] = ROLE_LAST
                have_last = True
                continue
        if starts_at_head and not have_first:
            ev["role"] = ROLE_FIRST
            have_first = True
            continue
        ev["role"] = ROLE_MIDDLE
    return events


def plan_timeline(tdata, win_start, duration_frames, fps, global_prompt="",
                  use_custom_motion=True, use_custom_audio=False, override_audio=False,
                  extra_ref_image_count=0, soundscape="", music="", prompt_format=None,
                  summary=""):
    """Work out shots, keyframe roles, reference ordinals and the final prompt.

    Returns a dict; `execute` uses it to decide what media to load, the endpoint just
    reads `prompt`.
    """
    win_end = win_start + duration_frames
    window_seconds = duration_frames / fps
    retake = retake_state(tdata)

    if not global_prompt:
        global_prompt = tdata.get(
            "retake_global_prompt" if retake else "global_prompt", "") or ""

    # The editor's two soundscape boxes live in the timeline, so both consumers read them
    # from the same place and no third copy can go stale. Unlike the global prompt these
    # have no retake twin on purpose: re-rolling part of a shot does not change what the
    # room sounds like. An explicit argument still wins, which is what lets a caller
    # override them without touching the timeline.
    if not (soundscape or "").strip():
        soundscape = tdata.get("overall_soundscape", "") or ""
    if not (music or "").strip():
        music = tdata.get("non_diegetic_music", "") or ""

    ref_mode_on = ref_mode_from(tdata)
    if prompt_format is None:
        prompt_format = str(tdata.get("prompt_format", FORMAT_MINIMAX)).lower()
    if prompt_format not in (FORMAT_MINIMAX, FORMAT_COMFYUI):
        prompt_format = FORMAT_MINIMAX

    # --- subject slots (metadata only) ---
    # `subjects` is what the editor writes now; `characters` is the same panel's old key
    # and is still read, because a slot holding a scene or a prop is the same structure
    # that used to hold only characters.
    subject_slots = []
    raw_slots = tdata.get("subjects")
    if not isinstance(raw_slots, list):
        raw_slots = tdata.get("characters", []) or []
    for info in raw_slots[:MAX_SUBJECT_SLOTS]:
        images_list = info.get("images", []) or []
        legacy_b64 = info.get("imageB64", "")
        if legacy_b64 and not images_list:
            images_list = [{"b64": legacy_b64, "name": info.get("fileName", "")}]
        subject_slots.append({
            "images": images_list,
            "description": info.get("description", "") or "",
            "kind": sanitize_kind(info.get("kind")),
            "retention": sanitize_retention(info.get("retention")),
            "note": info.get("retentionNote", "") or "",
        })

    # --- shots + image events ---
    shots, events = [], []
    if retake:
        # Retake replaces the timeline window: one shot over the marked range, anchored on
        # the base video's own frames either side of it.
        text = retake["prompt"].strip()
        if not text:
            for seg in sorted(tdata.get("segments", []) or [],
                              key=lambda s: float(s.get("start", 0))):
                if overlaps(seg, win_start, win_end) and (seg.get("prompt") or "").strip():
                    text = seg["prompt"]
                    break
        text, spoken = split_dialogue(text)
        shots.append({"prompt": text, "dialogue": spoken,
                      "start_sec": 0.0, "end_sec": window_seconds})
    else:
        segments = [s for s in (tdata.get("segments", []) or [])
                    if overlaps(s, win_start, win_end)]
        segments.sort(key=lambda s: float(s.get("start", 0)))
        for seg in segments:
            seg_start = float(seg.get("start", 0))
            seg_len = float(seg.get("length", 1))
            rel_start = max(0.0, seg_start - win_start)
            rel_end = min(float(duration_frames), seg_start + seg_len - win_start)
            # dialogue is lifted before anything else touches the text, so a shot
            # whose whole content is a spoken line still becomes a numbered shot
            prose, spoken = split_dialogue(seg.get("prompt", "") or "")
            shots.append({"prompt": prose, "dialogue": spoken,
                          "start_sec": rel_start / fps, "end_sec": rel_end / fps})

            kind = seg.get("type", "image")
            if kind not in ("image", "video"):
                continue
            if not (seg.get("imageFile") or seg.get("imageB64")):
                continue
            events.append({"seg": seg, "rel_start_f": rel_start, "rel_end_f": rel_end,
                           "is_end": bool(seg.get("isEndFrame")), "kind": kind,
                           "name": seg.get("fileName", "") or "", "role": ROLE_MIDDLE,
                           # which shot this image belongs to, so a picture note can name
                           # it the way the guide does. Resolved to the *written* shot
                           # numbering below — the body only numbers shots that carry text.
                           "shot_index": len(shots) - 1})
        classify_events(events, duration_frames, fps)

    # --- reference ordinals, in the order the tokenizer will present them ---
    char_tag_values = {}
    ref_notes = []
    ref_image_slots = []     # {"source": "char"/"input"/"timeline", ...} in <Picture i> order

    if ref_mode_on:
        for slot_idx, slot in enumerate(subject_slots):
            if not slot["images"] or len(ref_image_slots) >= MAX_REF_IMAGES:
                continue
            for img in slot["images"]:
                if len(ref_image_slots) >= MAX_REF_IMAGES:
                    break
                ref_image_slots.append({"source": "char", "slot": slot_idx, "image": img})

        for _ in range(max(0, int(extra_ref_image_count))):
            if len(ref_image_slots) >= MAX_REF_IMAGES:
                break
            ref_image_slots.append({"source": "input"})

        # Timeline images in the order they appear on the timeline. `events` is already
        # chronological, so <Picture n> counts up with time. Character slots and the
        # ref_images input stay ahead of all of them, so a character's number never shifts
        # when an image is dropped on the timeline.
        #
        # The wording is the reference guide's own, verbatim where it gives it:
        # "the shot begins from <Picture 1>", "the shot's keyframe corresponds to
        # <Picture 2>", "the shot ends on <Picture 3>". `<Picture N>` is precisely what
        # that guide asks for when an image "serves as a shot's first frame, keyframe,
        # last frame, edited keyframe, or composition anchor", so ref2va does have frame
        # anchors in its notation — what it does not have is FL2VA's vocabulary. Calling
        # these "opening frame" / "closing frame" borrowed from the wrong guide, and made
        # the phrasing depend on where a segment happened to end: flush with the window it
        # read as a closing frame, three frames shorter as something else entirely
        # (issue #4).
        #
        # Shots are named the way the body names them, which counts only shots carrying
        # text. An image whose own segment has no prompt gets the guide's shot-free
        # phrasing rather than a number the reader cannot find.
        #
        # Only what the wording will be *derived from* is recorded here. The sentences
        # themselves are built after the total-file cap has trimmed the list, so a
        # reference that did not survive never had a note to prune — the old code wrote
        # the notes first and then parsed the strings back apart to drop them again.
        #
        # `picture_role` is the timeline's own reading of the image (opening, closing or
        # in between); `ref_role` is what the user says the image is *for*. Only an
        # untouched `auto` image is a real keyframe: that flag is not about wording at all,
        # it picks which frame of a video segment becomes the reference and whether it is
        # fitted to the canvas. See minimax_director.py.
        written_shot_no = {}
        counted = 0
        for shot_i, shot in enumerate(shots):
            if shot_has_text(shot):
                counted += 1
                written_shot_no[shot_i] = counted

        for ev in events:
            if len(ref_image_slots) >= MAX_REF_IMAGES:
                break
            seg = ev["seg"]
            ref_role = sanitize_ref_role(seg.get("refRole"))
            slot = {"source": "timeline", "event": ev, "ref_role": ref_role,
                    "picture_role": ev["role"],
                    "kind": sanitize_kind(seg.get("refKind")),
                    "retention": sanitize_retention(seg.get("retention")),
                    "desc": (seg.get("refDesc") or "").strip(),
                    "note": (seg.get("refNote") or "").strip(),
                    "shot_no": written_shot_no.get(ev.get("shot_index")),
                    "at": fmt_seconds(ev["rel_start_f"] / fps)}
            if ref_role == REF_ROLE_AUTO and ev["role"] in (ROLE_FIRST, ROLE_LAST):
                slot["keyframe"] = ev["role"]
            ref_image_slots.append(slot)
    else:
        for slot_idx, slot in enumerate(subject_slots):
            if slot["description"]:
                char_tag_values[slot_idx + 1] = slot["description"]

    # --- reference video / audio tracks ---
    ref_video_segs, ref_audio_segs = [], []
    if ref_mode_on:
        if use_custom_motion:
            motion = [s for s in (tdata.get("motionSegments", []) or [])
                      if s.get("videoFile") and overlaps(s, win_start, win_end)]
            motion.sort(key=lambda s: float(s.get("start", 0)))
            ref_video_segs = motion[:MAX_REF_VIDEOS]
        if use_custom_audio:
            audio = [s for s in (tdata.get("audioSegments", []) or [])
                     if (s.get("audioFile") or s.get("audioB64"))
                     and overlaps(s, win_start, win_end)]
            audio.sort(key=lambda s: float(s.get("start", 0)))
            ref_audio_segs = audio[:MAX_REF_AUDIOS]

    # --- total file cap ---
    # The per-type caps are not the whole story: H3 also takes at most 12 reference files
    # across all types. Trim from the back so the earlier, more deliberate references win.
    ref_warnings = []
    total_files = len(ref_image_slots) + len(ref_video_segs) + len(ref_audio_segs)
    if total_files > MAX_REF_FILES:
        excess = total_files - MAX_REF_FILES
        for bucket, label in ((ref_audio_segs, "audio"), (ref_video_segs, "video"),
                              (ref_image_slots, "image")):
            while excess > 0 and bucket:
                bucket.pop()
                excess -= 1
        ref_warnings.append(
            "H3 takes at most %d reference files in total; %d were dropped."
            % (MAX_REF_FILES, total_files - MAX_REF_FILES))

    # reference videos: each 2-15s, and no more than 15s of them together
    if ref_video_segs:
        budget = REF_VIDEO_TOTAL_SEC
        kept = []
        for seg in ref_video_segs:
            seconds = max(0.0, float(seg.get("length", 0)) / fps)
            if seconds < REF_VIDEO_MIN_SEC:
                ref_warnings.append(
                    "Reference video '%s' is %.1fs; H3 wants 2-15s per clip."
                    % (seg.get("fileName") or seg.get("videoFile"), seconds))
            # what this clip would actually contribute, after the per-clip cap
            usable = min(seconds, REF_VIDEO_MAX_SEC)
            # the question is whether it FITS, not whether anything is left at all
            if usable > budget + 1e-6:
                ref_warnings.append(
                    "Reference videos exceed the %.0fs total budget; '%s' was dropped."
                    % (REF_VIDEO_TOTAL_SEC, seg.get("fileName") or seg.get("videoFile")))
                continue
            kept.append(seg)
            budget -= usable
        ref_video_segs = kept

    # --- output length ---
    # Computed before the prompt because the alignment instruction has to name the
    # *effective* duration, i.e. after snapping to the 17k+5 grid, not the window length.
    length = align_frame_count(max(5, int(round(window_seconds * MODEL_FPS))))
    actual_seconds = length / MODEL_FPS

    # --- reference labels ---
    # Built after the caps have done their trimming, so every declaration describes a
    # reference that is actually being sent.
    subject_lines, subject_of_slot, ref_labels, pic_texts = [], {}, [], {}
    if ref_mode_on:
        subject_lines, subject_of_slot, ref_labels, pic_texts = build_subject_definitions(
            subject_slots, ref_image_slots, ref_video_segs, ref_audio_segs)
        # the comfyui format has no sections to put any of this in, so it keeps the one
        # flat notes line — which is the same set of facts in the guide's older phrasing
        ref_notes = [pic_texts[o]["note"] for o in sorted(pic_texts)]

        # A slot whose pictures were all trimmed away must not keep pointing at an ordinal
        # that no longer exists; it falls back to its description, exactly as it would
        # with references off.
        char_tag_values = {}
        for slot_index, slot in enumerate(subject_slots):
            ordinals = [i + 1 for i, s in enumerate(ref_image_slots)
                        if s.get("source") == "char" and s.get("slot") == slot_index]
            if ordinals:
                char_tag_values[slot_index + 1] = "<Picture %d>" % ordinals[0]
            elif slot["description"]:
                char_tag_values[slot_index + 1] = slot["description"]

        if prompt_format == FORMAT_MINIMAX:
            # a named subject beats a bare picture label: it survives across cuts
            for slot, subject in subject_of_slot.items():
                char_tag_values[slot] = "<Subject %d>" % subject

    global_prompt = substitute_char_tags(global_prompt, char_tag_values)
    for shot in shots:
        shot["prompt"] = substitute_char_tags(shot["prompt"], char_tag_values)

    # Rendered here, after the tags resolve to their labels and before the shot text is
    # read back for `(appears in [Shot N])` — a subject that only ever speaks still
    # appears in the shots where it speaks.
    speaker_ids = assign_speakers(shots, lambda slot: char_tag_values.get(slot))

    # only the minimax format has a detailed_description to measure; the key exists either
    # way so every caller can read it without checking the format first
    description_words = 0

    if prompt_format == FORMAT_MINIMAX:
        # the guide keeps the soundtrack out of the shot description
        global_prompt, found_audio, found_music = split_audio_music(global_prompt)
        # A filled box wins, but the lifted line is then thrown away — and it may be work
        # the Enhance node's vision model just did. Say so rather than swallow it.
        for label, box, found in (("overall_soundscape", soundscape, found_audio),
                                  ("non_diegetic_music", music, found_music)):
            if (box or "").strip() and found:
                log.info("[MiniMaxDirector] %s came from the timeline box, so the %s line "
                         "in the prompt text was dropped.", label,
                         "Audio:" if label == "overall_soundscape" else "Music:")
        soundscape = (soundscape or "").strip() or found_audio
        music = (music or "").strip() or found_music

        # Which shots each subject actually turns up in, read back off the shot text now
        # that the tags have been substituted. Numbering matches the body, which counts
        # only shots carrying text.
        subject_shots = {}
        written_no = 0
        for shot in shots:
            if not shot_has_text(shot):
                continue
            written_no += 1
            # dialogue counts: a subject that only ever speaks still appears in that shot
            body = "%s %s" % (shot["prompt"] or "", shot.get("dialogue_text") or "")
            for entry in ref_labels:
                num = entry.get("subject")
                if num and entry["label"] in body:
                    subject_shots.setdefault(num, []).append(written_no)

        retention_lines = build_retention_analysis(ref_labels, subject_shots)
        # "Do not write (Sx) in retention_analysis" — the IDs belong to vocal events in
        # detailed_description. Only a hand-written note can put one here, so say so
        # rather than quietly editing what was typed.
        if any(SPEAKER_ID_RE.search(line) for line in retention_lines):
            ref_warnings.append(
                "A speaker ID (Sx) appears in retention_analysis; the guide reserves those "
                "for detailed_description.")

        # guide 5.3: name the frame anchor inside the shot as well as declaring it above
        for ordinal, texts in pic_texts.items():
            if not texts["phrase"]:
                continue
            shot_index = ref_image_slots[ordinal - 1]["event"].get("shot_index")
            if shot_index is not None and 0 <= shot_index < len(shots):
                shots[shot_index].setdefault("ref_phrases", []).append(texts["phrase"])

        summary_line = " ".join(x for x in (
            task_type_prefix(ref_image_slots, ref_video_segs, ref_audio_segs,
                             tdata.get("task_type_override")) if ref_mode_on else "",
            (summary or "").strip() or (tdata.get("summary", "") or "").strip(),
        ) if x)

        # Only the fl2va path: ref2va has no keyframe slot and its guide asks for no
        # instruction line. `written` mirrors the shot numbering the body will use.
        instruction = ""
        if not ref_mode_on:
            written_shots = len([s for s in shots if shot_has_text(s)])
            instruction = alignment_instruction(
                any(e["role"] == ROLE_FIRST for e in events),
                any(e["role"] == ROLE_LAST for e in events),
                written_shots, actual_seconds)
        prompt = compile_storyboard_minimax(global_prompt, shots, soundscape, music,
                                            subject_lines, retention_lines, instruction,
                                            summary_line)

        # Measured on detailed_description alone — the label blocks and the sound sections
        # are not what the guide is counting.
        #
        # Reported as a figure, not a warning. The guide's 350-500 is a lot of words for a
        # 5-15 second clip, so being under it is the ordinary state here rather than a
        # fault: warning about it fired on essentially every timeline, which is how a
        # warning area stops being read at all. Only overshooting the range is called out,
        # where the prompt really has outgrown what the guide describes.
        description_words = len(global_prompt.split()) + sum(
            len(("%s %s" % (s["prompt"] or "", s.get("dialogue_text") or "")).split())
            for s in shots if shot_has_text(s))
        if description_words > DESCRIPTION_MAX_WORDS:
            ref_warnings.append(
                "detailed_description is about %d words; the guide suggests %d-%d for "
                "generation tasks."
                % (description_words, DESCRIPTION_MIN_WORDS, DESCRIPTION_MAX_WORDS))
    else:
        prompt = compile_storyboard(global_prompt, shots, window_seconds)
        if ref_notes:
            prompt = (prompt + "\n\nReference notes: " + "; ".join(ref_notes) + ".").strip()
        if (soundscape or "").strip():
            prompt = (prompt + "\n\nAudio: " + soundscape.strip()).strip()
        if (music or "").strip():
            prompt = (prompt + "\n\nMusic: " + music.strip()).strip()

    fallback = not prompt
    if fallback:
        prompt = "video"

    has_keyframe = any(ev["role"] in (ROLE_FIRST, ROLE_LAST) for ev in events) or bool(retake)
    mode = "ref2va" if ref_mode_on else ("fl2va" if has_keyframe else "t2va")

    return {
        "prompt": prompt, "prompt_is_fallback": fallback,
        "shots": shots, "events": events, "retake": retake,
        "ref_mode_on": ref_mode_on, "mode": mode,
        "ref_image_slots": ref_image_slots, "ref_notes": ref_notes,
        "ref_video_segs": ref_video_segs, "ref_audio_segs": ref_audio_segs,
        "ref_warnings": ref_warnings, "prompt_format": prompt_format,
        "description_words": description_words,
        "char_tag_values": char_tag_values,
        "win_start": win_start, "duration_frames": duration_frames, "fps": fps,
        "window_seconds": window_seconds,
        "length": length, "actual_seconds": actual_seconds,
    }
