// In-node live preview for MiniMax H3 Preview Override.
// The Python side sends one animated WebP per sampled step over the "minimax_h3_preview"
// event; we drop it into a DOM widget on the node and show a small status line.

const { app } = window.comfyAPI.app;
const { api } = window.comfyAPI.api;

const NODE_TYPE = "MiniMaxH3PreviewOverrideCS";
const IDLE_TEXT = "waiting for sample…";

function buildPanel() {
  const root = document.createElement("div");
  Object.assign(root.style, {
    display: "flex", flexDirection: "column", gap: "4px",
    boxSizing: "border-box", width: "100%", height: "100%",
  });

  const frame = document.createElement("div");
  Object.assign(frame.style, {
    position: "relative", width: "100%", minHeight: "160px", flex: "1",
    background: "#141414", border: "1px solid #3a3a3a", borderRadius: "6px",
    display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden",
  });

  const img = document.createElement("img");
  Object.assign(img.style, {
    maxWidth: "100%", maxHeight: "100%", objectFit: "contain",
    display: "none", imageRendering: "auto",
  });

  const idle = document.createElement("div");
  idle.textContent = IDLE_TEXT;
  Object.assign(idle.style, { color: "#6a6a6a", fontSize: "11px", fontStyle: "italic" });

  frame.appendChild(img);
  frame.appendChild(idle);

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
  root.appendChild(status);
  return { root, img, idle, left, right };
}

app.registerExtension({
  name: "MiniMaxH3PreviewOverrideCS",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_TYPE) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      if (onNodeCreated) onNodeCreated.apply(this, arguments);

      const panel = buildPanel();
      this._mmxPreview = panel;

      // Height is declared through the DOM-widget layout API, not through computeSize.
      //
      // computeSize is the wrong hook here: LiteGraph evaluates it to get the node's
      // minimum height, so any height derived from node.size[1] ratchets — the minimum is
      // measured against the size you are trying to leave, and the node can only ever
      // grow. Core's multiline textarea sidesteps that by declaring a minHeight and no
      // maxHeight, which lets the layout engine hand it the leftover space instead. Same
      // deal here: one floor, no ceiling, resizable in both directions.
      const MIN_PREVIEW_H = 140;
      const widget = this.addDOMWidget("minimax_preview_ui", "minimax_preview_ui", panel.root, {
        getValue: () => "",
        setValue: () => {},
        getMinHeight: () => MIN_PREVIEW_H,
      });
      widget.serialize = false;

      if (this.size[0] < 340) this.size[0] = 340;
      if (this.size[1] < 520) this.size[1] = 520;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      if (this._mmxPreview?.img?.src?.startsWith("blob:")) {
        URL.revokeObjectURL(this._mmxPreview.img.src);
      }
      this._mmxPreview = null;
      return onRemoved?.apply(this, arguments);
    };
  },

  async setup() {
    api.addEventListener("minimax_h3_preview", (event) => {
      const d = event.detail || {};
      // node ids are strings server-side and numbers in the graph — compare loosely
      const node = app.graph?._nodes?.find((n) => String(n.id) === String(d.node_id));
      const panel = node?._mmxPreview;
      if (!panel || !d.webp) return;

      panel.img.src = "data:image/webp;base64," + d.webp;
      panel.img.style.display = "block";
      panel.idle.style.display = "none";
      const rate = Number(d.fps);
      const src = Number(d.source_fps);
      // A rate below the shot's own is not a fault, it is the "true speed" trade: the
      // sampled frames are spread over the clip's real length. Say which of the two you
      // are looking at, or the number reads as a setting being ignored.
      const slowed = Number.isFinite(src) && Math.abs(src - rate) > 0.05;
      const fpsText = slowed
        ? `${rate.toFixed(1)}fps of ${src.toFixed(0)} · true speed`
        : `${rate.toFixed(rate % 1 ? 1 : 0)}fps${d.playback === "source fps" ? " · source" : ""}`;
      panel.left.textContent = `step ${d.step}/${d.total_steps} · ${d.frames}f @${fpsText}`;
      // server-side cost of building this preview, not anything the browser spent
      const cost = Number(d.ms) >= 1000 ? `${(d.ms / 1000).toFixed(1)}s` : `${d.ms}ms`;
      panel.right.textContent = `render ${cost} · ${d.mode ? d.mode.split(" ")[0] : ""}`;
      node.setDirtyCanvas?.(true, false);
    });

    // reset the panels back to idle when a new run starts
    api.addEventListener("execution_start", () => {
      for (const n of app.graph?._nodes || []) {
        const p = n._mmxPreview;
        if (!p) continue;
        p.left.textContent = "waiting…";
        p.right.textContent = "";
      }
    });
  },
});
