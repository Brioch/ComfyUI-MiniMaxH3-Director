// In-node live audio preview for MiniMax H3 Audio Preview.
//
// The Python side decodes the audio half of the latent while sampling and announces each
// clip over the "minimax_h3_audio_preview" event: a URL to fetch it from, plus a peak
// envelope. The envelope is drawn every time; the clip plays only once you have asked for
// sound, because browsers refuse to play audio on a page that has not been interacted with —
// and because a queued render should not suddenly start talking.

const { app } = window.comfyAPI.app;
const { api } = window.comfyAPI.api;

const NODE_TYPE = "MiniMaxH3AudioPreviewCS";
const IDLE_TEXT = "waiting for sample…";
const SOUND_ON = "🔊 sound on";
const SOUND_OFF = "🔇 sound off";
const SOUND_BLOCKED = "🔇 click for sound";
const SOUND_BROKEN = "⚠ clip unplayable";

// The clip is fetched over HTTP, not embedded in the page.
//
// Putting the audio *in* the document failed both ways available: a data: URI came back as
// NotSupportedError "media resource not suitable", a blob: URL as "failed to open channel" —
// for bytes that are a valid WAV either way. That is a CSP or a browser extension refusing
// media schemes, and no re-encoding fixes it. The Python side serves the clip from a route on
// ComfyUI's own server instead, so this is an ordinary same-origin GET.
function clipURL(url) {
  // fileURL applies ComfyUI's base path (it may be served under a subdirectory) without the
  // /api prefix, which is where custom-node routes live.
  return api.fileURL ? api.fileURL(url) : url;
}

function drawEnvelope(canvas, envelope) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#141414";
  ctx.fillRect(0, 0, w, h);
  if (!Array.isArray(envelope) || envelope.length === 0) return;

  // One lane per channel: left above, right below, each mirrored around its own mid-line.
  const rows = envelope.slice(0, 2);
  const rowH = h / rows.length;
  rows.forEach((row, r) => {
    if (!Array.isArray(row) || row.length === 0) return;
    const mid = rowH * r + rowH / 2;
    ctx.strokeStyle = "#2f2f2f";
    ctx.beginPath();
    ctx.moveTo(0, mid);
    ctx.lineTo(w, mid);
    ctx.stroke();
    ctx.fillStyle = r === 0 ? "#6ba7d8" : "#8fd86b";
    const bw = w / row.length;
    for (let i = 0; i < row.length; i++) {
      const peak = Math.max(0, Math.min(1, Number(row[i]) || 0));
      const half = Math.max(0.5, (peak * rowH * 0.9) / 2);
      ctx.fillRect(i * bw, mid - half, Math.max(1, bw - 0.5), half * 2);
    }
  });
}

function buildPanel() {
  const root = document.createElement("div");
  Object.assign(root.style, {
    display: "flex", flexDirection: "column", gap: "4px",
    boxSizing: "border-box", width: "100%", height: "100%",
  });

  const frame = document.createElement("div");
  Object.assign(frame.style, {
    position: "relative", width: "100%", minHeight: "56px", flex: "1",
    background: "#141414", border: "1px solid #3a3a3a", borderRadius: "6px",
    display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden",
  });

  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 128;
  Object.assign(canvas.style, {
    width: "100%", height: "100%", display: "none",
  });

  const idle = document.createElement("div");
  idle.textContent = IDLE_TEXT;
  Object.assign(idle.style, { color: "#6a6a6a", fontSize: "11px", fontStyle: "italic" });

  frame.appendChild(canvas);
  frame.appendChild(idle);

  const controls = document.createElement("div");
  Object.assign(controls.style, { display: "flex", alignItems: "center", gap: "6px" });

  // A style object, not a copy of the first button's CSSStyleDeclaration — Object.assign
  // over one of those copies indexed properties, not the declarations.
  const buttonStyle = {
    fontSize: "10px", fontFamily: "monospace", padding: "3px 6px", cursor: "pointer",
    background: "#2a2a2a", color: "#c8c8c8", border: "1px solid #3a3a3a",
    borderRadius: "4px", whiteSpace: "nowrap",
  };

  const soundBtn = document.createElement("button");
  soundBtn.textContent = SOUND_OFF;
  soundBtn.title = "Play each decoded clip as it arrives";
  Object.assign(soundBtn.style, buttonStyle);

  const replayBtn = document.createElement("button");
  replayBtn.textContent = "↻ replay";
  replayBtn.title = "Play the last clip again";
  Object.assign(replayBtn.style, buttonStyle);

  const audio = document.createElement("audio");
  audio.preload = "auto";

  controls.appendChild(soundBtn);
  controls.appendChild(replayBtn);

  const status = document.createElement("div");
  Object.assign(status.style, {
    display: "flex", justifyContent: "space-between", gap: "8px",
    color: "#8a8a8a", fontSize: "10px", fontFamily: "monospace", padding: "0 2px",
  });
  const left = document.createElement("span");
  const right = document.createElement("span");
  left.textContent = "idle";
  status.appendChild(left);
  status.appendChild(right);

  root.appendChild(frame);
  root.appendChild(controls);
  root.appendChild(status);

  // wantSound starts false: the strip is drawn, nothing is played. A queued render should
  // not suddenly start talking, and the first click is also the gesture that lets it.
  const panel = { root, canvas, idle, left, right, soundBtn, replayBtn, audio,
                  wantSound: false, blocked: false, clipInfo: "" };

  // Two very different failures reach the same catch, and telling them apart is the whole
  // difference between a button that lies and one that helps:
  //   NotAllowedError  — the autoplay policy wants a click. Clicking fixes it.
  //   NotSupportedError — the browser cannot decode the bytes we sent. Clicking never will;
  //                       the fault is server-side, so say so and log it once.
  const failed = (err) => {
    if (err && err.name === "NotAllowedError") {
      panel.blocked = true;
      soundBtn.textContent = SOUND_BLOCKED;
      return;
    }
    panel.blocked = false;
    soundBtn.textContent = SOUND_BROKEN;
    soundBtn.title = "The browser refused this clip — see the console for what it said";
    // Report the clip's type, size and URL — enough to tell a refused format from a route
    // that answered 404.
    console.warn(`[MiniMaxDirector] audio preview: the browser refused the clip ` +
                 `(${panel.clipInfo}): ${err && (err.name || "MediaError")}: ` +
                 `${err && err.message}`);
  };

  const play = () => {
    if (!audio.src) return;
    audio.currentTime = 0;
    audio.play().then(() => {
      panel.blocked = false;
      soundBtn.title = "Play each decoded clip as it arrives";
      if (panel.wantSound) soundBtn.textContent = SOUND_ON;
    }).catch(failed);
  };

  // play() only rejects if it is called; a clip that fails to load on its own would
  // otherwise go unreported until the next click.
  audio.addEventListener("error", () => {
    if (audio.src) failed(audio.error || { name: "MediaError", message: "load failed" });
  });

  soundBtn.addEventListener("click", () => {
    if (panel.blocked && panel.wantSound) {
      // The button is asking for a click, so honour it literally: retry the clip. Toggling
      // here instead would turn sound off — the opposite of what the label just promised.
      panel.blocked = false;
      soundBtn.textContent = SOUND_ON;
      play();
      return;
    }
    panel.wantSound = !panel.wantSound;
    panel.blocked = false;
    soundBtn.textContent = panel.wantSound ? SOUND_ON : SOUND_OFF;
    // The click itself is the gesture that unlocks playback, so start here rather than
    // making you wait for the next step's clip.
    if (panel.wantSound) play(); else audio.pause();
  });
  replayBtn.addEventListener("click", play);
  panel.play = play;

  return panel;
}

