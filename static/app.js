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
  const titleOf = (j) => j.source_title || j.upload_name ||
    (j.source_url ? (() => { try { return new URL(j.source_url).hostname.replace(/^www\./, ""); } catch { return "Video link"; } })() : "Untitled project");

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

  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ---------- state ----------
  const state = {
    selectedId: null,
    jobs: [],
    job: null,            // latest detail for the selected project
    listKey: "",
    headerKey: "",
    alertKey: "",
    logCount: 0,
    clipEls: new Map(),   // clip id -> element
    clipData: new Map(),  // clip id -> clip record
    detailLoadedFor: null,
    busy: false,
  };

  // ---------- engine info ----------
  async function loadConfig() {
    try {
      const c = await api("/api/config");
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
    } catch {
      $("#engine-text").textContent = "Engine offline";
      $("#engine-pill").classList.add("off");
    }
  }

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
  function selectedLength() {
    const pick = $(".pick[aria-pressed='true']") || $(".pick[data-min]");
    return { min: +pick.dataset.min, max: +pick.dataset.max };
  }

  const fileInput = $("#file");
  function showFile(file) {
    $("#dz-empty").hidden = !!file;
    $("#dz-file").hidden = !file;
    if (!file) { fileInput.value = ""; return; }
    $("#dz-name").textContent = `${file.name}`;
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

  $("#create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.busy) return;
    const isLink = $("#tab-link").getAttribute("aria-selected") === "true";
    const { min, max } = selectedLength();
    const common = {
      count: +$("#count").value,
      min_seconds: min,
      max_seconds: max,
      vertical: $("#vertical").checked,
      captions: $("#captions").checked,
      use_llm: $("#use_llm").checked && !$("#use_llm").disabled,
    };

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
        job = await api("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, ...common }),
        });
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
      select(job.id);
    } catch (err) {
      toast(err.message || "Something went wrong.", "err");
    } finally {
      setBusy(false);
    }
  });

  // ---------- projects list ----------
  function renderList(jobs) {
    const key = JSON.stringify(jobs.map((j) => [j.id, j.status, j.stage, j.source_title, j.clip_count]) ) + state.selectedId;
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

  $("#projects").addEventListener("click", async (e) => {
    const del = e.target.closest("[data-del]");
    if (!del) return;
    e.preventDefault();
    const id = del.dataset.del;
    if (!confirm("Delete this project and its clips? This can't be undone.")) return;
    try {
      await api(`/api/jobs/${id}`, { method: "DELETE" });
      if (state.selectedId === id) clearSelection();
      toast("Project deleted.");
      state.listKey = "";
      refreshList();
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---------- project view ----------
  function ring(score) {
    const s = Math.max(0, Math.min(100, Math.round(score || 0)));
    const cls = s >= 70 ? "hi" : s >= 55 ? "mid" : "lo";
    const C = 2 * Math.PI * 16;
    return `<div class="ring ${cls}" title="Engagement score (0–100)">
      <svg viewBox="0 0 40 40" aria-hidden="true">
        <circle class="ring-bg" cx="20" cy="20" r="16" fill="none" stroke-width="3.5"/>
        <circle class="ring-arc" cx="20" cy="20" r="16" fill="none" stroke-width="3.5" stroke-linecap="round"
          stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(C * (1 - s / 100)).toFixed(1)}"/>
      </svg>
      <span>${s}</span>
    </div>`;
  }

  function clipCard(c) {
    const f = c.files || {};
    return `<article class="clip" data-id="${esc(c.id)}">
      <div class="clip-media">
        <span class="clip-badge">#${c.rank}</span>
        <button class="clip-expand" type="button" data-expand="${esc(c.id)}" aria-label="Open larger preview">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/></svg>
        </button>
        <video controls playsinline preload="metadata" src="/media/${esc(f.video)}" poster="/media/${esc(f.thumbnail)}"></video>
      </div>
      <div class="clip-body">
        <div class="clip-top">
          <div style="min-width:0">
            <div class="clip-title">${esc(c.title || "Untitled clip")}</div>
            <div class="clip-meta">${fmtClock(c.start)} → ${fmtClock(c.end)} · ${Math.round(c.duration)}s</div>
          </div>
          ${ring(c.scores?.final)}
        </div>
        ${c.reason ? `<div class="clip-reason">${esc(c.reason)}</div>` : ""}
        ${c.transcript ? `<details class="tr"><summary>Transcript</summary><p>${esc(c.transcript)}</p></details>` : ""}
        <div class="clip-actions">
          <a class="btn btn-primary btn-sm" href="/media/${esc(f.video)}" download>Download MP4</a>
          ${f.captions ? `<a class="btn btn-ghost btn-sm" href="/media/${esc(f.captions)}" download>Captions</a>` : ""}
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

  function renderClips(job) {
    const grid = $("#clips");
    const clips = [...(job.clips || [])].sort((a, b) => a.rank - b.rank);

    // Real clips: add only new ones so existing videos keep playing.
    for (const c of clips) {
      state.clipData.set(c.id, c);
      if (!state.clipEls.has(c.id)) {
        const wrap = document.createElement("div");
        wrap.innerHTML = clipCard(c).trim();
        const el = wrap.firstElementChild;
        state.clipEls.set(c.id, el);
        grid.appendChild(el);
      }
    }

    // Pending placeholders are rebuilt each time at the end of the grid.
    $$(".clip-pending, .clips-empty", grid).forEach((n) => n.remove());
    const total = job.planned_clips || (job.status === "running" || job.status === "queued" ? job.params?.count || 0 : 0);
    const pendingCount = Math.max(0, total - clips.length);
    const running = job.status === "running" || job.status === "queued";
    const html = [];
    for (let i = 0; i < pendingCount; i++) {
      const index = clips.length + i + 1;
      html.push(pendingCard(index, total, i === 0 && job.stage === "clipping"));
    }
    if (!clips.length && !pendingCount) {
      html.push(job.status === "done"
        ? `<div class="clips-empty"><strong>No clips met the bar</strong>Try a longer video or a wider clip length.</div>`
        : `<div class="clips-empty"><strong>No clips yet</strong>Clips appear here one at a time as they render.</div>`);
    }
    if (html.length) grid.insertAdjacentHTML("beforeend", html.join(""));
    $("#clips-count").textContent = `${clips.length} / ${total || (running ? "…" : clips.length)}`;
  }

  function renderLog(job) {
    const el = $("#console");
    const entries = job.log || [];
    if (entries.length < state.logCount) {  // log was trimmed; start over
      el.innerHTML = "";
      state.logCount = 0;
    }
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
    const key = [job.id, job.status, titleOf(job), job.params?.count, job.params?.min_seconds, job.params?.max_seconds,
      job.params?.vertical, job.params?.captions, job.llm_used].join("|");
    if (key === state.headerKey) return;
    state.headerKey = key;
    $("#p-kicker").textContent = job.source_url ? "Video link" : "Uploaded file";
    $("#p-title").textContent = titleOf(job);
    $("#p-title").title = titleOf(job);
    const p = job.params || {};
    const chips = [
      `<span class="chip">${esc(p.min_seconds)}–${esc(p.max_seconds)}s</span>`,
      `<span class="chip">Up to ${esc(p.count)} clips</span>`,
      p.vertical ? `<span class="chip">9:16</span>` : "",
      p.captions ? `<span class="chip">Captions</span>` : "",
      job.llm_used ? `<span class="chip ai">AI ranked</span>` : "",
      job.source_duration ? `<span class="chip">Source ${fmtSpan(job.source_duration)}</span>` : "",
    ];
    $("#p-chips").innerHTML = chips.join("");
    const st = $("#p-status");
    st.className = `status ${job.status}`;
    $("#p-status-text").textContent = STATUS_LABEL[job.status] || job.status;
  }

  function renderProgress(job) {
    const pct = Math.round((job.progress || 0) * 100);
    $("#p-pct").textContent = pct;
    $("#bar").setAttribute("aria-valuenow", String(pct));
    $("#bar-fill").style.width = `${pct}%`;
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
    const box = $("#alerts");
    const parts = [];
    for (const w of job.warnings || []) {
      parts.push(`<div class="alert warn"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg><div>${esc(w)}</div></div>`);
    }
    if (job.status === "failed") {
      parts.push(`<div class="alert err"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg><div><strong>This project stopped.</strong> ${esc(job.error || "Unknown error")}<br>Check the activity log for details, then start a new project.</div></div>`);
    }
    box.innerHTML = parts.join("");
  }

  function renderProject(job) {
    state.job = job;
    $("#welcome").hidden = true;
    $("#project").hidden = false;
    renderHeader(job);
    renderProgress(job);
    renderAlerts(job);
    renderClips(job);
    renderLog(job);
    $("#live").classList.toggle("off", TERMINAL.has(job.status));
    $("#live-text").textContent = TERMINAL.has(job.status) ? "Stopped" : "Live";
  }

  function resetProjectView() {
    state.job = null;
    state.headerKey = "";
    state.alertKey = "";
    state.logCount = 0;
    state.clipEls.clear();
    state.clipData.clear();
    $("#clips").innerHTML = "";
    $("#console").innerHTML = "";
    $("#alerts").innerHTML = "";
    $("#jump").hidden = true;
    $("#clips-count").textContent = "0 / 0";
  }

  function select(id) {
    if (state.selectedId !== id) {
      state.selectedId = id;
      resetProjectView();
      state.detailLoadedFor = null;
      history.replaceState(null, "", `#job/${id}`);
      state.listKey = "";
      renderList(state.jobs);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    refreshDetail();
  }

  function clearSelection() {
    state.selectedId = null;
    resetProjectView();
    state.detailLoadedFor = null;
    history.replaceState(null, "", location.pathname);
    $("#project").hidden = true;
    $("#welcome").hidden = false;
    state.listKey = "";
    renderList(state.jobs);
  }

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
    if (state.jobs.length && !summary) { clearSelection(); return; }
    if (summary && TERMINAL.has(summary.status) && state.detailLoadedFor === id) return;
    try {
      const job = await api(`/api/jobs/${id}`);
      if (state.selectedId !== id) return;
      renderProject(job);
      if (TERMINAL.has(job.status)) state.detailLoadedFor = id;
    } catch { /* transient: try again next tick */ }
  }

  async function tick() {
    await refreshList();
    await refreshDetail();
    setTimeout(tick, 1200);
  }
  // Keep the elapsed timer moving between polls
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

  // ---------- expanded preview ----------
  const dialog = $("#preview");
  function openPreview(id) {
    const c = state.clipData.get(id);
    if (!c) return;
    const f = c.files || {};
    $("#preview-kicker").textContent = `Clip #${c.rank} · ${fmtClock(c.start)} → ${fmtClock(c.end)} · ${Math.round(c.duration)}s`;
    $("#preview-title").textContent = c.title || "Untitled clip";
    $("#preview-reason").textContent = c.reason || "";
    const v = $("#preview-video");
    v.src = `/media/${f.video}`;
    v.poster = f.thumbnail ? `/media/${f.thumbnail}` : "";
    $("#preview-download").href = `/media/${f.video}`;
    dialog.showModal();
    v.play().catch(() => {});
  }
  $("#clips").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-expand]");
    if (btn) openPreview(btn.dataset.expand);
  });
  $("#preview-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => { const v = $("#preview-video"); v.pause(); v.removeAttribute("src"); v.load(); });

  // ---------- routing ----------
  const idFromHash = () => {
    const m = location.hash.match(/^#job\/([a-z0-9]+)$/i);
    return m ? m[1] : null;
  };
  window.addEventListener("hashchange", () => {
    const id = idFromHash();
    if (id) select(id);
    else if (state.selectedId) clearSelection();
  });
  $("#new-btn").addEventListener("click", () => {
    if (state.selectedId) clearSelection();
    window.scrollTo({ top: 0, behavior: "smooth" });
    setTimeout(() => $("#url").focus({ preventScroll: true }), 350);
  });

  // ---------- boot ----------
  loadConfig();
  refreshList().then(() => {
    const id = idFromHash();
    if (id) select(id);
  });
  setTimeout(tick, 1200);
})();
