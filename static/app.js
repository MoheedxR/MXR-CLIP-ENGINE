/* MXR CLIP ENGINE: front end. Vanilla JS, no build step. */
(() => {
  "use strict";

  // ---------- helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const TERMINAL = new Set(["done", "failed"]);
  const STAGE_INDEX = { queued: -1, downloading: 0, audio: 0, transcribing: 1, scoring: 2, ranking: 2, clipping: 3, done: 4, failed: -1 };
  const STATUS_LABEL = { queued: "Queued", running: "Working", done: "Ready", failed: "Failed" };
  const LABEL_HEX = { yellow: "#FFD400", cyan: "#22D3EE", pink: "#FF4D8D", lime: "#A3E635", violet: "#8B7BFF", grey: "#8891A6" };
  const RATING_CLASS = { Weak: "weak", Fair: "fair", Good: "good", Excellent: "excellent" };

  const fmtClock = (sec) => {
    sec = Math.max(0, Math.round(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? `${h}:` : "") + `${mm}:${String(s).padStart(2, "0")}`;
  };
  const fmtSpan = (sec) => {
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  };
  const fmtAgo = (ts) => {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  };
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const initials = (t) => {
    const w = String(t || "?").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
    return ((w[0]?.[0] || "?") + (w[1]?.[0] || "")).toUpperCase();
  };
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "Video link"; } };
  const titleOf = (j) => j.source_title || j.upload_name || (j.source_url ? hostOf(j.source_url) : "Untitled project");
  const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const pct = (v) => (v == null ? "" : `${Math.round(v)}%`);
  const mediaUrl = (p) => (p ? `/media/${p}` : "");

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = `Request failed (${res.status})`;
      try {
        const body = await res.json();
        if (typeof body.detail === "string") msg = body.detail;
        else if (Array.isArray(body.detail)) msg = body.detail.map((d) => String(d.msg).replace(/^Value error, /, "")).join(" ");
      } catch { /* not JSON */ }
      throw new Error(msg);
    }
    return res.status === 204 ? null : res.json();
  }
  const sendJson = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ---------- state ----------
  const state = {
    config: null,
    route: { view: "home" },
    form: { platform: "tiktok", quality: "quick", style: "highlight", position: "bottom", color: "yellow",
            qualityMode: "default", resolution: 1080, fpsMode: "default", fps: 30,
            aspectMode: "default", aspect: "9:16", downloadLimit: "full" },
    busy: false,
    jobs: [],
    selectedId: null,
    job: null,
    logCount: 0,
    headerKey: "",
    alertKey: "",
    clipEls: new Map(),
    clipData: new Map(),
    clipSort: "best",
    detailLoadedFor: null,
    collections: [],
    collection: null,
    picker: null,
  };

  // ---------- engine info and option groups ----------
  async function loadConfig() {
    try {
      const c = await api("/api/config");
      state.config = c;
      document.title = c.app_name || "MXR CLIP ENGINE";
      $("#engine-pill").classList.toggle("off", !c.llm_enabled);
      $("#engine-text").innerHTML = `${c.llm_enabled ? "AI ranking on" : "Local ranking"} · Whisper <b>${esc(c.whisper_model)}</b>`;
      if (!c.llm_enabled) {
        const ai = $("#use_llm");
        ai.checked = false;
        ai.disabled = true;
        $("#ai-row").classList.add("disabled");
        $("#ai-sub").textContent = "Add an LLM key in .env to enable";
      }
      renderOptionGroups();
    } catch {
      $("#engine-text").textContent = "Engine offline";
      $("#engine-pill").classList.add("off");
    }
  }

  function choiceButtons(items, value, cls = "") {
    return items.map((it) => `<button type="button" class="choice ${cls}" role="radio" aria-checked="${it.id === value}" data-id="${esc(it.id)}"><span class="ch-t">${esc(it.label)}</span></button>`).join("");
  }

  function swatchButtons(ids, value, hexFor) {
    return ids.map((id) => `<button type="button" class="swatch" role="radio" aria-checked="${id === value}" data-id="${esc(id)}" style="--sw:${hexFor(id)}" aria-label="${esc(id)}" title="${esc(id)}"></button>`).join("");
  }

  function renderOptionGroups() {
    const c = state.config;
    if (!c) return;
    $("#quality-group").innerHTML = choiceButtons(c.qualities, state.form.quality, "q-choice");
    $("#platform-group").innerHTML = choiceButtons(c.platforms, state.form.platform, "p-choice");
    $("#download-group").innerHTML = choiceButtons(c.download_limits, state.form.downloadLimit, "dl-choice");
    syncDownloadHint();
    $("#style-group").innerHTML = c.caption_styles.map((s) => `<button type="button" class="style-card st-${esc(s.id)}" role="radio" aria-checked="${s.id === state.form.style}" data-id="${esc(s.id)}" title="${esc(s.hint)}">
        <span class="sc-demo"><span class="sc-w1">Hook</span> <span class="sc-w2">it fast</span></span>
        <span class="sc-name">${esc(s.label)}</span></button>`).join("");
    $("#position-group").innerHTML = c.caption_positions.map((p) => `<button type="button" class="choice" role="radio" aria-checked="${p === state.form.position}" data-id="${esc(p)}"><span class="ch-t">${esc(p[0].toUpperCase() + p.slice(1))}</span></button>`).join("");
    $("#color-group").innerHTML = swatchButtons(c.caption_colors.map((x) => x.id), state.form.color, (id) => LABEL_HEX[id] || c.caption_colors.find((x) => x.id === id).hex);
    renderOutputGroups(c);
    $("#coll-new-colors").innerHTML = swatchButtons(c.label_colors, "", (id) => LABEL_HEX[id]);
    $("#coll-new-status").innerHTML = c.collection_statuses.map((s) => `<option>${esc(s)}</option>`).join("");
    $("#coll-new-status").value = "Draft";
    syncFormHints();
  }

  function syncFormHints() {
    const c = state.config;
    if (!c) return;
    const q = c.qualities.find((x) => x.id === state.form.quality);
    const p = c.platforms.find((x) => x.id === state.form.platform);
    $("#platform-hint").textContent = p ? p.hint : "";
    syncOutputUI();
    const style = state.form.style;
    const color = state.form.color;
    $("#style-group").style.setProperty("--cap", LABEL_HEX[color] || "#FFD400");
    $("#color-group").style.setProperty("--cap", LABEL_HEX[color] || "#FFD400");
  }

  // ---------- custom output: quality, frame rate, aspect ratio ----------
  const SEG_KEYS = { quality: "qualityMode", fps: "fpsMode", aspect: "aspectMode" };
  function parseAspectText(text) {
    const m = /^\s*(\d{1,2})\s*[:/x]\s*(\d{1,2})\s*$/.exec(text || "");
    if (!m) return { error: "Use width:height, for example 4:5" };
    const w = +m[1], h = +m[2];
    if (!w || !h) return { error: "Both sides must be at least 1" };
    if (w / h < 0.4 || w / h > 2.5) return { error: "Keep the ratio between 2:5 and 5:2" };
    return { value: `${w}:${h}` };
  }
  function renderOutputGroups(c) {
    $("#resolution-group").innerHTML = c.resolutions.map((r) => `<button type="button" class="choice" role="radio" aria-checked="${r.id === state.form.resolution}" data-id="${r.id}"><span class="ch-t">${esc(r.label)}</span></button>`).join("");
    $("#fps-group").innerHTML = c.fps_options.map((f) => `<button type="button" class="choice" role="radio" aria-checked="${f === state.form.fps}" data-id="${f}"><span class="ch-t">${f} fps</span></button>`).join("");
    $("#aspect-group").innerHTML = c.aspect_presets.map((a) => `<button type="button" class="choice" role="radio" aria-checked="${a === state.form.aspect}" data-id="${esc(a)}"><span class="ch-t">${esc(a)}</span></button>`).join("");
    $("#aspect-input").value = state.form.aspect;
    syncOutputUI();
  }
  function syncOutputUI() {
    const f = state.form;
    const qCustom = f.qualityMode === "custom";
    const fCustom = f.fpsMode === "custom";
    const aCustom = f.aspectMode === "custom";
    const q = state.config?.qualities.find((x) => x.id === f.quality);
    $("#quality-group").hidden = qCustom;
    $("#resolution-group").hidden = !qCustom;
    $("#quality-hint").textContent = qCustom
      ? "The short side of the picture, in pixels. Custom quality also exports an MP3 of each clip."
      : (q ? q.hint : "");
    $("#fps-group").hidden = !fCustom;
    $("#fps-default-hint").hidden = fCustom;
    $("#fps-default-hint").textContent = `Follows the quality preset: ${qCustom ? 30 : (q ? q.fps : 30)} fps.`;
    $("#aspect-custom").hidden = !aCustom;
    $("#aspect-default-hint").hidden = aCustom;
    $("#aspect-default-hint").textContent = "9:16 for every platform.";
    $("#platform-sub").textContent = aCustom ? "· custom aspect" : "· all 9:16";
    if (aCustom) {
      const r = parseAspectText($("#aspect-input").value);
      $("#aspect-hint").textContent = r.value ? `Clips are cropped to ${r.value} from the centre.` : r.error;
    }
  }
  function markChoice(groupSel, id) {
    $$(`${groupSel} [data-id]`).forEach((x) => x.setAttribute("aria-checked", String(x.dataset.id === String(id))));
  }

  function pickGroup(groupId, key, value) {
    state.form[key] = value;
    $$(`#${groupId} [data-id]`).forEach((b) => b.setAttribute("aria-checked", String(b.dataset.id === value)));
    syncFormHints();
  }

  $("#quality-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]"); if (b) pickGroup("quality-group", "quality", b.dataset.id);
  });
  $$(".seg[data-setting]").forEach((seg) => seg.addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (!b) return;
    state.form[SEG_KEYS[seg.dataset.setting]] = b.dataset.mode;
    $$(`.seg[data-setting="${seg.dataset.setting}"] [data-mode]`).forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    syncOutputUI();
  }));
  $("#resolution-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (!b) return;
    state.form.resolution = +b.dataset.id;
    markChoice("#resolution-group", b.dataset.id);
  });
  $("#fps-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (!b) return;
    state.form.fps = +b.dataset.id;
    markChoice("#fps-group", b.dataset.id);
  });
  $("#aspect-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (!b) return;
    state.form.aspect = b.dataset.id;
    $("#aspect-input").value = b.dataset.id;
    markChoice("#aspect-group", b.dataset.id);
    syncOutputUI();
  });
  $("#aspect-input").addEventListener("input", (e) => {
    const r = parseAspectText(e.target.value);
    if (r.value) state.form.aspect = r.value;
    markChoice("#aspect-group", r.value || "");
    syncOutputUI();
  });

  $("#platform-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]"); if (b) pickGroup("platform-group", "platform", b.dataset.id);
  });
  $("#download-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (b) { pickGroup("download-group", "downloadLimit", b.dataset.id); syncDownloadHint(); }
  });
  function syncDownloadHint() {
    const item = (state.config?.download_limits || []).find((x) => x.id === state.form.downloadLimit);
    $("#download-hint").textContent = (item ? item.hint + ". " : "") + "Applies when the video is downloaded. It cannot change a video that is already downloaded.";
  }
  $("#style-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]"); if (b) pickGroup("style-group", "style", b.dataset.id);
  });
  $("#position-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]"); if (b) pickGroup("position-group", "position", b.dataset.id);
  });
  $("#color-group").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]"); if (b) pickGroup("color-group", "color", b.dataset.id);
  });
  $("#coll-new-colors").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]"); if (!b) return;
    const was = b.getAttribute("aria-checked") === "true";
    $$("#coll-new-colors [data-id]").forEach((x) => x.setAttribute("aria-checked", "false"));
    b.setAttribute("aria-checked", String(!was));
  });

  $("#captions").addEventListener("change", (e) => { $("#caption-panel").hidden = !e.target.checked; });

  // ---------- form ----------
  function setTab(name) {
    const isLink = name === "link";
    $("#tab-link").setAttribute("aria-selected", String(isLink));
    $("#tab-file").setAttribute("aria-selected", String(!isLink));
    $("#panel-link").hidden = !isLink;
    $("#panel-file").hidden = isLink;
  }
  $$("[data-tab]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  $$(".pick[data-min]").forEach((b) => b.addEventListener("click", () => {
    $$(".pick[data-min]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  }));

  const fileInput = $("#file");
  function showFile(file) {
    $("#dz-empty").hidden = !!file;
    $("#dz-file").hidden = !file;
    if (!file) { fileInput.value = ""; return; }
    $("#dz-name").textContent = file.name;
    $("#dz-name").title = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
  }
  fileInput.addEventListener("change", () => showFile(fileInput.files[0] || null));
  $("#dz-clear").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); showFile(null); });
  const dz = $("#dropzone");
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (!f) return;
    const dt = new DataTransfer();
    dt.items.add(f);
    fileInput.files = dt.files;
    showFile(f);
  });

  function setBusy(on) {
    state.busy = on;
    const btn = $("#submit");
    btn.disabled = on;
    $("#submit-label").textContent = on ? "Starting…" : "Create clips";
    $("#submit-arrow").hidden = on;
    btn.querySelector(".spinner")?.remove();
    if (on) btn.insertAdjacentHTML("afterbegin", '<span class="spinner" aria-hidden="true"></span>');
  }

  function formValues() {
    const pick = $(".pick[aria-pressed='true']") || $(".pick[data-min]");
    return {
      count: +$("#count").value,
      min_seconds: +pick.dataset.min,
      max_seconds: +pick.dataset.max,
      platform: state.form.platform,
      quality: state.form.quality,
      download_limit: state.form.downloadLimit,
      quality_mode: state.form.qualityMode,
      resolution: state.form.resolution,
      fps_mode: state.form.fpsMode,
      fps: state.form.fps,
      aspect_mode: state.form.aspectMode,
      aspect: state.form.aspect,
      captions: $("#captions").checked,
      caption_style: state.form.style,
      caption_position: state.form.position,
      caption_uppercase: $("#caption_uppercase").checked,
      caption_color: state.form.color,
      use_llm: $("#use_llm").checked && !$("#use_llm").disabled,
    };
  }

  $("#create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.busy) return;
    if (state.form.aspectMode === "custom") {
      const r = parseAspectText($("#aspect-input").value);
      if (!r.value) { toast(`Aspect ratio: ${r.error}`, "err"); $("#aspect-input").focus(); return; }
      state.form.aspect = r.value;
    }
    const isLink = $("#tab-link").getAttribute("aria-selected") === "true";
    const common = formValues();

    setBusy(true);
    try {
      let job;
      if (isLink) {
        const url = $("#url").value.trim();
        if (!/^https?:\/\/\S+\.\S+/i.test(url)) {
          toast("Paste a full video link, starting with https://", "err");
          $("#url").focus();
          return;
        }
        job = await api("/api/jobs", sendJson("POST", { url, ...common }));
        $("#url").value = "";
      } else {
        const file = fileInput.files[0];
        if (!file) { toast("Choose a video or audio file first.", "err"); return; }
        const fd = new FormData();
        fd.append("file", file);
        for (const [k, v] of Object.entries(common)) fd.append(k, String(v));
        job = await api("/api/jobs/upload", { method: "POST", body: fd });
        showFile(null);
      }
      toast("Project started. Watch the activity log for live progress.", "ok");
      await refreshList();
      location.hash = `#job/${job.id}`;
    } catch (err) {
      toast(err.message || "Something went wrong.", "err");
    } finally {
      setBusy(false);
    }
  });

  // ---------- projects list ----------
  function renderList(jobs) {
    const key = JSON.stringify(jobs.map((j) => [j.id, j.status, j.stage, j.source_title, j.clip_count])) + state.selectedId;
    if (key === state.listKey) return;
    state.listKey = key;
    $("#projects-count").textContent = jobs.length;
    const ul = $("#projects");
    if (!jobs.length) {
      ul.innerHTML = `<li class="projects-empty">Your projects will appear here.</li>`;
      return;
    }
    ul.innerHTML = jobs.map((j) => {
      const sub = j.status === "done"
        ? `${j.clip_count} clip${j.clip_count === 1 ? "" : "s"}`
        : STATUS_LABEL[j.status] || j.status;
      return `<li class="${j.id === state.selectedId ? "active" : ""}">
        <a href="#job/${esc(j.id)}" data-id="${esc(j.id)}">
          <span class="proj-avatar" aria-hidden="true">${esc(initials(titleOf(j)))}</span>
          <span class="proj-info">
            <span class="proj-name">${esc(titleOf(j))}</span>
            <span class="proj-sub"><span class="dot ${esc(j.status)}"></span>${esc(sub)} · ${esc(fmtAgo(j.created_at))}</span>
          </span>
        </a>
        <button class="del" type="button" data-del="${esc(j.id)}" aria-label="Delete project" title="Delete project">✕</button>
      </li>`;
    }).join("");
  }
  state.listKey = "";

  $("#projects").addEventListener("click", async (e) => {
    const del = e.target.closest("[data-del]");
    if (!del) return;
    e.preventDefault();
    const id = del.dataset.del;
    if (!confirm("Delete this project and its clips? Any collection entries from it will be removed too.")) return;
    try {
      await api(`/api/jobs/${id}`, { method: "DELETE" });
      if (state.selectedId === id) location.hash = "#/";
      toast("Project deleted.");
      state.listKey = "";
      refreshList();
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---------- project view ----------
  function ratingBadge(rating) {
    return rating ? `<span class="rating r-${RATING_CLASS[rating] || "fair"}">${esc(rating)}</span>` : "";
  }

  function watchMeter(c) {
    if (c.watch_rate == null) return "";
    const v = Math.max(0, Math.min(100, Math.round(c.watch_rate)));
    const src = c.watch_rate_source === "ai" ? "AI estimate" : "Local estimate";
    return `<div class="wr r-${RATING_CLASS[c.rating] || "fair"}" title="Estimated share of viewers still watching at the end. A guess, not measured retention.">
      <div class="wr-head"><span class="wr-k">Est. watch rate</span><span class="wr-v">${v}%</span>${ratingBadge(c.rating)}</div>
      <div class="wr-track" style="--v:${v}" aria-hidden="true"><span class="wr-fill" style="width:${v}%"></span><span class="wr-dot" style="left:${v}%"></span></div>
      <div class="wr-ends" aria-hidden="true"><span>Worst</span><span>Best</span></div>
      <div class="wr-src">${esc(src)} · a model guess, not measured</div>
    </div>`;
  }

  function clipCard(c) {
    const f = c.files || {};
    const platform = state.config?.platforms.find((p) => p.id === c.platform)?.label;
    const quality = c.output || state.config?.qualities.find((q) => q.id === c.quality)?.label;
    const meta = [`${fmtClock(c.start)} → ${fmtClock(c.end)}`, `${Math.round(c.duration)}s`, platform, quality].filter(Boolean);
    return `<article class="clip" data-id="${esc(c.id)}">
      <div class="clip-media">
        <span class="clip-badge">#${c.rank}</span>
        <button class="clip-expand" type="button" data-expand="${esc(c.id)}" aria-label="Open larger preview">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/></svg>
        </button>
        <video controls playsinline preload="metadata" src="${esc(mediaUrl(f.video))}" poster="${esc(mediaUrl(f.thumbnail))}"></video>
      </div>
      <div class="clip-body">
        <div class="clip-top">
          <div style="min-width:0">
            <div class="clip-title">${esc(c.title || "Untitled clip")}</div>
            <div class="clip-meta">${meta.map(esc).join(" · ")}</div>
          </div>
        </div>
        ${watchMeter(c)}
        ${c.reason ? `<div class="clip-reason">${esc(c.reason)}</div>` : ""}
        ${c.transcript ? `<details class="tr"><summary>Transcript</summary><p>${esc(c.transcript)}</p></details>` : ""}
        <div class="clip-actions">
          <a class="btn btn-primary btn-sm" href="${esc(mediaUrl(f.video))}" download>Download MP4</a>
          ${f.audio ? `<a class="btn btn-ghost btn-sm" href="${esc(mediaUrl(f.audio))}" download>MP3</a>` : ""}
          ${f.captions ? `<a class="btn btn-ghost btn-sm" href="${esc(mediaUrl(f.captions))}" download title="Caption file (ASS)">Captions</a>` : ""}
          <button class="btn btn-ghost btn-sm" type="button" data-add-clip="${esc(c.id)}">+ Collection</button>
        </div>
      </div>
    </article>`;
  }

  function pendingCard(index, total, rendering) {
    return `<div class="clip-pending" aria-hidden="true">
      <div class="ph"><div class="ph-inner">
        <div class="spin-lg"></div>
        <strong>${rendering ? `Rendering clip ${index} of ${total}` : `Clip ${index} of ${total}`}</strong>
        ${rendering ? "" : "Waiting in queue"}
      </div></div>
      <div class="pb">${rendering ? "Cutting, cropping and encoding…" : "Will appear here when ready"}</div>
    </div>`;
  }

  function sortClips(list) {
    const arr = list.slice();
    if (state.clipSort === "best") arr.sort((a, b) => (b.watch_rate ?? -1) - (a.watch_rate ?? -1) || a.rank - b.rank);
    else if (state.clipSort === "worst") arr.sort((a, b) => (a.watch_rate ?? 101) - (b.watch_rate ?? 101) || a.rank - b.rank);
    else arr.sort((a, b) => a.rank - b.rank);
    return arr;
  }

  function renderClips(job) {
    const grid = $("#clips");
    const clips = job.clips || [];
    for (const c of clips) {
      state.clipData.set(c.id, c);
      if (!state.clipEls.has(c.id)) {
        const tpl = document.createElement("div");
        tpl.innerHTML = clipCard(c).trim();
        state.clipEls.set(c.id, tpl.firstElementChild);
      }
    }
    const sorted = sortClips(clips).map((c) => c.id);
    const current = Array.from(grid.children).filter((n) => n.classList.contains("clip")).map((n) => n.dataset.id);
    // Only move cards when the order really changed: moving a playing <video> pauses it.
    if (sorted.join() !== current.join()) {
      sorted.forEach((id) => grid.appendChild(state.clipEls.get(id)));
    }
    grid.querySelectorAll(".clip-pending, .clips-empty").forEach((n) => n.remove());

    const total = job.planned_clips || job.params?.count || 0;
    const pendingCount = Math.max(0, total - clips.length);
    const running = job.status === "running" || job.status === "queued";
    for (let i = 0; i < pendingCount; i++) {
      grid.insertAdjacentHTML("beforeend", pendingCard(clips.length + i + 1, total, i === 0 && job.stage === "clipping"));
    }
    if (!clips.length && !pendingCount) {
      grid.insertAdjacentHTML("beforeend", job.status === "done"
        ? `<div class="clips-empty"><strong>No clips met the bar</strong>Try a longer video or a wider clip length.</div>`
        : `<div class="clips-empty"><strong>No clips yet</strong>Clips appear here one at a time as they render.</div>`);
    }
    $("#clips-count").textContent = `${clips.length} / ${total || (running ? "…" : clips.length)}`;
  }

  function renderLog(job) {
    const el = $("#console");
    const entries = job.log || [];
    if (entries.length < state.logCount) { el.innerHTML = ""; state.logCount = 0; }
    if (entries.length === state.logCount) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    const frag = document.createDocumentFragment();
    for (let i = state.logCount; i < entries.length; i++) {
      const e = entries[i];
      const row = document.createElement("div");
      row.className = `log ${esc(e.level)}`;
      row.innerHTML = `<span class="log-t">${fmtTime(e.t)}</span><span class="log-mark">${e.level === "detail" ? "" : "›"}</span><span class="log-msg"></span>`;
      row.querySelector(".log-msg").textContent = e.msg;
      frag.appendChild(row);
    }
    el.appendChild(frag);
    state.logCount = entries.length;
    if (nearBottom) el.scrollTop = el.scrollHeight;
    else $("#jump").hidden = false;
  }

  function renderHeader(job) {
    const p = job.params || {};
    const key = [job.id, job.status, titleOf(job), JSON.stringify(p), job.llm_used, job.source_duration].join("|");
    if (key === state.headerKey) return;
    state.headerKey = key;
    const cfg = state.config || { platforms: [], qualities: [], caption_styles: [] };
    $("#p-kicker").textContent = job.source_url ? "Video link" : "Uploaded file";
    $("#p-title").textContent = titleOf(job);
    $("#p-title").title = titleOf(job);
    const plat = cfg.platforms.find((x) => x.id === p.platform);
    const qual = cfg.qualities.find((x) => x.id === p.quality);
    const style = cfg.caption_styles.find((x) => x.id === p.caption_style);
    const chips = [
      p.min_seconds != null ? `<span class="chip">${esc(p.min_seconds)}–${esc(p.max_seconds)}s</span>` : "",
      p.count ? `<span class="chip">Up to ${esc(p.count)} clips</span>` : "",
      plat ? `<span class="chip">${esc(plat.label)} · 9:16</span>` : "",
      p.quality_mode !== "custom" && qual ? `<span class="chip">${esc(qual.label)}</span>` : "",
      p.quality_mode === "custom" ? `<span class="chip">${esc(cfg.resolutions?.find((r) => r.id === p.resolution)?.label || p.resolution)} custom</span>` : "",
      p.fps_mode === "custom" ? `<span class="chip">${esc(p.fps)} fps</span>` : "",
      p.aspect_mode === "custom" ? `<span class="chip">${esc(p.aspect)} aspect</span>` : "",
      p.captions ? `<span class="chip">Captions${style ? ` · ${esc(style.label)}` : ""}</span>` : "",
      job.llm_used ? `<span class="chip ai">AI ranked</span>` : "",
      job.source_duration ? `<span class="chip">Source ${fmtSpan(job.source_duration)}</span>` : "",
    ];
    $("#p-chips").innerHTML = chips.join("");
    const st = $("#p-status");
    st.className = `status ${job.status}`;
    $("#p-status-text").textContent = STATUS_LABEL[job.status] || job.status;
  }

  function renderProgress(job) {
    const percent = Math.round((job.progress || 0) * 100);
    $("#p-pct").textContent = percent;
    $("#bar").setAttribute("aria-valuenow", String(percent));
    $("#bar-fill").style.width = `${percent}%`;
    $("#bar").classList.toggle("idle", TERMINAL.has(job.status));
    $("#p-msg").textContent = job.message || "Working…";

    const idx = STAGE_INDEX[job.stage] ?? -1;
    $$("#stages li").forEach((li) => {
      const i = +li.dataset.stage;
      li.classList.toggle("done", job.status === "done" || i < idx);
      li.classList.toggle("active", job.status !== "done" && job.status !== "failed" && i === idx);
      li.classList.toggle("failed", job.status === "failed" && i === Math.max(idx, 0));
    });
    updateSub(job);
  }

  function updateSub(job) {
    const total = job.planned_clips || job.params?.count || 0;
    const ready = (job.clips || []).length;
    const start = job.created_at || Date.now() / 1000;
    const end = TERMINAL.has(job.status) ? (job.finished_at || Date.now() / 1000) : Date.now() / 1000;
    const parts = [`${TERMINAL.has(job.status) ? "Took" : "Elapsed"} ${fmtSpan(end - start)}`];
    if (total) parts.push(`${ready} of ${total} clips ready`);
    $("#p-sub").textContent = parts.join(" · ");
  }

  function renderAlerts(job) {
    const key = JSON.stringify([job.status, job.error, job.warnings]);
    if (key === state.alertKey) return;
    state.alertKey = key;
    const parts = [];
    for (const w of job.warnings || []) {
      parts.push(`<div class="alert warn"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg><div>${esc(w)}</div></div>`);
    }
    if (job.status === "failed") {
      parts.push(`<div class="alert err"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg><div><strong>This project stopped.</strong> ${esc(job.error || "Unknown error")}<br>Check the activity log for details, then start a new project.</div></div>`);
    }
    $("#alerts").innerHTML = parts.join("");
  }

  // ---------- change the format: re-cut clips from the downloaded video ----------
  function variantStatus(v) {
    return { queued: "Waiting", running: "Cutting…", done: "Ready", failed: "Failed" }[v.status] || v.status;
  }

  function renderReformat(job) {
    const box = $("#reformat");
    box.hidden = !job.source_ready;
    if (!job.source_ready) return;
    const done = job.status === "done";
    $("#reformat-sub").textContent = done
      ? "Re-cut these clips from the video you downloaded. Nothing is downloaded again."
      : "Available as soon as the clips are finished.";
    $("#ref-choices").innerHTML = (state.config?.aspect_choices || []).map((a) => {
      const [w, h] = a.id.split(":").map(Number);
      const k = 40 / Math.max(w, h);
      return `<button type="button" class="choice ref-choice" data-aspect="${esc(a.id)}" title="${esc(a.hint || "")}" ${done ? "" : "disabled"}>
        <span class="ref-shape" style="width:${Math.round(w * k)}px;height:${Math.round(h * k)}px" aria-hidden="true"></span>
        <span class="ch-t">${esc(a.label)}</span><span class="ref-ratio">${esc(a.id)}</span></button>`;
    }).join("");

    // Rebuild the cut list only when something changed, so playing videos are not reset.
    const variants = job.variants || [];
    const clipCount = (job.clips || []).length;
    const key = JSON.stringify([job.status, clipCount, variants.map((v) => [v.id, v.status, (v.clips || []).length])]);
    if (key === state.refKey) return;
    state.refKey = key;
    $("#ref-variants").innerHTML = variants.slice().reverse().map((v) => {
      const clips = v.clips || [];
      const live = v.status === "queued" || v.status === "running";
      let pend = "";
      if (live) {
        for (let i = 0; i < Math.max(0, clipCount - clips.length); i++) {
          pend += pendingCard(clips.length + i + 1, clipCount, i === 0 && v.status === "running");
        }
      }
      return `<div class="ref-variant">
        <div class="ref-variant-head"><strong>${esc(v.aspect)}</strong><span class="ref-status ${esc(v.status)}">${esc(variantStatus(v))}</span></div>
        ${v.error ? `<p class="ref-error">${esc(v.error)}</p>` : ""}
        <div class="clips ref-clips">${clips.map((c) => clipCard(c)).join("")}${pend}</div>
      </div>`;
    }).join("");
  }

  async function requestVariant(aspect) {
    const job = state.job;
    if (!job || job.status !== "done") return;
    $("#ref-hint").textContent = "";
    try {
      await api(`/api/jobs/${job.id}/variants`, sendJson("POST", { aspect }));
      toast(`Cutting the clips at ${aspect}`);
      state.variantsBusy = true;
      state.refKey = null;
      await refreshDetail();
    } catch (err) {
      $("#ref-hint").textContent = err.message;
    }
  }

  $("#ref-choices").addEventListener("click", (e) => {
    const b = e.target.closest("[data-aspect]");
    if (b && !b.disabled) requestVariant(b.dataset.aspect);
  });
  $("#ref-custom").addEventListener("submit", (e) => {
    e.preventDefault();
    const r = parseAspectText($("#ref-custom-input").value);
    if (r.error) { $("#ref-hint").textContent = r.error; return; }
    requestVariant(r.value);
  });

  function renderProject(job) {
    state.job = job;
    renderHeader(job);
    renderProgress(job);
    renderAlerts(job);
    renderReformat(job);
    state.variantsBusy = (job.variants || []).some((v) => v.status === "queued" || v.status === "running");
    renderClips(job);
    renderLog(job);
    $("#live").classList.toggle("off", TERMINAL.has(job.status));
    $("#live-text").textContent = job.status === "done" ? "Finished" : job.status === "failed" ? "Stopped" : "Live";
  }

  function resetProjectView() {
    state.job = null;
    state.headerKey = "";
    state.alertKey = "";
    state.logCount = 0;
    state.clipEls.clear();
    state.clipData.clear();
    $("#clips").innerHTML = "";
    state.refKey = null;
    $("#reformat").hidden = true;
    $("#ref-variants").innerHTML = "";
    $("#console").innerHTML = "";
    $("#alerts").innerHTML = "";
    $("#jump").hidden = true;
    $("#clips-count").textContent = "0 / 0";
  }

  $("#clip-sort").addEventListener("change", (e) => {
    state.clipSort = e.target.value;
    if (state.job) renderClips(state.job);
  });

  // ---------- polling ----------
  async function refreshList() {
    try {
      const jobs = await api("/api/jobs");
      state.jobs = jobs;
      renderList(jobs);
      return jobs;
    } catch {
      return state.jobs;
    }
  }

  async function refreshDetail() {
    const id = state.selectedId;
    if (!id) return;
    const summary = state.jobs.find((j) => j.id === id);
    if (state.jobs.length && !summary) { location.hash = "#/"; return; }
    if (summary && TERMINAL.has(summary.status) && state.detailLoadedFor === id && !state.variantsBusy) return;
    try {
      const job = await api(`/api/jobs/${id}`);
      if (state.selectedId !== id) return;
      renderProject(job);
      if (TERMINAL.has(job.status)) state.detailLoadedFor = id;
    } catch { /* transient: try again next tick */ }
  }

  async function tick() {
    if (state.route.view === "home" || state.route.view === "job") {
      await refreshList();
      await refreshDetail();
    }
    setTimeout(tick, 1200);
  }
  setInterval(() => { if (state.job) updateSub(state.job); }, 1000);

  $("#console").addEventListener("scroll", (e) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 60) $("#jump").hidden = true;
  });
  $("#jump").addEventListener("click", () => {
    const el = $("#console");
    el.scrollTop = el.scrollHeight;
    $("#jump").hidden = true;
  });

  // ---------- preview dialog ----------
  const dialog = $("#preview");
  function openPreview({ video, poster, kicker, title, reason, links = [] }) {
    if (!video) return;
    $("#preview-kicker").textContent = kicker || "";
    $("#preview-title").textContent = title || "Untitled clip";
    $("#preview-reason").textContent = reason || "";
    const v = $("#preview-video");
    v.src = mediaUrl(video);
    v.poster = poster ? mediaUrl(poster) : "";
    $("#preview-links").innerHTML = [
      `<a class="btn btn-primary btn-sm" href="${esc(mediaUrl(video))}" download>Download MP4</a>`,
      ...links.map((l) => `<a class="btn btn-ghost btn-sm" href="${esc(mediaUrl(l.href))}" download>${esc(l.label)}</a>`),
    ].join("");
    dialog.showModal();
    v.play().catch(() => {});
  }
  $("#clips").addEventListener("click", (e) => {
    const expand = e.target.closest("[data-expand]");
    if (expand) {
      const c = state.clipData.get(expand.dataset.expand);
      if (!c) return;
      const f = c.files || {};
      openPreview({
        video: f.video, poster: f.thumbnail, reason: c.reason,
        title: c.title,
        kicker: `Clip #${c.rank} · ${fmtClock(c.start)} → ${fmtClock(c.end)} · ${Math.round(c.duration)}s${c.watch_rate != null ? ` · watch rate ${pct(c.watch_rate)} (${c.rating})` : ""}`,
        links: [f.audio && { href: f.audio, label: "MP3" }, f.captions && { href: f.captions, label: "Captions" }].filter(Boolean),
      });
      return;
    }
    const add = e.target.closest("[data-add-clip]");
    if (add) {
      const c = state.clipData.get(add.dataset.addClip);
      if (c && state.job) openCollectionPicker({ job_id: state.job.id, clip_id: c.id, title: c.title });
    }
  });
  $("#preview-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => { const v = $("#preview-video"); v.pause(); v.removeAttribute("src"); v.load(); });

  // ---------- collections: list ----------
  function collectionCard(c) {
    const slugStatus = slug(c.status);
    return `<a class="coll-card" href="#/collections/${esc(c.id)}">
      <div class="cc-top">
        <span class="status-pill s-${esc(slugStatus)}">${esc(c.status)}</span>
        ${c.label ? `<span class="label-chip lc-${esc(c.label_color || "grey")}">${esc(c.label)}</span>` : ""}
      </div>
      <h3 class="cc-name">${esc(c.name)}</h3>
      <div class="cc-foot"><span>${c.clip_count} clip${c.clip_count === 1 ? "" : "s"}</span><span>Updated ${esc(fmtAgo(c.updated_at))}</span></div>
    </a>`;
  }

  async function loadCollections() {
    try {
      state.collections = await api("/api/collections");
    } catch (err) {
      toast(err.message, "err");
    }
    $("#coll-grid").innerHTML = state.collections.map(collectionCard).join("");
    $("#coll-empty").hidden = state.collections.length > 0;
  }

  $("#coll-new-toggle").addEventListener("click", () => setNewCollectionOpen(true));
  $("#coll-new-cancel").addEventListener("click", () => setNewCollectionOpen(false));
  function setNewCollectionOpen(open) {
    $("#coll-new").hidden = !open;
    $("#coll-new-toggle").setAttribute("aria-expanded", String(open));
    if (open) setTimeout(() => $("#coll-new input[name=name]").focus(), 0);
  }

  $("#coll-new").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const color = $("#coll-new-colors [aria-checked='true']")?.dataset.id || null;
    try {
      const col = await api("/api/collections", sendJson("POST", {
        name: f.name.value.trim(),
        status: f.status.value,
        label: f.label.value.trim(),
        label_color: color,
      }));
      f.reset();
      $("#coll-new-status").value = "Draft";
      $$("#coll-new-colors [data-id]").forEach((x) => x.setAttribute("aria-checked", "false"));
      setNewCollectionOpen(false);
      toast("Collection created.", "ok");
      location.hash = `#/collections/${col.id}`;
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---------- collections: detail ----------
  const STATUSES_FALLBACK = ["Draft", "In review", "Ready", "Scheduled", "Posted"];

  function renderCollectionHead(col) {
    const statuses = state.config?.collection_statuses || STATUSES_FALLBACK;
    const colors = state.config?.label_colors || Object.keys(LABEL_HEX);
    $("#coll-head").innerHTML = `
      <div class="ch-grid">
        <label class="field grow"><span class="label">Name</span>
          <input class="input ch-name" maxlength="80" value="${esc(col.name)}" aria-label="Collection name"></label>
        <label class="field"><span class="label">Status</span>
          <select class="input ch-status" aria-label="Status">${statuses.map((s) => `<option ${s === col.status ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
      </div>
      <div class="ch-grid">
        <label class="field grow"><span class="label">Label</span>
          <input class="input ch-label" maxlength="30" value="${esc(col.label)}" placeholder="e.g. Client A, Promo" aria-label="Label"></label>
        <div class="field"><span class="label">Colour</span>
          <div class="swatches ch-colors" role="radiogroup" aria-label="Label colour">${swatchButtons(colors, col.label_color || "", (id) => LABEL_HEX[id])}</div></div>
      </div>
      <label class="field"><span class="label">Notes</span>
        <textarea class="input ch-notes" rows="3" maxlength="4000" placeholder="What is this collection for? Posting plan, feedback, reminders…" aria-label="Notes">${esc(col.notes)}</textarea></label>
      <div class="ch-foot">
        <span class="save-state" id="save-state" aria-live="polite">Saved</span>
        <button class="btn btn-ghost btn-sm ch-delete" type="button">Delete collection</button>
      </div>`;
  }

  function entryRow(e, index, total) {
    const f = e;
    return `<li class="entry" data-eid="${esc(e.id)}">
      <div class="entry-thumb">
        ${e.thumbnail ? `<img src="${esc(mediaUrl(e.thumbnail))}" alt="" loading="lazy">` : `<span class="entry-noimg">▶</span>`}
        <span class="entry-no">${index + 1}</span>
      </div>
      <div class="entry-main">
        <input class="input e-title" maxlength="200" value="${esc(e.title)}" aria-label="Clip title">
        <div class="entry-meta">
          <span>${esc(e.source_title || "Untitled project")}</span>
          <span>${fmtClock(e.start)} → ${fmtClock(e.end)} · ${Math.round(e.duration || 0)}s</span>
          ${e.watch_rate != null ? `<span class="meta-rate r-${RATING_CLASS[e.rating] || "fair"}">${pct(e.watch_rate)} · ${esc(e.rating || "")}</span>` : ""}
        </div>
        <div class="entry-row2">
          <input class="input e-note" maxlength="1000" value="${esc(e.note)}" placeholder="Add a note…" aria-label="Note for this clip">
          <input class="input e-label" maxlength="30" value="${esc(e.label || "")}" placeholder="Label" aria-label="Clip label">
        </div>
      </div>
      <div class="entry-side">
        <select class="input e-status" aria-label="Clip status">${(state.config?.collection_statuses || STATUSES_FALLBACK).map((s) => `<option ${s === e.status ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
        <div class="entry-btns">
          <button class="icon-btn e-up" type="button" aria-label="Move up" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="icon-btn e-down" type="button" aria-label="Move down" ${index === total - 1 ? "disabled" : ""}>↓</button>
          <button class="icon-btn e-play" type="button" aria-label="Preview">▶</button>
          <a class="icon-btn" href="${esc(mediaUrl(f.video))}" download aria-label="Download MP4" title="Download MP4">⤓</a>
          <button class="icon-btn e-remove" type="button" aria-label="Remove from collection" title="Remove">✕</button>
        </div>
      </div>
    </li>`;
  }

  function renderEntries(col) {
    const entries = col.entries || [];
    $("#coll-count").textContent = entries.length;
    $("#entries").innerHTML = entries.map((e, i) => entryRow(e, i, entries.length)).join("");
    $("#entries-empty").hidden = entries.length > 0;
  }

  async function loadCollection(id) {
    try {
      const col = await api(`/api/collections/${id}`);
      if (state.route.view !== "collection" || state.route.id !== id) return;
      state.collection = col;
      renderCollectionHead(col);
      renderEntries(col);
    } catch (err) {
      toast(err.message, "err");
      location.hash = "#/collections";
    }
  }

  function setSave(text, kind = "") {
    const el = $("#save-state");
    if (!el) return;
    el.textContent = text;
    el.className = `save-state ${kind}`;
  }

  async function patchCollection(fields) {
    const col = state.collection;
    if (!col) return;
    setSave("Saving…");
    try {
      const updated = await api(`/api/collections/${col.id}`, sendJson("PATCH", fields));
      state.collection = { ...col, ...updated, entries: col.entries };
      setSave("Saved", "ok");
    } catch (err) {
      setSave("Not saved", "err");
      toast(err.message, "err");
      renderCollectionHead(col);
    }
  }

  const viewCollection = $("#view-collection");
  viewCollection.addEventListener("change", (e) => {
    if (e.target.matches(".ch-status")) patchCollection({ status: e.target.value });
  });
  viewCollection.addEventListener("focusout", (e) => {
    const col = state.collection;
    if (!col) return;
    const t = e.target;
    if (t.matches(".ch-name")) {
      const v = t.value.trim();
      if (!v) { t.value = col.name; toast("A collection needs a name.", "err"); return; }
      if (v !== col.name) patchCollection({ name: v });
    } else if (t.matches(".ch-label")) {
      if (t.value.trim() !== col.label) patchCollection({ label: t.value.trim() });
    } else if (t.matches(".ch-notes")) {
      if (t.value !== col.notes) patchCollection({ notes: t.value });
    }
  });
  viewCollection.addEventListener("click", async (e) => {
    const sw = e.target.closest(".ch-colors [data-id]");
    if (sw && state.collection) {
      const next = sw.dataset.id;
      $$(".ch-colors [data-id]").forEach((x) => x.setAttribute("aria-checked", String(x === sw)));
      patchCollection({ label_color: next });
      return;
    }
    if (e.target.closest(".ch-delete") && state.collection) {
      if (!confirm(`Delete “${state.collection.name}”? The clips stay in their projects.`)) return;
      try {
        await api(`/api/collections/${state.collection.id}`, { method: "DELETE" });
        toast("Collection deleted.");
        location.hash = "#/collections";
      } catch (err) { toast(err.message, "err"); }
      return;
    }
    if (e.target.closest("#coll-add")) { openClipPicker(); return; }
  });

  // Entry edits (delegated)
  const entriesEl = $("#entries");
  async function patchEntry(eid, fields) {
    const col = state.collection;
    if (!col) return;
    try {
      const updated = await api(`/api/collections/${col.id}/entries/${eid}`, sendJson("PATCH", fields));
      const i = col.entries.findIndex((x) => x.id === eid);
      if (i >= 0) col.entries[i] = { ...col.entries[i], ...updated };
    } catch (err) {
      toast(err.message, "err");
      renderEntries(col);
    }
  }
  entriesEl.addEventListener("change", (e) => {
    const li = e.target.closest(".entry");
    if (li && e.target.matches(".e-status")) patchEntry(li.dataset.eid, { status: e.target.value });
  });
  entriesEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches("input")) e.target.blur();
  });
  entriesEl.addEventListener("focusout", (e) => {
    const li = e.target.closest(".entry");
    const col = state.collection;
    if (!li || !col) return;
    const entry = col.entries.find((x) => x.id === li.dataset.eid);
    if (!entry) return;
    const t = e.target;
    if (t.matches(".e-title")) {
      const v = t.value.trim();
      if (!v) { t.value = entry.title; return; }
      if (v !== entry.title) patchEntry(entry.id, { title: v });
    } else if (t.matches(".e-note")) {
      if (t.value !== entry.note) patchEntry(entry.id, { note: t.value });
    } else if (t.matches(".e-label")) {
      if (t.value.trim() !== (entry.label || "")) patchEntry(entry.id, { label: t.value.trim() });
    }
  });
  entriesEl.addEventListener("click", async (e) => {
    const li = e.target.closest(".entry");
    const col = state.collection;
    if (!li || !col) return;
    const eid = li.dataset.eid;
    const entry = col.entries.find((x) => x.id === eid);
    if (!entry) return;
    if (e.target.closest(".e-play")) {
      openPreview({
        video: entry.video, poster: entry.thumbnail, title: entry.title, reason: entry.note,
        kicker: `${entry.source_title || "Clip"} · ${fmtClock(entry.start)} → ${fmtClock(entry.end)}`,
        links: [entry.audio && { href: entry.audio, label: "MP3" }, entry.captions && { href: entry.captions, label: "Captions" }].filter(Boolean),
      });
    } else if (e.target.closest(".e-up") || e.target.closest(".e-down")) {
      const direction = e.target.closest(".e-up") ? "up" : "down";
      try {
        await api(`/api/collections/${col.id}/entries/${eid}/move`, sendJson("POST", { direction }));
        await loadCollection(col.id);
      } catch (err) { toast(err.message, "err"); }
    } else if (e.target.closest(".e-remove")) {
      if (!confirm("Remove this clip from the collection? The clip itself is kept in its project.")) return;
      try {
        await api(`/api/collections/${col.id}/entries/${eid}`, { method: "DELETE" });
        await loadCollection(col.id);
        toast("Removed from collection.");
      } catch (err) { toast(err.message, "err"); }
    }
  });

  // ---------- picker dialog ----------
  const picker = $("#picker");
  $("#picker-close").addEventListener("click", () => picker.close());
  picker.addEventListener("click", (e) => { if (e.target === picker) picker.close(); });

  function pickerRow(html) { return `<div class="picker-row">${html}</div>`; }

  // From a clip card: choose (or create) a collection
  async function openCollectionPicker(clip) {
    $("#picker-title").textContent = "Add to a collection";
    const list = $("#picker-list");
    list.innerHTML = `<div class="picker-note">Choose a collection for “${esc(clip.title)}”.</div>
      <form class="picker-new" id="picker-new">
        <input class="input" maxlength="80" placeholder="New collection name" aria-label="New collection name">
        <button class="btn btn-primary btn-sm" type="submit">Create &amp; add</button>
      </form><div class="picker-items">Loading…</div>`;
    picker.showModal();
    const items = $("#picker-list .picker-items");
    try {
      const cols = await api("/api/collections");
      items.innerHTML = cols.length
        ? cols.map((c) => pickerRow(`<span class="pr-main"><strong>${esc(c.name)}</strong><span class="pr-sub">${esc(c.status)} · ${c.clip_count} clip${c.clip_count === 1 ? "" : "s"}</span></span>
            <button class="btn btn-ghost btn-sm" type="button" data-add-to="${esc(c.id)}">Add</button>`)).join("")
        : `<div class="picker-note">No collections yet. Create one above.</div>`;
    } catch (err) { items.textContent = err.message; }

    list.onclick = async (ev) => {
      const b = ev.target.closest("[data-add-to]");
      if (!b) return;
      await addClipTo(b.dataset.addTo, clip);
    };
    $("#picker-new").onsubmit = async (ev) => {
      ev.preventDefault();
      const name = ev.target.querySelector("input").value.trim();
      if (!name) return;
      try {
        const col = await api("/api/collections", sendJson("POST", { name }));
        await addClipTo(col.id, clip, col.name);
      } catch (err) { toast(err.message, "err"); }
    };
  }

  async function addClipTo(cid, clip, name) {
    try {
      await api(`/api/collections/${cid}/entries`, sendJson("POST", { job_id: clip.job_id, clip_id: clip.clip_id }));
      picker.close();
      const label = name || (state.collections.find((c) => c.id === cid) || {}).name || "collection";
      toast(`Added to “${label}”.`, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  // From a collection: pick finished clips from any project
  async function openClipPicker() {
    const col = state.collection;
    if (!col) return;
    $("#picker-title").textContent = "Add clips";
    const list = $("#picker-list");
    list.innerHTML = `<div class="picker-note">Finished clips from all projects. Clips already here are greyed out.</div><div class="picker-items">Loading…</div>`;
    picker.showModal();
    const items = $("#picker-list .picker-items");
    try {
      const clips = await api("/api/clips");
      const have = new Set(col.entries.map((e) => `${e.job_id}/${e.clip_id}`));
      if (!clips.length) { items.innerHTML = `<div class="picker-note">No finished clips yet. Create some first.</div>`; return; }
      const groups = new Map();
      for (const c of clips) {
        const key = c.source_title || "Untitled project";
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(c);
      }
      items.innerHTML = Array.from(groups.entries()).map(([src, arr]) => `
        <div class="picker-group"><div class="pg-title">${esc(src)}</div>
        ${arr.map((c) => {
          const added = have.has(`${c.job_id}/${c.clip_id}`);
          return pickerRow(`${c.thumbnail ? `<img class="pr-thumb" src="${esc(mediaUrl(c.thumbnail))}" alt="" loading="lazy">` : `<span class="pr-thumb pr-none">▶</span>`}
            <span class="pr-main"><strong>${esc(c.title)}</strong><span class="pr-sub">${c.watch_rate != null ? `${pct(c.watch_rate)} · ${esc(c.rating)}` : "Not rated"}</span></span>
            <button class="btn btn-ghost btn-sm" type="button" data-pick="${esc(c.job_id)}|${esc(c.clip_id)}" ${added ? "disabled" : ""}>${added ? "Added" : "Add"}</button>`);
        }).join("")}</div>`).join("");
    } catch (err) { items.textContent = err.message; }

    list.onclick = async (ev) => {
      const b = ev.target.closest("[data-pick]");
      if (!b || b.disabled) return;
      const [job_id, clip_id] = b.dataset.pick.split("|");
      try {
        await api(`/api/collections/${col.id}/entries`, sendJson("POST", { job_id, clip_id }));
        b.disabled = true;
        b.textContent = "Added";
        await loadCollection(col.id);
      } catch (err) { toast(err.message, "err"); }
    };
  }

  // ---------- Projects dropdown ----------
  const projectsBtn = $("#projects-btn");
  const projectsMenu = $("#projects-menu");
  function setMenu(open) {
    projectsMenu.hidden = !open;
    projectsBtn.setAttribute("aria-expanded", String(open));
    if (open) projectsMenu.querySelector(".menu-item")?.focus();
  }
  projectsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    setMenu(projectsMenu.hidden);
  });
  projectsMenu.addEventListener("click", (e) => {
    const item = e.target.closest("[data-go]");
    if (!item) return;
    setMenu(false);
    const go = item.dataset.go;
    if (go === "collections") location.hash = "#/collections";
    else if (go === "clips") location.hash = "#/clips";
    else {
      // New project: the clip form lives on the home view, so go there and focus the link field.
      location.hash = "#/";
      setTimeout(() => {
        window.scrollTo({ top: 0 });
        $("#url").focus({ preventScroll: true });
      }, 80);
    }
  });
  document.addEventListener("click", (e) => {
    if (!$("#menu-wrap").contains(e.target)) setMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !projectsMenu.hidden) {
      setMenu(false);
      projectsBtn.focus();
    }
  });

  // ---------- Clips library ----------
  function libraryCard(c) {
    const plat = state.config?.platforms.find((x) => x.id === c.platform)?.label;
    const rate = c.watch_rate != null
      ? `<span class="meta-rate r-${RATING_CLASS[c.rating] || "fair"}">${pct(c.watch_rate)} · ${esc(c.rating || "")}</span>`
      : `<span class="muted-sm">Not rated</span>`;
    const meta = [rate, c.duration ? `${Math.round(c.duration)}s` : "", plat ? esc(plat) : ""].filter(Boolean).join(" · ");
    const ids = `data-job="${esc(c.job_id)}" data-clip="${esc(c.clip_id)}"`;
    return `<article class="lib-card">
      <button class="lib-media" type="button" data-lib-play ${ids} aria-label="Preview ${esc(c.title)}">
        ${c.thumbnail ? `<img src="${esc(mediaUrl(c.thumbnail))}" alt="" loading="lazy">` : `<span class="lib-none">▶</span>`}
        <span class="lib-play" aria-hidden="true">▶</span>
      </button>
      <div class="lib-body">
        <div class="lib-title">${esc(c.title || "Untitled clip")}</div>
        <div class="lib-sub">${esc(c.source_title || "Untitled project")}</div>
        <div class="lib-meta">${meta}</div>
        <div class="lib-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-lib-play ${ids}>Preview</button>
          <a class="btn btn-ghost btn-sm" href="${esc(mediaUrl(c.video))}" download>Download</a>
          <button class="btn btn-ghost btn-sm" type="button" data-lib-add ${ids}>+ Collection</button>
        </div>
      </div>
    </article>`;
  }

  async function loadClips() {
    try {
      state.libraryClips = await api("/api/clips");
    } catch (err) {
      toast(err.message, "err");
      state.libraryClips = state.libraryClips || [];
    }
    const clips = state.libraryClips;
    $("#lib-count").textContent = clips.length;
    $("#lib-empty").hidden = clips.length > 0;
    $("#lib-grid").innerHTML = clips.map(libraryCard).join("");
  }

  $("#lib-grid").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-lib-play],[data-lib-add]");
    if (!btn) return;
    const c = (state.libraryClips || []).find((x) => x.job_id === btn.dataset.job && x.clip_id === btn.dataset.clip);
    if (!c) return;
    if (btn.hasAttribute("data-lib-play")) {
      openPreview({
        video: c.video,
        poster: c.thumbnail,
        title: c.title,
        reason: c.reason,
        kicker: [c.source_title, c.duration ? `${Math.round(c.duration)}s` : "", c.watch_rate != null ? `watch rate ${pct(c.watch_rate)} (${c.rating})` : ""].filter(Boolean).join(" · "),
        links: [c.audio && { href: c.audio, label: "MP3" }, c.captions && { href: c.captions, label: "Captions" }].filter(Boolean),
      });
    } else {
      openCollectionPicker({ job_id: c.job_id, clip_id: c.clip_id, title: c.title });
    }
  });

  // ---------- routing ----------
  function parseRoute() {
    const h = location.hash.replace(/^#/, "");
    let m = h.match(/^\/?collections\/([a-z0-9]+)$/i);
    if (m) return { view: "collection", id: m[1] };
    if (/^\/?collections(\?|$)/i.test(h)) {
      const q = new URLSearchParams(h.split("?")[1] || "");
      return { view: "collections", isNew: q.get("new") === "1" };
    }
    m = h.match(/^job\/([a-z0-9]+)$/i);
    if (m) return { view: "job", id: m[1] };
    if (/^\/?clips$/i.test(h)) return { view: "clips" };
    return { view: "home" };
  }

  function showView(view) {
    const map = { home: "#view-home", job: "#view-job", collections: "#view-collections", collection: "#view-collection", clips: "#view-clips" };
    for (const [k, sel] of Object.entries(map)) $(sel).hidden = k !== view;
    document.body.dataset.view = view;
    $("#sidebar").hidden = !(view === "home" || view === "job");
  }

  function onRoute() {
    const r = parseRoute();
    const prev = state.route;
    state.route = r;
    showView(r.view === "collection" ? "collection" : r.view);

    if (r.view === "job") {
      if (state.selectedId !== r.id) {
        state.selectedId = r.id;
        resetProjectView();
        state.detailLoadedFor = null;
        state.listKey = "";
        renderList(state.jobs);
        window.scrollTo({ top: 0 });
      }
      refreshDetail();
    } else {
      if (state.selectedId) { state.selectedId = null; resetProjectView(); state.listKey = ""; renderList(state.jobs); }
    }

    if (r.view === "home") {
      setTimeout(() => $("#url")?.focus({ preventScroll: true }), 0);
    }
    if (r.view === "collections") {
      loadCollections().then(() => { if (r.isNew) setNewCollectionOpen(true); });
      if (r.isNew) setNewCollectionOpen(true);
    }
    if (r.view === "clips") loadClips();
    if (r.view === "collection") {
      if (!prev || prev.view !== "collection" || prev.id !== r.id || !state.collection || state.collection.id !== r.id) {
        state.collection = null;
        $("#coll-head").innerHTML = `<div class="picker-note">Loading…</div>`;
        $("#entries").innerHTML = "";
        loadCollection(r.id);
      }
    }
    window.scrollTo({ top: 0 });
  }

  window.addEventListener("hashchange", onRoute);

  // ---------- boot ----------
  loadConfig();
  refreshList().then(() => onRoute());
  setTimeout(tick, 1200);
})();