app.registerExtension({
  name: "MiniMaxH3AudioPreviewCS",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_TYPE) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      if (onNodeCreated) onNodeCreated.apply(this, arguments);

      const panel = buildPanel();
      this._mmxAudio = panel;

      // One floor, no ceiling — the same reasoning as the video preview's DOM widget:
      // deriving a height from node.size[1] in computeSize() ratchets the node upwards.
      const MIN_PANEL_H = 96;
      const widget = this.addDOMWidget("minimax_audio_ui", "minimax_audio_ui", panel.root, {
        getValue: () => "",
        setValue: () => {},
        getMinHeight: () => MIN_PANEL_H,
      });
      widget.serialize = false;

      if (this.size[0] < 300) this.size[0] = 300;
      if (this.size[1] < 300) this.size[1] = 300;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      // A detached <audio> keeps playing, so silence it before dropping the panel — and
      // release the blob, which the browser holds until told otherwise.
      if (this._mmxAudio?.audio) {
        this._mmxAudio.audio.pause();
        this._mmxAudio.audio.removeAttribute("src");
      }
      this._mmxAudio = null;
      return onRemoved?.apply(this, arguments);
    };
  },

  async setup() {
    api.addEventListener("minimax_h3_audio_preview", (event) => {
      const d = event.detail || {};
      // node ids are strings server-side and numbers in the graph — compare loosely
      const node = app.graph?._nodes?.find((n) => String(n.id) === String(d.node_id));
      const panel = node?._mmxAudio;
      if (!panel || !d.url) return;

      drawEnvelope(panel.canvas, d.envelope);
      panel.canvas.style.display = "block";
      panel.idle.style.display = "none";
      // A new clip may be in a format the last one was not — the encoder falls back to WAV
      // when its MP3 comes out wrong — so give it a clean slate rather than leaving the
      // previous failure on the button.
      if (panel.soundBtn.textContent === SOUND_BROKEN) {
        panel.soundBtn.textContent = panel.wantSound ? SOUND_ON : SOUND_OFF;
      }
      panel.clipInfo = `${d.audio_mime || "audio/wav"}, ${d.kb} KB, ${clipURL(d.url)}`;
      panel.audio.src = clipURL(d.url);
      if (panel.wantSound) panel.play();

      const secs = Number(d.seconds);
      panel.left.textContent =
        `step ${d.step}/${d.total_steps} · ${Number.isFinite(secs) ? secs.toFixed(1) : "?"}s`;
      // server-side cost of decoding and encoding this clip, not anything the browser spent
      const cost = Number(d.ms) >= 1000 ? `${(d.ms / 1000).toFixed(1)}s` : `${d.ms}ms`;
      panel.right.textContent = `decode ${cost}`;
      node.setDirtyCanvas?.(true, false);
    });

    // Last run's sound belongs to last run's shot. The sound-on choice is yours and stays.
    api.addEventListener("execution_start", () => {
      for (const n of app.graph?._nodes || []) {
        const p = n._mmxAudio;
        if (!p) continue;
        p.audio.pause();
        p.audio.removeAttribute("src");
        p.canvas.style.display = "none";
        p.idle.style.display = "block";
        drawEnvelope(p.canvas, null);
        p.left.textContent = "waiting…";
        p.right.textContent = "";
      }
    });
  },
});
