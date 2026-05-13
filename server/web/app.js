const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { current: null, sse: null };

function brandedConfirm(message, { title = "Tem certeza?", okText = "Confirmar", okClass = "" } = {}) {
  return new Promise((resolve) => {
    const root = $("#confirm-backdrop");
    if (!root) return resolve(window.confirm(message));
    $("#confirm-title").textContent = title;
    $("#confirm-message").textContent = message;
    const ok = $("#confirm-ok");
    const cancel = $("#confirm-cancel");
    ok.className = "btn-primary";
    if (okClass) ok.classList.add(okClass);
    ok.textContent = okText;
    root.classList.remove("hidden");
    const cleanup = () => {
      root.classList.add("hidden");
      ok.onclick = null;
      cancel.onclick = null;
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); cleanup(); resolve(false); }
      if (e.key === "Enter")  { e.preventDefault(); cleanup(); resolve(true); }
    };
    document.addEventListener("keydown", onKey);
    ok.onclick = () => { cleanup(); resolve(true); };
    cancel.onclick = () => { cleanup(); resolve(false); };
  });
}

function toast(msg, kind = "", duration = 2400) {
  let el = document.querySelector(".live-toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "live-toast";
    document.body.appendChild(el);
  }
  el.className = `live-toast ${kind}`;
  // Allow callers to pass HTML (our own templates use data-icon spans).
  // External text from API errors stays escaped at the call site.
  if (msg && msg.includes("<")) {
    el.innerHTML = msg;
    if (window.HFIcons) HFIcons.render(el);
  } else {
    el.textContent = msg;
  }
  requestAnimationFrame(() => el.classList.add("show"));
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), Math.max(1000, duration));
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${text}`);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

// XHR upload with progress events (fetch can't report upload progress yet
// without ReadableStream wrapping). Use for any file > a few MB.
function apiUpload(path, fd, { onProgress, timeoutMs = 30 * 60 * 1000 } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.responseType = "text";
    xhr.timeout = timeoutMs;
    if (xhr.upload && onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) onProgress({
          loaded: e.loaded, total: e.total,
          pct: e.total ? (e.loaded / e.total) : 0,
        });
      });
    }
    xhr.onload = () => {
      const ok = xhr.status >= 200 && xhr.status < 300;
      const ct = xhr.getResponseHeader("content-type") || "";
      const body = ct.includes("application/json")
        ? (() => { try { return JSON.parse(xhr.responseText); } catch { return xhr.responseText; } })()
        : xhr.responseText;
      if (ok) resolve(body);
      else reject(new Error(`${xhr.status} ${typeof body === "string" ? body : JSON.stringify(body)}`));
    };
    xhr.onerror   = () => reject(new Error("network error — verifica conexão / servidor"));
    xhr.ontimeout = () => reject(new Error("timeout no upload — arquivo muito grande ou rede lenta"));
    xhr.onabort   = () => reject(new Error("upload cancelado"));
    xhr.send(fd);
  });
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${u[i]}`;
}

const MEDIA_ROLE_ICON = {
  source: "video", angle: "film", clip: "video", graded: "sliders",
  cut: "scissors", roughcut: "wand-2", highlights: "sparkles",
  preview: "eye", hook: "zap", export: "package",
};
const MEDIA_ROLE_LABEL = {
  source: "Source", angle: "Multicam", clip: "Vlog",
  graded: "Edição", cut: "Edição", roughcut: "Rough", highlights: "Highlights",
  preview: "Preview", hook: "Hook", export: "Export",
};

async function refreshMediaList() {
  const card = $("#media-card");
  if (!card || !state.current) return;
  const list = $("#media-list");
  const pathEl = $("#media-project-path");
  list.innerHTML = `<div class="muted" style="padding:12px">carregando…</div>`;
  try {
    const data = await api(`/api/projects/${state.current.id}/media`);
    pathEl.textContent = data.project_dir;
    pathEl.dataset.path = data.project_dir;
    state.media = data;
    if (!data.items.length) {
      list.innerHTML = `<div class="muted" style="padding:12px">Nada subido ainda.</div>`;
      return;
    }
    list.innerHTML = `<table class="media-table">
      <thead><tr><th></th><th>Arquivo</th><th>Tipo</th><th>Dur</th><th>Tamanho</th><th></th></tr></thead>
      <tbody>${data.items.map((it, i) => `<tr data-i="${i}">
        <td class="m-icon"><span data-icon="${MEDIA_ROLE_ICON[it.role] || 'file-text'}"></span></td>
        <td>
          <strong>${escapeHtml(it.label || it.name)}</strong>
          ${it.status === "processing" ? ` <span class="pill" data-tone="warn">processando</span>` : ""}
          ${it.status === "error" ? ` <span class="pill" data-tone="danger" title="${escapeHtml(it.error || '')}">erro</span>` : ""}
          <div class="muted m-path"><code>${escapeHtml(it.rel)}</code></div>
        </td>
        <td><span class="pill" data-tone="neutral">${escapeHtml(MEDIA_ROLE_LABEL[it.role] || it.role)}</span></td>
        <td>${it.duration ? `${it.duration.toFixed(1)}s` : "—"}</td>
        <td>${it.size ? fmtBytes(it.size) : "—"}</td>
        <td class="m-actions">
          <a class="btn-icon btn-sm" href="${it.url}" target="_blank" title="Abrir no navegador"><span data-icon="play"></span></a>
          <a class="btn-icon btn-sm" href="${it.url}" download title="Baixar"><span data-icon="download"></span></a>
          <button class="btn-icon btn-sm" data-act="reveal" data-path="${escapeHtml(it.path)}" title="Abrir no Finder"><span data-icon="arrow-right"></span></button>
          <button class="btn-icon btn-sm" data-act="copy" data-path="${escapeHtml(it.path)}" title="Copiar caminho"><span data-icon="copy"></span></button>
        </td>
      </tr>`).join("")}</tbody>
    </table>`;
    if (window.HFIcons) HFIcons.render(list);
    list.querySelectorAll("button[data-act]").forEach(btn => {
      btn.addEventListener("click", () => {
        const act = btn.dataset.act;
        const path = btn.dataset.path;
        if (act === "copy") copyToClipboard(path);
        else if (act === "reveal") revealInFinder(path);
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="err" style="padding:12px">Falha: ${escapeHtml(e.message)}</div>`;
  }
}

async function revealInFinder(path) {
  if (!state.current) return;
  try {
    await api(`/api/projects/${state.current.id}/reveal`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ path: path || null }),
    });
    toast?.("Abrindo no Finder…", "ok");
  } catch (e) {
    log(`✗ reveal: ${e.message}`, "err");
  }
}

function copyToClipboard(text) {
  if (!text) return;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(
      () => toast?.("Caminho copiado", "ok"),
      () => fallbackCopy(text),
    );
  } else {
    fallbackCopy(text);
  }
}
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); toast?.("Caminho copiado", "ok"); } catch {}
  document.body.removeChild(ta);
}

function log(msg, kind = "") {
  const el = $("#pipeline-log");
  if (!el) return;
  const ts = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  line.className = kind;
  // `msg` is allowed to contain our own data-icon spans (kind="ok" etc.).
  // We don't run user-supplied text through innerHTML, so XSS surface
  // stays at "self-authored markup + decoded filename/error strings",
  // which is acceptable for a local tool. HFIcons.render hydrates spans.
  line.innerHTML = `[${ts}] ${msg}`;
  el.appendChild(line);
  if (window.HFIcons) HFIcons.render(line);
  el.scrollTop = el.scrollHeight;
}

// Step-by-step status panel used inside the Multicam section so users
// see exactly what's missing before "Escolher câmera por turno" works.
function renderMulticamChecklist(p) {
  const root = $("#multicam-checklist");
  if (!root || !p) return;
  const steps = [
    {
      key: "source", label: "Vídeo de origem",
      done: !!p.source_filename, action: null,
      hint: p.source_filename ? p.source_filename : "Suba o vídeo principal em Captura",
    },
    {
      key: "angles", label: "Câmeras adicionais",
      done: (p.angles || []).length >= 1,
      action: null,
      hint: (p.angles || []).length
        ? `${(p.angles || []).length} ângulo(s)`
        : "Use 'Subir câmera' abaixo (B, C…)",
    },
    {
      key: "sync", label: "Sincronizar áudio",
      done: (p.angles || []).some(a => typeof a.audio_offset === "number"),
      action: { id: "multicam-sync-btn", label: "Sincronizar" },
      hint: "Alinha as câmeras pelo áudio comum",
    },
    {
      key: "transcribe", label: "Transcrição",
      done: !!p.has_transcript,
      action: { id: "run-transcribe", label: "Transcrever", run: runTranscribeQuick },
      hint: "Whisper extrai a fala — base do split por speaker",
    },
    {
      key: "speakers", label: "Detectar falas (speakers)",
      done: !!p.has_speakers,
      action: { id: "run-speakers", label: "Detectar", run: detectSpeakersQuick },
      hint: "Identifica quem fala quando — define cada turno",
    },
    {
      key: "pick", label: "Escolher câmera por turno",
      done: !!p.has_camera_plan,
      action: { id: "multicam-pick-btn", label: "Escolher" },
      hint: "IA atribui a melhor câmera a cada turno",
    },
    {
      key: "render", label: "Renderizar multicam",
      done: !!(p.last_export || "").includes("multicam"),
      action: { id: "multicam-render-btn", label: "Renderizar" },
      hint: "Produz o MP4 final com os cortes aplicados",
    },
  ];
  // Find the first not-done step → highlight it so the next action is obvious.
  const nextIdx = steps.findIndex(s => !s.done);
  root.innerHTML = `<div class="cl-title">Pipeline multicam podcast</div>
    <ol class="cl-steps">
      ${steps.map((s, i) => {
        const state = s.done ? "done" : (i === nextIdx ? "now" : "pending");
        const icon = s.done ? "check" : (i === nextIdx ? "arrow-right" : "chevron-right");
        const cta = (!s.done && s.action)
          ? `<button class="btn btn-soft btn-sm cl-cta" data-step="${s.key}">${escapeHtml(s.action.label)}</button>`
          : "";
        return `<li class="cl-step cl-${state}">
          <span class="cl-mark"><span data-icon="${icon}" data-icon-size="14"></span></span>
          <span class="cl-body">
            <span class="cl-label">${escapeHtml(s.label)}</span>
            <span class="cl-hint">${escapeHtml(s.hint)}</span>
          </span>
          ${cta}
        </li>`;
      }).join("")}
    </ol>`;
  if (window.HFIcons) HFIcons.render(root);
  // Wire each CTA → click the corresponding control / run the inline fn.
  for (const btn of root.querySelectorAll(".cl-cta")) {
    const key = btn.dataset.step;
    const step = steps.find(s => s.key === key);
    btn.addEventListener("click", () => {
      if (!step?.action) return;
      if (step.action.run) return step.action.run();
      const target = document.getElementById(step.action.id);
      if (target && !target.disabled) target.click();
    });
  }
}

async function runTranscribeQuick() {
  if (!state.current) return;
  toast?.("Transcrição iniciada…", "ok");
  try {
    await api(`/api/projects/${state.current.id}/transcribe`, { method: "POST" });
    await loadProject(state.current.id);
    toast?.("Transcrição pronta", "ok");
  } catch (e) {
    toast?.(`Falha: ${e.message}`, "err");
  }
}

async function detectSpeakersQuick() {
  if (!state.current) return;
  toast?.("Detectando speakers…", "ok");
  try {
    await api(`/api/projects/${state.current.id}/speakers`, { method: "POST" });
    await loadProject(state.current.id);
    toast?.("Speakers detectados", "ok");
  } catch (e) {
    toast?.(`Falha: ${e.message}`, "err");
  }
}

// Set a button label that may include data-icon spans. textContent would
// render the markup literally; this hydrates icons after assignment.
function setBtnHTML(btn, html) {
  if (!btn) return;
  btn.innerHTML = html;
  if (window.HFIcons) HFIcons.render(btn);
}

async function refreshList() {
  const list = await api("/api/projects");
  const root = $("#project-list");
  root.innerHTML = "";
  for (const p of list) {
    const btn = document.createElement("button");
    btn.className = "project-item" + (state.current?.id === p.id ? " active" : "");
    btn.innerHTML = `
      <div class="pi-name">${escapeHtml(p.name)}</div>
      <div class="pi-meta"><span class="dot ${p.has_render ? "ok" : ""}"></span>${new Date(p.updated_at).toLocaleString()}</div>
    `;
    btn.onclick = () => loadProject(p.id);
    root.appendChild(btn);
  }
  renderEmptyRecents(list);
  refreshStats();
}

function renderEmptyRecents(list) {
  const root = $("#empty-recents");
  if (!root) return;
  if (!list || list.length === 0) {
    root.innerHTML = "";
    return;
  }
  const recent = list.slice(0, 6);
  root.innerHTML = `
    <div class="empty-recents-head">
      <span class="eyebrow">Projetos recentes</span>
      <span class="muted">${list.length} no total</span>
    </div>
    <div class="empty-recents-grid">
      ${recent.map(p => `
        <button class="recent-card" data-pid="${p.id}">
          <div class="recent-card-name">${escapeHtml(p.name)}</div>
          <div class="recent-card-meta">
            <span class="pill" data-tone="${p.has_render ? "success" : "neutral"}">${p.has_render ? "render" : "draft"}</span>
            <span class="muted">${new Date(p.updated_at).toLocaleDateString()}</span>
          </div>
        </button>
      `).join("")}
    </div>`;
  for (const btn of root.querySelectorAll(".recent-card")) {
    btn.addEventListener("click", () => loadProject(btn.dataset.pid));
  }
}

async function refreshStats() {
  try {
    const s = await api("/api/stats");
    const gb = (b) => `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`;
    $("#stats-bar").innerHTML = `
      <div class="row"><span>Projetos</span><strong>${s.projects}</strong></div>
      <div class="row"><span>Soundbites</span><strong>${s.soundbites}</strong></div>
      <div class="row"><span>Renders</span><strong>${s.renders}</strong></div>
      <div class="row"><span>Em uso</span><strong>${gb(s.bytes_used)}</strong></div>
      <div class="row"><span>Disco livre</span><strong>${gb(s.disk_free)}</strong></div>
    `;
  } catch {}
}

let _searchTimer = null;
async function runSearch(q) {
  const root = $("#search-results");
  if (!q || q.length < 2) {
    root.innerHTML = "";
    return;
  }
  try {
    const results = await api(`/api/search?q=${encodeURIComponent(q)}&limit=20`);
    root.innerHTML = "";
    for (const r of results) {
      const div = document.createElement("div");
      div.className = "sr-item";
      div.innerHTML = `
        <div class="sr-proj">${escapeHtml(r.project_name)}</div>
        <div class="sr-text">${escapeHtml(r.soundbite.text || r.soundbite.summary || "")}</div>
        <div class="sr-meta">${r.soundbite.start.toFixed(1)}s · score ${r.soundbite.score}</div>
      `;
      div.onclick = () => loadProject(r.project_id);
      root.appendChild(div);
    }
  } catch (e) {
    root.innerHTML = `<div class="sr-item">${escapeHtml(e.message)}</div>`;
  }
}

async function loadProject(id) {
  const p = await api(`/api/projects/${id}`);
  state.current = p;
  // Persist so a refresh / share-link lands back on the same project.
  try { localStorage.setItem("hfvp.current_project", id); } catch {}
  if (location.hash !== `#p/${id}`) {
    history.replaceState(null, "", `#p/${id}`);
  }
  attachEventStream(id);
  renderStaleWarnings(p);
  renderCutsTimeline(p);
  renderProgressBar(p);
  $("#cancel-render-btn").style.display = p.render_active ? "inline-block" : "none";
  $("#empty").classList.add("hidden");
  $("#project-view").classList.remove("hidden");
  $("#project-name").textContent = p.name;
  $("#topbar-project-name").textContent = p.name;
  refreshHeader();
  refreshMediaList();
  renderMulticamChecklist(p);
  const etaEl = document.getElementById("podcast-1click-eta");
  if (etaEl) etaEl.textContent = podcastEtaHint(p);
  applyModeFiltering();  // updates topbar kind pill + section visibility

  const preview = $("#preview");
  if (p.source_filename) {
    preview.src = `/api/projects/${p.id}/files/source.mp4?t=${Date.now()}`;
    preview.load();
    $("#upload-status").textContent = `${p.source_filename} · ${p.source_duration?.toFixed?.(2) || 0}s`;
    $("#upload-status").className = "status ok";
  } else {
    // Aggressive reset — some browsers keep the last frame on screen
    // after removeAttribute("src"), which made switching projects look
    // like the previous source had carried over.
    preview.pause();
    preview.removeAttribute("src");
    preview.src = "";
    preview.load();
    $("#upload-status").textContent = "Aguardando vídeo";
    $("#upload-status").className = "status muted";
  }

  // brand
  try {
    const b = await api(`/api/projects/${p.id}/brand`);
    fillBrandForm(b);
  } catch {}
  await refreshBrandPresets();
  await refreshSnapshots();

  $("#lut-status").textContent = p.has_lut ? `LUT: ${p.lut_filename}` : "Sem LUT";
  $("#lut-status").className = p.has_lut ? "status ok" : "status muted";

  // stages
  for (const stage of $$(".stage")) {
    const name = stage.dataset.stage;
    stage.classList.remove("done", "running", "error");
    const s = p.stages?.[name];
    if (s) {
      stage.classList.add(s.status);
      const sub = stage.querySelector(".stage-sub");
      if (s.message) sub.textContent = s.message;
    }
  }

  // result preview: prefer the most recent video export from history,
  // fall back to has_render + last_export, else show a placeholder.
  await refreshExportPreview(p);
  refreshOverview();
  applyPrereqs();
  refreshNleExport();
  annotateSourcePickers(p);
  syncColorProfileSelect(p);

  renderAngles(p);
  renderMusicSuggestion(p.music_suggestion);
  await loadSoundbitesAndStory(p);
  await loadTranscriptWords(p);
  await refreshTemplates();
  await refreshHistory();
  await refreshShortsGallery();
  await loadClips();

  await refreshList();
}

async function loadSoundbitesAndStory(p) {
  $("#topics-list").innerHTML = "";
  $("#story-summary").classList.add("hidden");

  if (p.has_soundbites) {
    try {
      const a = await api(`/api/projects/${p.id}/soundbites`);
      renderSoundbites(a);
    } catch {}
  }
  if (p.has_story) {
    try {
      const s = await api(`/api/projects/${p.id}/story`);
      renderStory(s);
    } catch {}
  }
  if (p.has_roughcut) {
    const v = $("#roughcut-video");
    v.classList.remove("hidden");
    v.src = `/api/projects/${p.id}/files/roughcut.mp4`;
    $("#roughcut-status").textContent = "rough cut pronto";
    $("#roughcut-status").className = "status ok";
  } else {
    $("#roughcut-video").classList.add("hidden");
    $("#roughcut-status").textContent = "";
  }
}

function renderSoundbites(analysis, thumbs) {
  const root = $("#topics-list");
  root.innerHTML = "";
  const byTopic = {};
  for (const sb of analysis.soundbites || []) {
    (byTopic[sb.topic] = byTopic[sb.topic] || []).push(sb);
  }
  const thumbByBite = {};
  for (const t of (thumbs || [])) thumbByBite[t.id] = t.url;
  for (const t of analysis.topics || []) {
    const card = document.createElement("div");
    card.className = "topic-card";
    const bites = (byTopic[t.id] || []).sort((a, b) => b.score - a.score);
    card.innerHTML = `
      <div class="topic-head">
        <span class="topic-name">${escapeHtml(t.name)}</span>
        <span class="topic-summary">${escapeHtml(t.summary || "")}</span>
      </div>
      <div class="bites-list">
        ${bites.map(b => {
          const thumb = thumbByBite[b.id]
            ? `<img class="bite-thumb" src="${thumb_safe(thumbByBite[b.id])}" alt="" loading="lazy"/>`
            : "";
          return `
          <label class="bite${thumb ? ' with-thumb' : ''}">
            <input type="checkbox" data-bite="${b.id}" />
            ${thumb}
            <div class="bite-body">
              <div class="bite-meta">
                <span class="bite-time" data-seek="${b.start}">${b.start.toFixed(1)}s – ${b.end.toFixed(1)}s</span>
                <span class="bite-score ${b.score >= 80 ? 'high' : b.score >= 60 ? 'mid' : 'low'}">${b.score}</span>
                ${b.summary ? `<span>${escapeHtml(b.summary)}</span>` : ""}
              </div>
              <div class="bite-text">${escapeHtml(b.text)}</div>
            </div>
          </label>
        `;}).join("")}
      </div>
    `;
    root.appendChild(card);
  }
  // wire seek-on-click
  for (const el of root.querySelectorAll(".bite-time")) {
    el.style.cursor = "pointer";
    el.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      const t = parseFloat(el.dataset.seek);
      const v = $("#preview");
      if (v && !isNaN(t)) { v.currentTime = t; v.play().catch(() => {}); }
    });
  }
}
function thumb_safe(u) { return u.replace(/"/g, "%22"); }

async function generateBiteThumbs() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> bite thumbs");
  try {
    const r = await api(`/api/projects/${state.current.id}/bite-thumbnails`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> ${r.thumbs.length} thumbs`, "ok");
    // re-render soundbites with thumbs
    const a = await api(`/api/projects/${state.current.id}/soundbites`);
    renderSoundbites(a, r.thumbs);
  } catch (e) {
    log(`✗ bite-thumbs: ${e.message}`, "err");
  }
}

function renderStory(s) {
  const root = $("#story-summary");
  root.classList.remove("hidden");
  root.innerHTML = `
    <div class="ss-title">${escapeHtml(s.title || "Roteiro")}</div>
    <div class="ss-logline">${escapeHtml(s.logline || "")}</div>
    <div class="chapters">
      ${(s.chapters || []).map(c => `
        <div class="chapter">
          <div class="chapter-head">
            <span class="chapter-name">${escapeHtml(c.name)}</span>
            <span class="chapter-meta">${c.soundbite_ids.length} bites · ${escapeHtml(c.transition_note || "")}</span>
          </div>
          <div class="chapter-summary">${escapeHtml(c.summary || "")}</div>
        </div>
      `).join("")}
    </div>
  `;
  // mark soundbites used by the story as checked
  for (const c of s.chapters || []) {
    for (const sid of c.soundbite_ids) {
      const cb = document.querySelector(`input[data-bite="${sid}"]`);
      if (cb) cb.checked = true;
    }
  }
}

function renderAngles(p) {
  const list = $("#angle-list");
  list.innerHTML = "";
  for (const a of p.angles || []) {
    const li = document.createElement("li");
    const tags = a.tags?.tags || [];
    const qc = a.quality_check || {};
    const qcBadge = qc.quality && qc.quality !== "ok"
      ? ` <span class="tag warn"><span data-icon=&quot;alert-triangle&quot;></span> ${qc.quality}</span>` : (qc.quality === "ok" ? ' <span class="tag ok"><span data-icon=&quot;check&quot;></span> ok</span>' : '');
    const fa = a.face_analysis || {};
    const shotBadge = fa.shot_type
      ? ` <span class="tag">${escapeHtml(fa.shot_type.replace('_', ' '))}</span>`
      : '';
    const subjBadge = fa.subject_change
      ? ' <span class="tag warn">⇄ sujeito mudou</span>' : '';
    const offset = a.audio_offset != null ? ` · sync ${a.audio_offset > 0 ? "+" : ""}${a.audio_offset.toFixed(2)}s` : '';
    li.innerHTML = `
      <span class="a-name">${escapeHtml(a.name)}${qcBadge}${shotBadge}${subjBadge}</span>
      <span class="a-meta">${(a.duration || 0).toFixed(1)}s · ${a.filename}${a.tags?.summary ? ' · ' + escapeHtml(a.tags.summary) : ''}${offset}</span>
      ${tags.length ? `<span class="a-tags">${tags.slice(0, 5).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join("")}</span>` : ""}
      <span class="a-actions">
        <button class="btn-ghost a-tag" data-idx="${a.index}">${a.tags ? "🔄 Re-taggear" : "<span data-icon=&quot;tag&quot;></span> Tag IA"}</button>
        <button class="a-del" data-idx="${a.index}">×</button>
      </span>
    `;
    li.querySelector(".a-del").onclick = async () => {
      await api(`/api/projects/${p.id}/angles/${a.index}`, { method: "DELETE" });
      log(`Ângulo ${a.name} removido`, "ok");
      await loadProject(p.id);
    };
    li.querySelector(".a-tag").onclick = async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setBtnHTML(btn, "<span data-icon=&quot;wand-2&quot;></span> Analisando...");
      try {
        const updated = await api(`/api/projects/${p.id}/angles/${a.index}/tag`, { method: "POST" });
        log(`<span data-icon=&quot;check&quot;></span> Ângulo "${updated.name}" → ${updated.tags.summary}`, "ok");
        await loadProject(p.id);
      } catch (err) {
        log(`✗ tag: ${err.message}`, "err");
        btn.disabled = false;
        setBtnHTML(btn, "<span data-icon=&quot;tag&quot;></span> Tag IA");
      }
    };
    list.appendChild(li);
  }
}

function renderMusicSuggestion(s) {
  const root = $("#music-suggestion");
  if (!s) {
    root.classList.add("hidden");
    return;
  }
  root.classList.remove("hidden");
  const tag = (text, cls = "") => `<span class="tag ${cls}">${escapeHtml(text)}</span>`;
  root.innerHTML = `
    <div class="ms-desc">${escapeHtml(s.description || "")}</div>
    <div>${(s.mood || []).map(m => tag(m, "mood")).join("")}</div>
    <div>${(s.genres || []).map(g => tag(g, "genre")).join("")}</div>
    <div>${tag(`${s.bpm_min}–${s.bpm_max} BPM`, "bpm")} ${tag(`Energia: ${s.energy}`)}</div>
    <div>${(s.instruments || []).map(i => tag(i)).join("")}</div>
    <div>${(s.keywords || []).map(k => tag(k)).join("")}</div>
  `;
  if (s.epidemic_search_url) {
    const link = $("#epidemic-link");
    link.href = s.epidemic_search_url;
    link.style.display = "inline-block";
    link.textContent = "🔗 Abrir busca no site";
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/**
 * Show the most recent video export in the #result <video> preview.
 *
 * Sources, in priority order:
 *   1. The most recent .mp4/.mov/.webm entry in /api/projects/{id}/history
 *      (covers burn-captions, highlights, hook, vlog, mix, shorts, reframe, ...)
 *   2. has_render + last_export (legacy fallback)
 *   3. Hide preview and show "Nada exportado ainda" placeholder
 */
async function refreshExportPreview(p) {
  p = p || state.current;
  if (!p) return;
  const video = $("#result");
  const placeholder = $("#result-empty");
  const downloadLink = $("#download-link");
  if (!video) return;

  let url = null;
  let label = null;
  try {
    const history = await api(`/api/projects/${p.id}/history`);
    const videoExt = /\.(mp4|mov|webm)$/i;
    const latest = (history || []).filter(h => h.name && videoExt.test(h.name)).slice(-1)[0];
    if (latest) {
      url = latest.url;
      label = latest.name;
    }
  } catch {}

  if (!url && p.has_render && p.last_export) {
    url = `/api/projects/${p.id}/exports/${p.last_export}`;
    label = p.last_export;
  }

  if (url) {
    if (video.getAttribute("src") !== url) {
      video.src = url;
      video.load?.();
    }
    video.style.display = "";
    if (placeholder) placeholder.style.display = "none";
    if (downloadLink) {
      downloadLink.href = url;
      downloadLink.style.opacity = 1;
      downloadLink.style.pointerEvents = "auto";
      if (label) setBtnHTML(downloadLink, `<span data-icon=&quot;download&quot;></span> ${label}`);
    }
  } else {
    video.removeAttribute("src");
    video.style.display = "none";
    if (placeholder) placeholder.style.display = "";
    if (downloadLink) {
      downloadLink.href = "#";
      downloadLink.style.opacity = 0.5;
      downloadLink.style.pointerEvents = "none";
    }
  }
}

function attachEventStream(pid) {
  if (state.sse) {
    state.sse.close();
    state.sse = null;
  }
  let stateRefreshTimer = null;
  const es = new EventSource(`/api/projects/${pid}/events`);
  es.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    if (data.type === "stage") {
      const st = document.querySelector(`.stage[data-stage="${data.stage}"]`);
      if (st) {
        st.classList.remove("running", "done", "error");
        st.classList.add(data.status);
        const sub = st.querySelector(".stage-sub");
        if (data.message && sub) sub.textContent = data.message;
        // progress bar
        let bar = st.querySelector(".stage-progress");
        if (!bar) {
          bar = document.createElement("div");
          bar.className = "stage-progress";
          bar.style.width = "0%";
          st.appendChild(bar);
        }
        if (data.status === "running") {
          if (typeof data.progress === "number") {
            bar.style.width = `${Math.round(data.progress * 100)}%`;
          }
        } else if (data.status === "done") {
          bar.style.width = "100%";
          setTimeout(() => bar.style.width = "0%", 800);
        } else if (data.status === "error") {
          bar.style.width = "0%";
        }
      }
      if (data.status === "done") toast(`<span data-icon=&quot;check&quot;></span> ${data.stage}: ${data.message || ""}`, "ok");
      if (data.status === "error") toast(`✗ ${data.stage}: ${data.message || ""}`, "error");
      if (data.status === "running") {
        if (data.stage === "render") {
          $("#cancel-render-btn").style.display = "inline-block";
        }
        if (typeof data.progress !== "number") toast(`<span data-icon=&quot;play&quot;></span> ${data.stage}…`);
      }
    } else if (data.type === "log") {
      log(data.message, data.level === "error" ? "err" : data.level === "ok" ? "ok" : "");
    } else if (data.type === "job") {
      // vlog 1-click pipeline
      if (state.vlogPipelineJobId && data.job_id === state.vlogPipelineJobId) {
        const status = $("#vlog-status");
        const btn = $("#vlog-auto-btn");
        if (status) {
          status.textContent = data.message || data.status;
          status.className = data.status === "error" ? "status error" :
                            data.status === "done" ? "status ok" : "status warn";
        }
        if (data.status === "done") {
          if (btn) btn.disabled = false;
          state.vlogPipelineJobId = null;
          loadClips();
          // narratives.json now persisted — refetch
          api(`/api/projects/${state.current.id}/files/vlog_narratives.json`)
            .then(n => renderNarratives(n.narratives || [])).catch(() => {});
          toast("Pipeline pronto — escolha uma narrativa", "ok");
        } else if (data.status === "error" || data.status === "cancelled") {
          if (btn) btn.disabled = false;
          state.vlogPipelineJobId = null;
        }
      }
      // vlog batch transcribe
      if (state.vlogTranscribeJobId && data.job_id === state.vlogTranscribeJobId) {
        if (data.status === "done") {
          state.vlogTranscribeJobId = null;
          toast("Transcrição em lote pronta", "ok");
          loadClips();
        } else if (data.status === "error" || data.status === "cancelled") {
          state.vlogTranscribeJobId = null;
          toast(`Transcrição em lote ${data.status}`, "error");
        }
      }
      // shorts batch UI
      if (state.shortsJobId && data.job_id === state.shortsJobId) {
        const btn = $("#shorts-btn");
        if (data.status === "done") {
          btn.disabled = false;
          state.shortsJobId = null;
          toast("Shorts prontos", "ok");
          refreshShortsGallery();
        } else if (data.status === "error" || data.status === "cancelled") {
          btn.disabled = false;
          state.shortsJobId = null;
          toast(`Shorts ${data.status}`, "error");
        }
      }
      // job snapshot — keep the podcast pipeline UI in sync if it's ours
      if (state.podcastJobId && data.job_id === state.podcastJobId) {
        const fill = $("#podcast-fill");
        const label = $("#podcast-label");
        const btn = $("#podcast-pipeline-btn");
        if (fill && typeof data.progress === "number") fill.style.width = `${Math.round(data.progress * 100)}%`;
        if (label) label.textContent = data.message || data.status;
        if (data.status === "done") {
          btn.disabled = false;
          toast("Pipeline pronto", "ok");
          loadProject(state.current.id);
          state.podcastJobId = null;
        } else if (data.status === "error" || data.status === "cancelled") {
          btn.disabled = false;
          toast(`Pipeline ${data.status}: ${data.message || ""}`, "error");
          state.podcastJobId = null;
        }
      }
    } else if (data.type === "state") {
      // refresh state.current debounced — avoid hammering on bursts
      if (stateRefreshTimer) return;
      stateRefreshTimer = setTimeout(async () => {
        stateRefreshTimer = null;
        if (state.current?.id !== pid) return;
        try {
          const p = await api(`/api/projects/${pid}`);
          state.current = p;
          renderAngles(p);
          renderMusicSuggestion(p.music_suggestion);
          await loadSoundbitesAndStory(p);
        } catch {}
      }, 250);
    }
  };
  es.onerror = () => {
    // browser will auto-reconnect
  };
  state.sse = es;
}

async function newProject() {
  const choice = await openCreateProjectModal();
  if (!choice) return;
  const p = await api("/api/projects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name: choice.name, kind: choice.kind }),
  });
  await refreshList();
  await loadProject(p.id);
}

// Resolves with { name, kind } or null when the user cancels.
function openCreateProjectModal() {
  return new Promise((resolve) => {
    const backdrop = document.getElementById("create-project-backdrop");
    const nameInput = document.getElementById("cp-name");
    const createBtn = document.getElementById("cp-create");
    const cancelBtn = document.getElementById("cp-cancel");
    const closeBtn  = document.getElementById("cp-close");
    const advanced  = document.getElementById("cp-advanced");
    const cards = backdrop.querySelectorAll(".kind-card");
    if (!backdrop || !nameInput) return resolve(null);

    let selectedKind = null;

    function syncEnabled() {
      const ok = nameInput.value.trim().length > 0 && !!selectedKind;
      createBtn.disabled = !ok;
    }
    function selectKind(kind) {
      selectedKind = kind;
      for (const c of cards) c.classList.toggle("selected", c.dataset.kind === kind);
      advanced.classList.toggle("selected", kind === "general");
      syncEnabled();
    }

    nameInput.value = "Meu vídeo";
    selectedKind = null;
    syncEnabled();
    backdrop.classList.remove("hidden");
    setTimeout(() => { nameInput.focus(); nameInput.select(); }, 50);

    function cleanup(result) {
      backdrop.classList.add("hidden");
      nameInput.removeEventListener("input", syncEnabled);
      cards.forEach(c => c.removeEventListener("click", onCard));
      advanced.removeEventListener("click", onAdvanced);
      createBtn.removeEventListener("click", onCreate);
      cancelBtn.removeEventListener("click", onCancel);
      closeBtn.removeEventListener("click", onCancel);
      backdrop.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKey);
      resolve(result);
    }
    function onCard(e)     { selectKind(e.currentTarget.dataset.kind); }
    function onAdvanced()  { selectKind("general"); }
    function onCreate()    {
      if (createBtn.disabled) return;
      cleanup({ name: nameInput.value.trim(), kind: selectedKind });
    }
    function onCancel()    { cleanup(null); }
    function onBackdrop(e) { if (e.target === backdrop) cleanup(null); }
    function onKey(e)      {
      if (e.key === "Escape") cleanup(null);
      if (e.key === "Enter" && !createBtn.disabled) onCreate();
    }

    nameInput.addEventListener("input", syncEnabled);
    cards.forEach(c => c.addEventListener("click", onCard));
    advanced.addEventListener("click", onAdvanced);
    createBtn.addEventListener("click", onCreate);
    cancelBtn.addEventListener("click", onCancel);
    closeBtn.addEventListener("click", onCancel);
    backdrop.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKey);
    if (window.HFIcons) HFIcons.render(backdrop);
  });
}

async function uploadFile(file) {
  if (!state.current) return;
  // Guard against silently overwriting the source — common pitfall for
  // multicam workflows where the user thinks every drop adds a new clip.
  if (state.current.source_filename) {
    const choice = await brandedConfirm(
      `Já existe um vídeo principal ("${state.current.source_filename}"). Subir "${file.name}" vai substituí-lo. ` +
      `Se você quer adicionar uma segunda câmera, use a seção <strong>Multicam → Subir ângulo</strong>.`,
      { title: "Substituir o vídeo de origem?", okText: "Substituir", cancelText: "Cancelar" }
    );
    if (!choice) {
      $("#upload-status").textContent = "Cancelado — use Multicam pra ângulos extras.";
      $("#upload-status").className = "status muted";
      return;
    }
  }
  const fd = new FormData();
  fd.append("file", file);
  const el = $("#pipeline-log");
  const line = document.createElement("div");
  line.innerHTML = `<span data-icon="upload-cloud"></span> Subindo ${file.name} (${fmtBytes(file.size)}) — 0%`;
  el?.appendChild(line);
  el && (el.scrollTop = el.scrollHeight);
  if (window.HFIcons) HFIcons.render(line);
  $("#upload-status").textContent = `Enviando ${file.name}...`;
  $("#upload-status").className = "status warn";
  try {
    await apiUpload(`/api/projects/${state.current.id}/upload`, fd, {
      onProgress: ({ loaded, total, pct }) => {
        line.innerHTML = `<span data-icon="upload-cloud"></span> Subindo ${file.name} — ${(pct * 100).toFixed(0)}% (${fmtBytes(loaded)} / ${fmtBytes(total)})`;
        if (window.HFIcons) HFIcons.render(line);
      },
    });
    line.classList.add("ok");
    line.innerHTML = `<span data-icon="check"></span> Upload concluído: ${file.name}`;
    if (window.HFIcons) HFIcons.render(line);
    $("#upload-status").textContent = `OK`;
    $("#upload-status").className = "status ok";
    await loadProject(state.current.id);
  } catch (e) {
    line.classList.add("err");
    line.innerHTML = `<span data-icon="alert-triangle"></span> Upload erro: ${e.message}`;
    if (window.HFIcons) HFIcons.render(line);
    $("#upload-status").textContent = `Falha: ${e.message}`;
    $("#upload-status").className = "status error";
  }
}

async function uploadLut(file) {
  if (!state.current) return;
  const fd = new FormData();
  fd.append("file", file);
  // Clear "uploading" state — easy for the user to think nothing happened
  // because the LUT just sits as a file until the pipeline applies it.
  const status = document.getElementById("lut-status");
  if (status) {
    status.textContent = `Subindo LUT: ${file.name}…`;
    status.className = "status warn";
  }
  try {
    await api(`/api/projects/${state.current.id}/lut`, { method: "POST", body: fd });
    log(`LUT enviada: ${file.name}`, "ok");
    toast?.(`LUT salva: ${file.name}. Será aplicada quando você editar o vídeo.`, "ok", 4500);
    await loadProject(state.current.id);
  } catch (e) {
    log(`LUT erro: ${e.message}`, "err");
    toast?.(`LUT falhou: ${e.message}`, "err");
    if (status) {
      status.textContent = `Falha: ${e.message}`;
      status.className = "status error";
    }
  }
}

async function saveBrand() {
  if (!state.current) return;
  const body = collectBrand();
  try {
    await api(`/api/projects/${state.current.id}/brand`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    $("#brand-status").textContent = "Brand salvo";
    $("#brand-status").className = "status ok";
    log("Brand atualizado", "ok");
  } catch (e) {
    $("#brand-status").textContent = e.message;
    $("#brand-status").className = "status error";
  }
}

async function runStage(name) {
  if (!state.current) return;
  const stage = document.querySelector(`.stage[data-stage="${name}"]`);
  stage?.classList.add("running");
  log(`<span data-icon=&quot;play&quot;></span> ${name}`);
  try {
    let body = {};
    let path = `/api/projects/${state.current.id}/${{
      transcribe: "transcribe",
      silence: "cut-silences",
      fillers: "cut-fillers",
      apply: "apply",
      render: "render",
    }[name]}`;
    if (name === "silence") {
      body = {
        noise_db: parseFloat($("#opt-noise").value),
        min_silence: parseFloat($("#opt-min").value),
        pad: 0.08,
      };
    } else if (name === "fillers") {
      const custom = ($("#opt-filler-custom")?.value || "")
        .split(/[,;]+/).map(s => s.trim()).filter(Boolean);
      body = {
        language: $("#opt-filler-lang").value,
        custom: custom.length ? custom : null,
        pad: 0.04,
      };
    } else if (name === "apply") {
      body = {
        loudnorm: $("#opt-loudnorm")?.checked || false,
        denoise: $("#opt-denoise")?.checked || false,
      };
    } else if (name === "render") {
      body = {
        aspect: $("#render-aspect").value,
        include_captions: true,
        source: $("#render-source")?.value || "graded",
        include_chapter_cards: $("#render-chapters")?.checked || false,
      };
    }
    const res = await api(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    log(`<span data-icon=&quot;check&quot;></span> ${name}: ${JSON.stringify(res).slice(0, 200)}`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    stage?.classList.remove("running");
    stage?.classList.add("error");
    log(`✗ ${name}: ${e.message}`, "err");
  }
}

async function extractSoundbites() {
  if (!state.current) return;
  const btn = $("#soundbites-btn");
  btn.disabled = true;
  setBtnHTML(btn, "<span data-icon=&quot;target&quot;></span> Analisando...");
  log("<span data-icon=&quot;play&quot;></span> soundbites");
  try {
    const a = await api(`/api/projects/${state.current.id}/soundbites`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> ${a.soundbites.length} soundbites · ${a.topics.length} tópicos`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ soundbites: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    setBtnHTML(btn, "<span data-icon=&quot;target&quot;></span> Extrair soundbites");
  }
}

async function buildStory() {
  if (!state.current) return;
  const btn = $("#story-btn");
  btn.disabled = true;
  setBtnHTML(btn, "<span data-icon=&quot;file-text&quot;></span> Pensando...");
  log("<span data-icon=&quot;play&quot;></span> story");
  try {
    const s = await api(`/api/projects/${state.current.id}/story`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ structure: $("#story-structure").value }),
    });
    log(`<span data-icon=&quot;check&quot;></span> Roteiro: ${s.title} (${s.chapters.length} capítulos)`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ story: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    setBtnHTML(btn, "<span data-icon=&quot;file-text&quot;></span> Propor roteiro");
  }
}

async function buildRoughCut() {
  if (!state.current) return;
  const btn = $("#roughcut-btn");
  const status = $("#roughcut-status");
  btn.disabled = true;
  status.textContent = "encoding...";
  status.className = "status warn";
  log("<span data-icon=&quot;play&quot;></span> roughcut");

  // Decide source of bites:
  //  1. If any checkbox is manually checked → use those (use_story=false).
  //  2. Else if a story exists → use the story (use_story=true).
  //  3. Else if soundbites exist → fall back to ALL soundbites in
  //     chronological order so the user gets something useful.
  //  4. Else → error.
  const checked = $$("input[data-bite]").filter(c => c.checked).map(c => c.dataset.bite);
  let useStory;
  let soundbiteIds = null;
  if (checked.length > 0) {
    useStory = false;
    soundbiteIds = checked;
  } else if (state.current.has_story) {
    useStory = true;
  } else if (state.current.has_soundbites) {
    // grab all bite ids in chronological order
    try {
      const a = await api(`/api/projects/${state.current.id}/soundbites`);
      const sorted = (a.soundbites || []).slice().sort((x, y) => (x.start || 0) - (y.start || 0));
      soundbiteIds = sorted.map(s => s.id);
    } catch (e) {
      soundbiteIds = [];
    }
    if (!soundbiteIds.length) {
      btn.disabled = false;
      status.textContent = "Sem soundbites — extraia primeiro.";
      status.className = "status error";
      return;
    }
    useStory = false;
    toast(`Sem roteiro — usando todos os ${soundbiteIds.length} soundbites`);
  } else {
    btn.disabled = false;
    status.textContent = "Extraia soundbites e/ou gere um roteiro primeiro.";
    status.className = "status error";
    return;
  }

  try {
    const r = await api(`/api/projects/${state.current.id}/roughcut`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        use_story: useStory,
        soundbite_ids: useStory ? null : soundbiteIds,
        apply_lut: true,
        loudnorm: $("#rc-loudnorm")?.checked || false,
        denoise: $("#rc-denoise")?.checked || false,
      }),
    });
    status.textContent = `${r.duration.toFixed(1)}s · ${r.segments} segmentos · ${r.chapters} capítulos`;
    status.className = "status ok";
    log(`<span data-icon=&quot;check&quot;></span> rough cut · ${r.duration.toFixed(1)}s`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    status.textContent = e.message;
    status.className = "status error";
    log(`✗ roughcut: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

async function exportPremiere() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> Premiere XML");
  try {
    const res = await api(`/api/projects/${state.current.id}/export/premiere`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        use_cuts: !$("#premiere-roughcut").checked,
        use_roughcut: $("#premiere-roughcut").checked,
        include_broll: $("#premiere-broll").checked,
      }),
    });
    log(`<span data-icon=&quot;check&quot;></span> Premiere XML · ${res.bytes}b`, "ok");
    const link = $("#premiere-link");
    link.href = res.url;
    link.style.display = "inline-block";
    setBtnHTML(link, `<span data-icon=&quot;download&quot;></span> Baixar ${res.export}`);
  } catch (e) {
    log(`✗ Premiere XML: ${e.message}`, "err");
  }
}

async function placeBroll() {
  if (!state.current) return;
  const btn = $("#place-broll-btn");
  const status = $("#broll-place-status");
  btn.disabled = true;
  status.textContent = "matching...";
  status.className = "status warn";
  try {
    const r = await api(`/api/projects/${state.current.id}/place-broll`, { method: "POST" });
    status.textContent = `${r.placements.length} inserts colocados`;
    status.className = "status ok";
    log(`<span data-icon=&quot;check&quot;></span> B-roll placement · ${r.placements.length} inserts`, "ok");
  } catch (e) {
    status.textContent = e.message;
    status.className = "status error";
    log(`✗ place-broll: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

async function smartReframe() {
  if (!state.current) return;
  const btn = $("#smart-reframe-btn");
  btn.disabled = true;
  setBtnHTML(btn, "<span data-icon=&quot;sparkles&quot;></span> Detectando subject...");
  log("<span data-icon=&quot;play&quot;></span> smart reframe");
  try {
    const r = await api(`/api/projects/${state.current.id}/smart-reframe`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        aspect: $("#export-aspect").value,
        use_roughcut: state.current.has_roughcut,
      }),
    });
    log(`<span data-icon=&quot;check&quot;></span> smart-crop anchor=${r.anchor_x.toFixed(2)} · ${r.url}`, "ok");
    $("#download-link").href = r.url;
    $("#result").src = r.url;
  } catch (e) {
    log(`✗ smart-reframe: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    setBtnHTML(btn, "<span data-icon=&quot;sparkles&quot;></span> Smart crop (IA)");
  }
}

async function exportCaptions(fmt) {
  if (!state.current) return;
  log(`<span data-icon=&quot;play&quot;></span> ${fmt}`);
  try {
    const useRoughcut = $("#caps-roughcut").checked;
    const speakers = $("#caps-speakers")?.checked || false;
    const style = $("#ass-style")?.value || "minimal";
    const url = `/api/projects/${state.current.id}/export/captions?fmt=${fmt}&use_roughcut=${useRoughcut}&style=${style}&speaker_labels=${speakers}`;
    const res = await api(url, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> ${fmt} · ${res.cues} cues`, "ok");
    const link = $("#caps-link");
    link.href = res.url;
    link.style.display = "inline-block";
    setBtnHTML(link, `<span data-icon=&quot;download&quot;></span> ${res.export}`);
  } catch (e) {
    log(`✗ ${fmt}: ${e.message}`, "err");
  }
}

async function burnCaptions() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> burn captions");
  try {
    const r = await api(`/api/projects/${state.current.id}/export/burn-captions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source: $("#burn-source").value,
        style: $("#burn-style").value,
      }),
    });
    log(`<span data-icon=&quot;check&quot;></span> burned · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#burn-link");
    a.href = r.url;
    a.style.display = "inline-block";
    setBtnHTML(a, `<span data-icon=&quot;download&quot;></span> ${r.export}`);
    await refreshHistory();
  } catch (e) {
    log(`✗ burn: ${e.message}`, "err");
  }
}

async function refreshSnapshots() {
  if (!state.current) return;
  const list = $("#snap-list");
  list.innerHTML = "";
  try {
    const snaps = await api(`/api/projects/${state.current.id}/snapshots`);
    for (const s of snaps) {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="snap-label">${escapeHtml(s.label)}</span>
        <span class="snap-meta">${(s.captured_files || []).length} files · ${new Date(s.created_at).toLocaleString()}</span>
        <button class="btn-ghost" data-snap="${s.id}">Restaurar</button>
      `;
      li.querySelector("button").onclick = async () => {
        if (!(await brandedConfirm(`Restaurar a snapshot "${s.label}"?`, {title: "Restaurar snapshot", okText: "Restaurar"}))) return;
        try {
          await api(`/api/projects/${state.current.id}/snapshots/${s.id}/restore`, { method: "POST" });
          log(`<span data-icon=&quot;check&quot;></span> snapshot restaurado`, "ok");
          await loadProject(state.current.id);
        } catch (e) {
          log(`✗ restore: ${e.message}`, "err");
        }
      };
      list.appendChild(li);
    }
  } catch {}
}

async function takeSnapshot() {
  if (!state.current) return;
  const label = $("#snap-label").value || `snap ${new Date().toLocaleString()}`;
  try {
    await api(`/api/projects/${state.current.id}/snapshots`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ label }),
    });
    log(`<span data-icon=&quot;check&quot;></span> snapshot: ${label}`, "ok");
    $("#snap-label").value = "";
    await refreshSnapshots();
  } catch (e) {
    log(`✗ snapshot: ${e.message}`, "err");
  }
}

async function refreshBrandPresets() {
  const sel = $("#brand-preset-pick");
  if (!sel) return;
  sel.innerHTML = '<option value="">— escolher —</option>';
  try {
    const presets = await api(`/api/brand-presets`);
    for (const p of presets) {
      const opt = document.createElement("option");
      opt.value = p._id;
      opt.textContent = p.name;
      sel.appendChild(opt);
    }
  } catch {}
}

async function saveBrandPreset() {
  const name = $("#brand-name").value || prompt("Nome do preset?", "Marca");
  if (!name) return;
  const brand = collectBrand();
  try {
    await api(`/api/brand-presets`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, brand }),
    });
    log(`<span data-icon=&quot;check&quot;></span> Preset salvo: ${name}`, "ok");
    await refreshBrandPresets();
  } catch (e) {
    log(`✗ preset: ${e.message}`, "err");
  }
}

// ---- Transcript-driven editor ------------------------------------------------

const tx = { words: [], selStart: null, selEnd: null, ranges: [] };

async function loadTranscriptWords(p) {
  if (!p?.has_transcript) {
    $("#transcript-words").innerHTML = '<span class="tx-w" style="color:var(--muted)">Sem transcrição. Rode a etapa 4.</span>';
    tx.words = [];
    return;
  }
  try {
    const t = await api(`/api/projects/${p.id}/transcript`);
    tx.words = t.words || [];
    renderTranscriptWords();
  } catch {
    $("#transcript-words").innerHTML = '<span class="tx-w" style="color:var(--muted)">Falha ao carregar.</span>';
  }
}

function renderTranscriptWords() {
  const root = $("#transcript-words");
  if (!tx.words.length) {
    root.innerHTML = '<span class="tx-w" style="color:var(--muted)">(vazio)</span>';
    return;
  }
  root.innerHTML = tx.words.map((w, i) =>
    `<span class="tx-w" data-i="${i}" data-start="${w.start}" data-end="${w.end}">${escapeHtml(w.word)}</span>`
  ).join(" ");
  root.querySelectorAll(".tx-w").forEach(el => {
    el.addEventListener("click", txOnClick);
  });
}

function txOnClick(e) {
  const i = parseInt(e.currentTarget.dataset.i, 10);
  const w = tx.words[i];
  const video = $("#preview");
  if (e.shiftKey && tx.selStart != null) {
    tx.selEnd = i;
  } else if (e.metaKey || e.ctrlKey) {
    // toggle individual word in selection
    const has = tx.ranges.find(r => r.from <= i && r.to >= i);
    if (has) {
      tx.ranges = tx.ranges.filter(r => r !== has);
    } else {
      tx.ranges.push({ from: i, to: i });
    }
    tx.selStart = tx.selEnd = null;
  } else {
    if (video && w) video.currentTime = Math.max(0, w.start - 0.05);
    tx.selStart = i;
    tx.selEnd = i;
    if (video && video.paused) video.play().catch(() => {});
  }
  txRefresh();
}

function txRefresh() {
  const root = $("#transcript-words");
  const inSelection = (i) => {
    if (tx.selStart != null && tx.selEnd != null) {
      const lo = Math.min(tx.selStart, tx.selEnd);
      const hi = Math.max(tx.selStart, tx.selEnd);
      if (i >= lo && i <= hi) return true;
    }
    return tx.ranges.some(r => r.from <= i && r.to >= i);
  };
  root.querySelectorAll(".tx-w").forEach(el => {
    const i = parseInt(el.dataset.i, 10);
    el.classList.toggle("selected", inSelection(i));
  });
  const totalWords = tx.ranges.reduce((acc, r) => acc + (r.to - r.from + 1), 0)
    + (tx.selStart != null && tx.selEnd != null ? Math.abs(tx.selEnd - tx.selStart) + 1 : 0);
  $("#tx-status").textContent = totalWords ? `${totalWords} palavras selecionadas` : "";
  $("#tx-status").className = totalWords ? "status ok" : "status muted";
}

function txClear() {
  tx.selStart = tx.selEnd = null;
  tx.ranges = [];
  txRefresh();
}

function txCommitToSelection() {
  if (tx.selStart != null && tx.selEnd != null) {
    const lo = Math.min(tx.selStart, tx.selEnd);
    const hi = Math.max(tx.selStart, tx.selEnd);
    tx.ranges.push({ from: lo, to: hi });
    tx.selStart = tx.selEnd = null;
  }
  txRefresh();
}

async function txKeep() {
  if (!state.current) return;
  txCommitToSelection();
  if (!tx.ranges.length) {
    toast("Selecione palavras antes de aplicar.", "error");
    return;
  }
  // collapse to non-overlapping ranges
  tx.ranges.sort((a, b) => a.from - b.from);
  const merged = [];
  for (const r of tx.ranges) {
    if (merged.length && r.from <= merged[merged.length - 1].to + 1) {
      merged[merged.length - 1].to = Math.max(merged[merged.length - 1].to, r.to);
    } else {
      merged.push({ ...r });
    }
  }
  const ranges = merged.map(r => ({
    start: tx.words[r.from].start,
    end: tx.words[r.to].end,
  }));
  try {
    const plan = await api(`/api/projects/${state.current.id}/cut-from-words`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ keep: ranges, pad: 0.05 }),
    });
    log(`<span data-icon=&quot;check&quot;></span> cuts manuais · kept ${plan.kept_duration.toFixed(2)}s`, "ok");
    toast(`Cortes salvos (${plan.kept_duration.toFixed(1)}s)`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ cut-from-words: ${e.message}`, "err");
  }
}

// Live highlight of the playing word
function attachVideoSync() {
  const video = $("#preview");
  if (!video) return;
  let lastIdx = -1;
  video.addEventListener("timeupdate", () => {
    if (!tx.words.length) return;
    const t = video.currentTime;
    let hit = -1;
    for (let i = 0; i < tx.words.length; i++) {
      const w = tx.words[i];
      if (t >= w.start && t <= w.end) { hit = i; break; }
      if (w.start > t) break;
    }
    if (hit !== lastIdx) {
      const root = $("#transcript-words");
      if (lastIdx >= 0) root.querySelector(`.tx-w[data-i="${lastIdx}"]`)?.classList.remove("playing");
      if (hit >= 0) root.querySelector(`.tx-w[data-i="${hit}"]`)?.classList.add("playing");
      lastIdx = hit;
    }
  });
}

// ---- Highlights / Social / Templates ---------------------------------------

async function buildHighlights() {
  if (!state.current) return;
  const target = parseFloat($("#hl-target").value || "30");
  log(`<span data-icon=&quot;play&quot;></span> highlights ${target}s`);
  try {
    const r = await api(`/api/projects/${state.current.id}/highlights`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ target_seconds: target, apply_lut: true }),
    });
    log(`<span data-icon=&quot;check&quot;></span> highlights · ${r.duration.toFixed(1)}s · ${r.segments} bites`, "ok");
    const a = $("#hl-link");
    a.href = r.url;
    a.style.display = "inline-block";
  } catch (e) {
    log(`✗ highlights: ${e.message}`, "err");
  }
}

async function generateSocialCopy() {
  if (!state.current) return;
  const lang = $("#social-lang").value;
  const btn = $("#social-btn");
  btn.disabled = true;
  btn.textContent = "✍ Gerando...";
  try {
    const c = await api(`/api/projects/${state.current.id}/social-copy`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ language: lang }),
    });
    renderSocialCopy(c);
    log("<span data-icon=&quot;check&quot;></span> social copy", "ok");
  } catch (e) {
    log(`✗ social: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "✍ Gerar copy";
  }
}

function renderSocialCopy(c) {
  const root = $("#social-copy");
  root.classList.remove("hidden");
  const row = (label, text, cls = "") => `
    <div class="sc-row">
      <div class="sc-label">${label} <button class="copy-btn" data-copy="${escapeHtml(text)}">copiar</button></div>
      <div class="sc-text ${cls}">${escapeHtml(text)}</div>
    </div>`;
  root.innerHTML =
    row("Hook", c.hook, "hook") +
    row("Caption (Reels/TikTok)", c.caption) +
    row("Caption longa", c.long_caption, "long") +
    row("YouTube title", c.youtube_title) +
    row("YouTube description", c.youtube_description, "long") +
    row("Thumbnail title", c.thumbnail_title) +
    `<div class="sc-row"><div class="sc-label">Hashtags <button class="copy-btn" data-copy="${escapeHtml(c.hashtags.join(" "))}">copiar</button></div><div class="sc-text">${(c.hashtags || []).map(escapeHtml).join(" ")}</div></div>`;
  root.querySelectorAll(".copy-btn").forEach(b => {
    b.onclick = () => {
      navigator.clipboard.writeText(b.dataset.copy).then(() => toast("copiado", "ok")).catch(() => {});
    };
  });
}

async function refreshTemplates() {
  const sel = $("#template-pick");
  if (!sel) return;
  sel.innerHTML = '<option value="">— template —</option>';
  try {
    const list = await api(`/api/templates`);
    for (const t of list) {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = t.label;
      sel.appendChild(opt);
    }
  } catch {}
}

async function applyTemplate() {
  if (!state.current) return;
  const id = $("#template-pick").value;
  if (!id) return;
  try {
    const r = await api(`/api/projects/${state.current.id}/template`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ template_id: id }),
    });
    fillBrandForm(r.brand);
    if ($("#render-aspect")) $("#render-aspect").value = r.aspect;
    if ($("#render-source")) $("#render-source").value = r.render_source;
    if ($("#render-chapters")) $("#render-chapters").checked = r.include_chapter_cards;
    log(`<span data-icon=&quot;check&quot;></span> template aplicado: ${r.label}`, "ok");
    toast(`Template: ${r.label}`, "ok");
  } catch (e) {
    log(`✗ template: ${e.message}`, "err");
  }
}

function renderProgressBar(p) {
  const root = $("#ph-progress");
  if (!root) return;
  // Single-mode pipeline: upload → transcribe → cuts → render
  // Vlog mode: clips ≥ 1 → transcribe all → narratives → assemble
  const mode = (p.clips && p.clips.length) ? "vlog" : "single";
  let steps;
  if (mode === "vlog") {
    const hasClips = (p.clips || []).length > 0;
    const allTx = hasClips && p.clips.every(c => c.has_transcript);
    const hasNarratives = !!p.has_narratives; // we'll set this client-side too
    const hasVlogRender = (p.last_export || "").includes("-vlog-");
    steps = [
      { id: "upload",    label: `Upload (${(p.clips || []).length})`,      done: hasClips },
      { id: "transcribe",label: "Transcrição",                              done: allTx },
      { id: "narratives",label: "Narrativas",                               done: hasNarratives },
      { id: "render",    label: "Vlog renderizado",                         done: hasVlogRender },
    ];
  } else {
    steps = [
      { id: "upload",     label: "Upload",      done: !!p.source_filename },
      { id: "transcribe", label: "Transcrição", done: !!p.has_transcript },
      { id: "cuts",       label: "Cortes",      done: !!p.has_cuts || !!p.has_fillers },
      { id: "soundbites", label: "Soundbites",  done: !!p.has_soundbites },
      { id: "story",      label: "Roteiro",     done: !!p.has_story },
      { id: "render",     label: "Render",      done: !!p.has_render },
    ];
  }
  // Mark the first not-done as "next"
  let nextMarked = false;
  const html = steps.map(s => {
    let cls = "step";
    if (s.done) cls += " done";
    else if (!nextMarked) { cls += " next"; nextMarked = true; }
    return `<span class="${cls}"><span class="step-dot"></span>${escapeHtml(s.label)}</span>`;
  }).join("");
  root.innerHTML = html;

  // Suggest the next action as a primary button
  const next = steps.find(s => !s.done);
  const btn = $("#ph-next-step");
  if (!btn) return;
  if (!next) {
    btn.style.display = "none";
  } else {
    btn.style.display = "inline-block";
    const map = {
      upload:     { label: "↑ Subir vídeo",       fn: () => $("#file-input")?.click() },
      transcribe: { label: "<span data-icon=&quot;play&quot;></span> Transcrever agora", fn: () => runStage("transcribe") },
      cuts:       { label: "<span data-icon=&quot;play&quot;></span> Detectar silêncios + muletas", fn: () => runStage("silence") },
      soundbites: { label: "<span data-icon=&quot;target&quot;></span> Extrair soundbites", fn: () => extractSoundbites() },
      story:      { label: "<span data-icon=&quot;file-text&quot;></span> Propor roteiro",   fn: () => buildStory() },
      render:     { label: "<span data-icon=&quot;film&quot;></span> Renderizar",       fn: () => runStage("render") },
      narratives: { label: "🧭 Sugerir narrativas", fn: () => vlogProposeNarratives() },
    };
    const m = map[next.id];
    if (!m) { btn.style.display = "none"; return; }
    btn.textContent = m.label;
    btn.onclick = m.fn;
  }
}

function renderStaleWarnings(p) {
  const root = $("#stale-warnings");
  if (!root) return;
  root.innerHTML = "";
  const stale = p.stale || {};
  const labels = {
    graded_vs_cuts: "graded.mp4 está desatualizado — re-rode 'Aplicar edição'.",
    soundbites_vs_transcript: "Transcrição mais nova que os soundbites — re-rode 'Extrair soundbites'.",
    story_vs_soundbites: "Soundbites mudaram depois do roteiro — re-rode 'Propor roteiro'.",
    roughcut_vs_story: "Roteiro mudou depois do rough cut — re-gere o rough cut.",
  };
  for (const [key, msg] of Object.entries(labels)) {
    if (stale[key]) {
      const div = document.createElement("div");
      div.className = "warn";
      setBtnHTML(div, `<span data-icon=&quot;alert-triangle&quot;></span> ${msg}`);
      root.appendChild(div);
    }
  }
}

function renderCutsTimeline(p) {
  const svg = $("#cuts-timeline");
  if (!svg) return;
  const dur = p.source_duration || 0;
  if (!dur) {
    svg.hidden = true;
    return;
  }
  svg.hidden = false;
  const W = 1000, H = 60;
  // background bar
  const parts = [`<rect x="0" y="${H/2 - 8}" width="${W}" height="16" rx="4" fill="rgba(255,255,255,0.06)"/>`];

  fetch(`/api/projects/${p.id}/cuts`).then(r => r.ok ? r.json() : null).then(plan => {
    if (plan?.silences?.length) {
      for (const s of plan.silences) {
        const x = (s.start / dur) * W;
        const w = ((s.end - s.start) / dur) * W;
        parts.push(`<rect x="${x}" y="${H/2 - 8}" width="${Math.max(2, w)}" height="16" fill="rgba(239, 68, 68, 0.55)"/>`);
      }
    }
    if (plan?.keep?.length) {
      for (const k of plan.keep) {
        const x = (k.start / dur) * W;
        const w = ((k.end - k.start) / dur) * W;
        parts.push(`<rect x="${x}" y="${H/2 - 8}" width="${Math.max(2, w)}" height="16" fill="rgba(52, 211, 153, 0.55)"/>`);
      }
    }
    fetch(`/api/projects/${p.id}/files/fillers.json`).then(r => r.ok ? r.json() : null).then(filler => {
      if (filler?.ranges?.length) {
        for (const r of filler.ranges) {
          const x = (r.start / dur) * W;
          const w = ((r.end - r.start) / dur) * W;
          parts.push(`<rect x="${x}" y="${H/2 - 8}" width="${Math.max(2, w)}" height="16" fill="rgba(245, 158, 11, 0.65)"/>`);
        }
      }
      // legend
      parts.push(`<text x="6" y="14" fill="rgba(255,255,255,0.55)" font-size="10">verde=keep · vermelho=silence · âmbar=muletas</text>`);
      parts.push(`<text x="${W - 60}" y="14" fill="rgba(255,255,255,0.55)" font-size="10" text-anchor="end">${dur.toFixed(1)}s</text>`);
      svg.innerHTML = parts.join("");
    }).catch(() => {
      svg.innerHTML = parts.join("") + `<text x="6" y="14" fill="rgba(255,255,255,0.55)" font-size="10">${dur.toFixed(1)}s</text>`;
    });
  }).catch(() => {
    svg.innerHTML = parts.join("") + `<text x="6" y="14" fill="rgba(255,255,255,0.55)" font-size="10">${dur.toFixed(1)}s — sem cortes ainda</text>`;
  });
}

async function exportAudio() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> audio export");
  try {
    const r = await api(`/api/projects/${state.current.id}/export/audio`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        format: $("#audio-format").value,
        source: $("#audio-source").value,
      }),
    });
    log(`<span data-icon=&quot;check&quot;></span> audio · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#audio-link");
    a.href = r.url;
    a.style.display = "inline-block";
    setBtnHTML(a, `<span data-icon=&quot;download&quot;></span> ${r.export}`);
  } catch (e) {
    log(`✗ audio: ${e.message}`, "err");
  }
}

async function cancelRender() {
  if (!state.current) return;
  try {
    const r = await api(`/api/projects/${state.current.id}/render/cancel`, { method: "POST" });
    log(r.cancelled ? "<span data-icon=&quot;check&quot;></span> render cancelado" : "ℹ nenhum render rodando", r.cancelled ? "ok" : "");
    $("#cancel-render-btn").style.display = "none";
  } catch (e) {
    log(`✗ cancel: ${e.message}`, "err");
  }
}

async function exportBundle() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> bundle");
  try {
    const r = await api(`/api/projects/${state.current.id}/export/bundle`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> bundle · ${r.files} files · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#bundle-link");
    a.href = r.url;
    a.style.display = "inline-block";
    setBtnHTML(a, `<span data-icon=&quot;download&quot;></span> ${r.export}`);
  } catch (e) {
    log(`✗ bundle: ${e.message}`, "err");
  }
}

async function duplicateProject() {
  if (!state.current) return;
  if (!(await brandedConfirm(`Duplicar "${state.current.name}"? Vai criar um clone completo.`,
        {title: "Duplicar projeto", okText: "Duplicar"}))) return;
  try {
    const p = await api(`/api/projects/${state.current.id}/duplicate`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> duplicado: ${p.name}`, "ok");
    await refreshList();
    await loadProject(p.id);
  } catch (e) {
    log(`✗ duplicate: ${e.message}`, "err");
  }
}

async function detectSpeakers() {
  if (!state.current) return;
  try {
    const r = await api(`/api/projects/${state.current.id}/speakers`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ backend: "auto" }),
    });
    const stats = r.stats || {};
    const speakers = Object.entries(stats.by_speaker || {})
      .map(([sp, s]) => `${sp}: ${(s.share * 100).toFixed(0)}%`)
      .join("  ·  ");
    log(`<span data-icon=&quot;check&quot;></span> ${r.backend} · ${r.turns.length} turnos · ${speakers}`, "ok");
    toast(`${r.stats?.speaker_count || 0} speakers detectados (${r.backend})`, "ok");
  } catch (e) {
    log(`✗ speakers: ${e.message}`, "err");
  }
}

async function levelSpeakers() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> leveling speakers");
  try {
    const r = await api(`/api/projects/${state.current.id}/speaker-levels`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ target_dbfs: -18.0, source: "graded" }),
    });
    const gains = Object.entries(r.gains).map(([sp, g]) => `${sp}:${g > 0 ? "+" : ""}${g}dB`).join("  ");
    log(`<span data-icon=&quot;check&quot;></span> gains: ${gains}`, "ok");
    toast(`Nivelado: ${gains}`, "ok");
    await refreshHistory();
  } catch (e) {
    log(`✗ speaker-levels: ${e.message}`, "err");
  }
}

async function detectChapters() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> chapters");
  try {
    const r = await api(`/api/projects/${state.current.id}/chapters`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> ${r.chapters.length} capítulos`, "ok");
    toast(`${r.chapters.length} capítulos detectados`, "ok");
    console.log("YouTube chapters:\n" + r.youtube_markdown);
  } catch (e) {
    log(`✗ chapters: ${e.message}`, "err");
  }
}

async function runPodcastPipeline() {
  if (!state.current) return;
  const btn = $("#podcast-pipeline-btn");
  const progress = $("#podcast-progress");
  const fill = $("#podcast-fill");
  const label = $("#podcast-label");
  btn.disabled = true;
  progress.classList.remove("hidden");
  fill.style.width = "0%";
  label.textContent = "iniciando…";
  log("<span data-icon=&quot;play&quot;></span> podcast pipeline");
  try {
    const lang = $("#podcast-lang").value || null;
    const r = await api(`/api/projects/${state.current.id}/podcast-pipeline`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ language: lang }),
    });
    state.podcastJobId = r.job_id;
    label.textContent = "rodando…";
    toast(`Pipeline iniciado · ${r.job_id}`, "ok");
  } catch (e) {
    log(`✗ pipeline: ${e.message}`, "err");
    btn.disabled = false;
    label.textContent = e.message;
    label.className = "podcast-label";
  }
}

async function generateShorts() {
  if (!state.current) return;
  const btn = $("#shorts-btn");
  btn.disabled = true;
  log("<span data-icon=&quot;play&quot;></span> shorts batch");
  try {
    const r = await api(`/api/projects/${state.current.id}/shorts/batch`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        target_count: parseInt($("#shorts-count").value || "5", 10),
        aspect: $("#shorts-aspect").value,
        use_hyperframes: true,
      }),
    });
    state.shortsJobId = r.job_id;
    toast(`Gerando shorts… (${r.job_id})`, "ok");
  } catch (e) {
    log(`✗ shorts: ${e.message}`, "err");
    btn.disabled = false;
  }
}

async function refreshShortsGallery() {
  if (!state.current) return;
  try {
    const r = await api(`/api/projects/${state.current.id}/shorts`);
    const root = $("#shorts-gallery");
    if (!root) return;
    root.innerHTML = "";
    for (const s of (r.shorts || [])) {
      const div = document.createElement("div");
      div.className = "short";
      div.innerHTML = `
        <video src="${s.url}" controls playsinline preload="metadata"></video>
        <div class="meta">
          <span>${escapeHtml(s.id)} · ${s.duration.toFixed(1)}s</span>
          <a href="${s.url}" download>⬇</a>
        </div>
      `;
      root.appendChild(div);
    }
  } catch {}
}

async function downloadYoutubeDescription() {
  if (!state.current) return;
  try {
    const r = await api(`/api/projects/${state.current.id}/export/youtube-description`, { method: "POST" });
    log("<span data-icon=&quot;check&quot;></span> YouTube description gerada", "ok");
    window.open(r.url, "_blank");
  } catch (e) {
    log(`✗ youtube: ${e.message}`, "err");
  }
}

// ---- Vlog mode -------------------------------------------------------------

async function uploadClips(files) {
  if (!state.current) return;
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("name", f.name);
    log(`<span data-icon=&quot;play&quot;></span> uploading clip ${f.name}`);
    try {
      const r = await api(`/api/projects/${state.current.id}/clips`, { method: "POST", body: fd });
      log(`<span data-icon=&quot;check&quot;></span> clip ${r.name} (${r.duration.toFixed(1)}s)`, "ok");
    } catch (e) {
      log(`✗ clip upload: ${e.message}`, "err");
    }
  }
  await loadClips();
}

async function loadClips() {
  if (!state.current) return;
  try {
    const clips = await api(`/api/projects/${state.current.id}/clips`);
    const root = $("#clips-list");
    if (!root) return;
    root.innerHTML = "";
    for (const c of clips) {
      const li = document.createElement("li");
      const thumb = c.thumbnail
        ? `<img class="bite-thumb" src="${escapeHtml(c.thumbnail)}" alt="" loading="lazy"/>`
        : "";
      const peopleBadges = (c.face_clusters || []).map(p =>
        `<span class="tag">👤 ${escapeHtml(p)}</span>`
      ).join(" ");
      li.innerHTML = `
        ${thumb}
        <span class="a-name">${escapeHtml(c.name)}${c.has_transcript ? ' <span class="tag ok"><span data-icon=&quot;check&quot;></span> txt</span>' : ' <span class="tag warn">— sem txt</span>'} ${peopleBadges}</span>
        <span class="a-meta">${c.duration.toFixed(1)}s${c.transcript_text ? ' · ' + escapeHtml(c.transcript_text.slice(0, 60)) : ''}</span>
        <span class="a-actions">
          <button class="btn-ghost c-txn" data-cid="${c.id}">Transcrever</button>
          <button class="a-del" data-cid="${c.id}">×</button>
        </span>
      `;
      li.querySelector(".c-txn").onclick = async () => {
        try {
          await api(`/api/projects/${state.current.id}/clips/${c.id}/transcribe`, { method: "POST" });
          await loadClips();
        } catch (e) { log(`✗ transcribe: ${e.message}`, "err"); }
      };
      li.querySelector(".a-del").onclick = async () => {
        if (!(await brandedConfirm(`Excluir o clipe "${c.name}"?`, {title: "Excluir clipe", okText: "Excluir"}))) return;
        await api(`/api/projects/${state.current.id}/clips/${c.id}`, { method: "DELETE" });
        await loadClips();
      };
      root.appendChild(li);
    }
  } catch {}
}

async function vlogTranscribeAll() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> transcribe all clips");
  try {
    const r = await api(`/api/projects/${state.current.id}/clips/transcribe-all`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });
    state.vlogTranscribeJobId = r.job_id;
    toast(`Transcrição em lote rodando…`, "ok");
  } catch (e) {
    log(`✗ transcribe-all: ${e.message}`, "err");
  }
}

async function vlogProposeNarratives() {
  if (!state.current) return;
  const btn = $("#vlog-narratives-btn");
  btn.disabled = true;
  btn.textContent = "🧭 pensando...";
  log("<span data-icon=&quot;play&quot;></span> vlog narratives");
  try {
    const r = await api(`/api/projects/${state.current.id}/vlog/narratives`, { method: "POST" });
    renderNarratives(r.narratives || []);
    log(`<span data-icon=&quot;check&quot;></span> ${r.narratives?.length || 0} narrativas propostas`, "ok");
  } catch (e) {
    log(`✗ narratives: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "🧭 Sugerir narrativas";
  }
}

function renderNarratives(narratives) {
  const root = $("#narratives-list");
  root.innerHTML = "";
  // Build a clip lookup so we can show clip thumbnails per bite
  const clipById = {};
  (state.current?.clips || []).forEach(c => { clipById[c.id] = c; });

  for (const n of narratives) {
    const card = document.createElement("div");
    card.className = "narrative-card";
    const totalDur = n.estimated_duration || (n.sequence || []).reduce((acc, b) => acc + (b.end - b.start), 0);
    const tlW = 1000;
    let cursor = 0;
    const tlBlocks = (n.sequence || []).map((b, i) => {
      const dur = b.end - b.start;
      const x = (cursor / Math.max(0.001, totalDur)) * tlW;
      const w = (dur / Math.max(0.001, totalDur)) * tlW;
      const clip = clipById[b.clip_id];
      const fill = clip?.thumbnail
        ? `<image href="${escapeHtml(clip.thumbnail)}" x="${x.toFixed(1)}" y="0" width="${Math.max(2, w).toFixed(1)}" height="64" preserveAspectRatio="xMidYMid slice" />`
        : `<rect x="${x.toFixed(1)}" y="0" width="${Math.max(2, w).toFixed(1)}" height="64" fill="rgba(167,139,250,0.4)"/>`;
      const overlay = `
        ${fill}
        <rect x="${x.toFixed(1)}" y="0" width="${Math.max(2, w).toFixed(1)}" height="64" fill="rgba(0,0,0,0.35)"/>
        <text x="${(x + 4).toFixed(1)}" y="14" fill="white" font-size="9" font-weight="700">${i + 1}</text>
        <text x="${(x + 4).toFixed(1)}" y="58" fill="white" font-size="9">${dur.toFixed(1)}s</text>`;
      cursor += dur;
      return overlay;
    }).join("");
    const tl = `<svg viewBox="0 0 ${tlW} 64" preserveAspectRatio="none" class="vlog-timeline-svg">${tlBlocks}</svg>`;

    const segs = (n.sequence || []).map(s =>
      `<span class="seg">${escapeHtml((clipById[s.clip_id]?.name || s.clip_id).slice(0, 16))} ${s.start.toFixed(1)}-${s.end.toFixed(1)}s<span class="reason">${escapeHtml(s.reason || "")}</span></span>`
    ).join("");

    card.innerHTML = `
      <div class="nh">
        <span class="name">${escapeHtml(n.name)}</span>
        <span class="genre">${escapeHtml(n.genre || "")}</span>
        <span class="duration">~${totalDur.toFixed(1)}s</span>
      </div>
      <div class="logline">${escapeHtml(n.logline || "")}</div>
      ${tl}
      <details class="seq-details"><summary>Sequência (${(n.sequence || []).length} bites)</summary><div class="seq">${segs}</div></details>
      <div class="actions">
        <select class="inline-select n-aspect"><option value="9:16">9:16</option><option value="16:9">16:9</option><option value="1:1">1:1</option></select>
        <button class="btn-ghost n-storyboard" data-id="${n.id}">📰 Storyboard</button>
        <button class="btn-ghost n-broll" data-id="${n.id}"><span data-icon=&quot;target&quot;></span> B-roll</button>
        <button class="btn-primary n-assemble" data-id="${n.id}"><span data-icon=&quot;film&quot;></span> Montar este vlog</button>
        <a class="btn-ghost n-link" href="#" download style="display:none"><span data-icon=&quot;download&quot;></span> vlog.mp4</a>
      </div>
    `;
    card.querySelector(".n-storyboard").onclick = async () => {
      try {
        const r = await api(`/api/projects/${state.current.id}/vlog/narratives/${n.id}/storyboard`, { method: "POST" });
        log(`<span data-icon=&quot;check&quot;></span> storyboard ${n.id}: ${r.panels.length} painéis`, "ok");
        toast(`Storyboard com ${r.panels.length} painéis`, "ok");
      } catch (e) { log(`✗ storyboard: ${e.message}`, "err"); }
    };
    card.querySelector(".n-broll").onclick = async () => {
      try {
        const r = await api(`/api/projects/${state.current.id}/vlog/place-broll?narrative_id=${n.id}`, { method: "POST" });
        log(`<span data-icon=&quot;check&quot;></span> B-roll ${n.id}: ${r.placements.length} inserts`, "ok");
        toast(`${r.placements.length} B-roll inserts planejados`, "ok");
      } catch (e) { log(`✗ B-roll: ${e.message}`, "err"); }
    };
    card.querySelector(".n-assemble").onclick = async () => {
      const aspect = card.querySelector(".n-aspect").value;
      log(`<span data-icon=&quot;play&quot;></span> assemble narrative ${n.id}`);
      try {
        const r = await api(`/api/projects/${state.current.id}/vlog/assemble`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ narrative_id: n.id, aspect, loudnorm: true }),
        });
        log(`<span data-icon=&quot;check&quot;></span> vlog · ${r.bytes} bytes`, "ok");
        const a = card.querySelector(".n-link");
        a.href = r.url; a.style.display = "inline-block"; setBtnHTML(a, `<span data-icon=&quot;download&quot;></span> ${r.export}`);
        await refreshHistory();
      } catch (e) {
        log(`✗ assemble: ${e.message}`, "err");
      }
    };
    root.appendChild(card);
  }
}

async function multicamPick() {
  if (!state.current) return;
  // Pre-flight: bail with a friendly toast pointing at what's missing.
  const p = state.current;
  if (!(p.angles || []).length) {
    toast?.("Suba pelo menos 1 ângulo (Câmera B) antes.", "err");
    return;
  }
  if (!p.has_transcript) {
    toast?.("Rode a transcrição antes — botão 'Transcrever' no checklist acima.", "err");
    return;
  }
  if (!p.has_speakers) {
    toast?.("Rode 'Detectar falas' antes — o multicam-pick precisa saber quem fala.", "err");
    return;
  }
  log(`<span data-icon="play"></span> multicam pick`);
  try {
    const r = await api(`/api/projects/${state.current.id}/multicam-pick`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ intervals: "turns", min_dur: 1.4 }),
    });
    log(`<span data-icon="check"></span> ${r.cuts.length} cam cuts (intervalos: ${r.intervals})`, "ok");
    toast(`Câmera escolhida pra ${r.cuts.length} segmentos`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ multicam-pick: ${e.message}`, "err");
    toast?.(`Falha: ${e.message}`, "err");
  }
}

async function multicamRender() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> multicam render");
  try {
    const r = await api(`/api/projects/${state.current.id}/multicam-render`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> multicam ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#multicam-link");
    a.href = r.url; a.style.display = "inline-block"; setBtnHTML(a, `<span data-icon=&quot;download&quot;></span> ${r.export}`);
    await refreshHistory();
  } catch (e) {
    log(`✗ multicam render: ${e.message}`, "err");
  }
}

async function vlogAutoPipeline() {
  if (!state.current) return;
  const btn = $("#vlog-auto-btn");
  const status = $("#vlog-status");
  btn.disabled = true;
  status.textContent = "iniciando…";
  status.className = "status warn";
  log("<span data-icon=&quot;play&quot;></span> vlog 1-click");
  try {
    const r = await api(`/api/projects/${state.current.id}/vlog/auto-pipeline`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        aspect: "9:16",
        apply_brand: true,
        chapter_cards: true,
        do_face_clustering: true,
      }),
    });
    state.vlogPipelineJobId = r.job_id;
    toast("Vlog pipeline rodando…", "ok");
  } catch (e) {
    log(`✗ vlog pipeline: ${e.message}`, "err");
    btn.disabled = false;
    status.textContent = e.message;
    status.className = "status error";
  }
}

async function vlogMusicSuggest() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> vlog music suggest");
  try {
    const s = await api(`/api/projects/${state.current.id}/vlog/music-suggest?language=pt`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> ${s.description}`, "ok");
    renderMusicSuggestion(s);
  } catch (e) {
    log(`✗ vlog music: ${e.message}`, "err");
  }
}

async function detectFaceIdentities() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> face identities");
  try {
    const r = await api(`/api/projects/${state.current.id}/face-identities`, { method: "POST" });
    renderPeople(r.clusters || [], r.presence || {});
    toast(`${r.clusters?.length || 0} pessoas detectadas`, "ok");
  } catch (e) {
    log(`✗ face-ids: ${e.message}`, "err");
  }
}

function renderPeople(clusters, presence) {
  const root = $("#people-list");
  if (!root) return;
  root.innerHTML = "";
  for (const c of clusters) {
    const div = document.createElement("div");
    div.className = "person";
    const where = (c.sources || []).map(s => s.split(":").slice(-1)[0] || s).join(", ") || "—";
    const thumb = c.thumbnail
      ? `<img class="bite-thumb" src="${escapeHtml(c.thumbnail)}" alt=""/>`
      : "";
    const display = c.display_name || c.id;
    div.innerHTML = `
      ${thumb}
      <span class="pid">${escapeHtml(c.id)}</span>
      <input class="snap-input p-name" type="text" value="${escapeHtml(display === c.id ? '' : display)}" placeholder="Nome (ex.: Marina)" style="max-width:160px" />
      <span class="sources">${escapeHtml(where)}</span>
      <span class="count">${c.size}</span>
      <button class="btn-ghost p-save" data-cid="${c.id}">Salvar</button>
    `;
    div.querySelector(".p-save").onclick = async () => {
      const name = div.querySelector(".p-name").value.trim();
      if (!name) return;
      try {
        await api(`/api/projects/${state.current.id}/face-identities/${c.id}`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name }),
        });
        toast(`${c.id} → ${name}`, "ok");
      } catch (e) { log(`✗ rename: ${e.message}`, "err"); }
    };
    root.appendChild(div);
  }
}

async function generatePeopleThumbs() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> people thumbs");
  try {
    const r = await api(`/api/projects/${state.current.id}/face-identities/thumbnails`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> ${r.thumbs.length} thumbs gerados`, "ok");
    // refresh people list
    const ids = await api(`/api/projects/${state.current.id}/files/face_identities.json`);
    renderPeople(ids.clusters || [], ids.presence || {});
  } catch (e) {
    log(`✗ people thumbs: ${e.message}`, "err");
  }
}

async function buildSourceSubjectTimeline() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> subject timeline (source)");
  try {
    const r = await api(`/api/projects/${state.current.id}/subject-timeline?target=source&step_seconds=0.5`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> ${r.events?.length || 0} samples · ${r.changes?.length || 0} mudanças`, "ok");
    toast(`${r.changes?.length || 0} mudanças de sujeito detectadas`, "ok");
  } catch (e) {
    log(`✗ subject timeline: ${e.message}`, "err");
  }
}

async function multicamSync() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> multicam sync");
  try {
    const r = await api(`/api/projects/${state.current.id}/multicam-sync`, { method: "POST" });
    const desc = r.offsets.slice(1).map(o => `${o.name}: ${o.offset > 0 ? "+" : ""}${o.offset.toFixed(2)}s (${(o.score * 100).toFixed(0)}%)`).join(", ");
    log(`<span data-icon=&quot;check&quot;></span> ${r.offsets.length - 1} angles → ${desc}`, "ok");
    toast(`Sync: ${desc || "no offsets"}`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ sync: ${e.message}`, "err");
  }
}

async function uploadMusic(file) {
  if (!state.current) return;
  const fd = new FormData();
  fd.append("file", file);
  log(`<span data-icon=&quot;play&quot;></span> uploading music ${file.name}`);
  try {
    const r = await api(`/api/projects/${state.current.id}/music/upload`, { method: "POST", body: fd });
    log(`<span data-icon=&quot;check&quot;></span> music uploaded (${(r.bytes / 1024).toFixed(0)} KB)`, "ok");
    toast(`Música pronta: ${r.filename}`, "ok");
  } catch (e) {
    log(`✗ music upload: ${e.message}`, "err");
  }
}

async function mixMusic() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> mixing music + voice");
  try {
    const r = await api(`/api/projects/${state.current.id}/music/mix`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source: $("#music-source").value,
        music_db: parseFloat($("#music-db").value || "-8"),
      }),
    });
    log(`<span data-icon=&quot;check&quot;></span> mix · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#mix-link");
    a.href = r.url;
    a.style.display = "inline-block";
    setBtnHTML(a, `<span data-icon=&quot;download&quot;></span> ${r.export}`);
    await refreshHistory();
  } catch (e) {
    log(`✗ mix: ${e.message}`, "err");
  }
}

async function showWaveform() {
  if (!state.current) return;
  const svg = $("#waveform");
  svg.hidden = false;
  svg.innerHTML = `<text x="6" y="18" fill="rgba(255,255,255,0.55)" font-size="10">carregando...</text>`;
  try {
    const wf = await api(`/api/projects/${state.current.id}/waveform?buckets=600`);
    let turns = [];
    try {
      const sp = await api(`/api/projects/${state.current.id}/files/speakers.json`);
      turns = (sp && sp.turns) || [];
    } catch {}
    const W = 1000, H = 60;
    const peaks = wf.peaks || [];
    const N = peaks.length;
    if (N === 0) {
      svg.innerHTML = `<text x="6" y="18" fill="rgba(255,255,255,0.55)" font-size="10">sem áudio</text>`;
      return;
    }
    const dur = wf.duration || 0;
    const speakerColors = { A: "rgba(245, 158, 11, 0.85)", B: "rgba(6, 182, 212, 0.85)", C: "rgba(167, 139, 250, 0.85)" };
    function colorAt(secs) {
      for (const t of turns) {
        if (secs >= (t.start || 0) && secs <= (t.end || 0)) {
          return speakerColors[t.speaker] || "rgba(167, 139, 250, 0.7)";
        }
      }
      return "rgba(167, 139, 250, 0.55)";
    }
    const bw = W / N;
    const parts = [];
    // tint background bands by speaker (subtle)
    for (const t of turns) {
      const x1 = ((t.start || 0) / Math.max(0.001, dur)) * W;
      const x2 = ((t.end || 0) / Math.max(0.001, dur)) * W;
      const c = (speakerColors[t.speaker] || "rgba(167, 139, 250, 0.4)").replace("0.85", "0.10").replace("0.7", "0.10").replace("0.55", "0.08");
      parts.push(`<rect x="${x1.toFixed(1)}" y="0" width="${Math.max(1, x2 - x1).toFixed(1)}" height="${H}" fill="${c}"/>`);
    }
    for (let i = 0; i < N; i++) {
      const h = Math.max(1, peaks[i] * (H - 4));
      const x = i * bw;
      const y = (H - h) / 2;
      const t_sec = dur * (i / N);
      parts.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(0.6, bw - 0.4).toFixed(1)}" height="${h.toFixed(1)}" fill="${colorAt(t_sec)}"/>`);
    }
    parts.push(`<text x="6" y="14" fill="rgba(255,255,255,0.55)" font-size="10">waveform · ${dur.toFixed(1)}s${turns.length ? ' · speakers coloridos' : ''}</text>`);
    svg.innerHTML = parts.join("");
  } catch (e) {
    svg.innerHTML = `<text x="6" y="18" fill="rgba(239,68,68,0.7)" font-size="10">${escapeHtml(e.message)}</text>`;
  }
}

async function archiveProject() {
  if (!state.current) return;
  if (!(await brandedConfirm(
    `Arquivar "${state.current.name}"? Vai gerar um zip com tudo e remover o projeto da lista.`,
    {title: "Arquivar projeto", okText: "Arquivar"}
  ))) return;
  try {
    const r = await api(`/api/projects/${state.current.id}/archive`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> archived: ${r.archived}`, "ok");
    toast(`Arquivado: ${r.archived}`, "ok");
    clearActiveProject();
    await refreshList();
  } catch (e) {
    log(`✗ archive: ${e.message}`, "err");
  }
}

async function makeHook() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> hook");
  try {
    const r = await api(`/api/projects/${state.current.id}/hook`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ target_seconds: 4 }),
    });
    log(`<span data-icon=&quot;check&quot;></span> hook ${r.duration.toFixed(1)}s @ ${r.start.toFixed(1)}s`, "ok");
    toast(`Hook pronto · ${r.duration.toFixed(1)}s`, "ok");
    await refreshHistory();
  } catch (e) {
    log(`✗ hook: ${e.message}`, "err");
  }
}

async function peakThumb() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> peak thumbnail");
  try {
    const r = await api(`/api/projects/${state.current.id}/peak-thumbnail`, { method: "POST" });
    const grid = $("#thumbs-grid");
    const div = document.createElement("div");
    div.className = "thumb";
    div.innerHTML = `<img src="${r.url}?t=${Date.now()}" alt="peak"/><div class="label"><span data-icon=&quot;sparkles&quot;></span> Peak @ ${r.at.toFixed(1)}s</div>`;
    grid.prepend(div);
    log(`<span data-icon=&quot;check&quot;></span> peak thumb @ ${r.at.toFixed(1)}s`, "ok");
  } catch (e) {
    log(`✗ peak: ${e.message}`, "err");
  }
}

async function emojifyCaptions() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> emojify");
  try {
    const r = await api(`/api/projects/${state.current.id}/captions/emojify`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ use_llm: true }),
    });
    log(`<span data-icon=&quot;check&quot;></span> ${r.updated} linhas decoradas`, "ok");
    toast(`${r.updated} legendas com emoji`, "ok");
  } catch (e) {
    log(`✗ emojify: ${e.message}`, "err");
  }
}

async function refreshHistory() {
  if (!state.current) return;
  try {
    const list = await api(`/api/projects/${state.current.id}/history`);
    // Defensive dedupe by name: legacy history files may still hold
    // duplicate rows from clicks before the backend dedupe shipped.
    // Keep the latest occurrence of each file name (latest ts wins).
    const byName = new Map();
    for (const e of list) byName.set(e.name, e);
    const deduped = Array.from(byName.values())
      .sort((a, b) => (new Date(a.ts) - new Date(b.ts)));
    const root = $("#history-list");
    if (root) {
      root.innerHTML = "";
      for (const e of deduped.slice().reverse().slice(0, 30)) {
        const li = document.createElement("li");
        const kb = (e.bytes / 1024).toFixed(0);
        li.innerHTML = `
          <span class="h-kind">${escapeHtml(e.kind)}</span>
          <span class="h-name">${escapeHtml(e.name)}</span>
          <span class="h-meta">${kb} KB · ${new Date(e.ts).toLocaleString()}</span>
          <a href="${e.url}" download>⬇</a>
        `;
        root.appendChild(li);
      }
    }
  } catch {}
  // Keep the export preview in sync with whatever's in history
  refreshExportPreview();
}

async function chapterThumbs() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> chapter thumbs");
  try {
    const r = await api(`/api/projects/${state.current.id}/chapter-thumbs`, { method: "POST" });
    const grid = $("#thumbs-grid");
    grid.innerHTML = "";
    for (const t of r.thumbs) {
      const div = document.createElement("div");
      div.className = "thumb";
      div.innerHTML = `<img src="${t.url}" alt="${escapeHtml(t.name)}" loading="lazy"/><div class="label">${escapeHtml(t.name)}</div>`;
      grid.appendChild(div);
    }
    log(`<span data-icon=&quot;check&quot;></span> thumbs · ${r.thumbs.length}`, "ok");
  } catch (e) {
    log(`✗ thumbs: ${e.message}`, "err");
  }
}

async function applyBrandPreset() {
  if (!state.current) return;
  const id = $("#brand-preset-pick").value;
  if (!id) return;
  try {
    const b = await api(`/api/projects/${state.current.id}/brand/from-preset/${id}`, { method: "POST" });
    fillBrandForm(b);
    log(`<span data-icon=&quot;check&quot;></span> Preset aplicado`, "ok");
  } catch (e) {
    log(`✗ apply preset: ${e.message}`, "err");
  }
}

function collectBrand() {
  return {
    name: $("#brand-name").value || "Brand",
    tagline: $("#brand-tagline").value || null,
    intro_title: $("#brand-intro-title").value || null,
    intro_subtitle: $("#brand-intro-sub").value || null,
    outro_text: $("#brand-outro").value || null,
    caption_position: $("#brand-cap-pos").value,
    caption_style: $("#brand-cap-style")?.value || "minimal",
    palette: {
      primary: $("#c-primary").value,
      secondary: $("#c-secondary").value,
      accent: $("#c-accent").value,
      background: $("#c-bg").value,
      foreground: $("#c-fg").value,
    },
  };
}

function fillBrandForm(b) {
  $("#brand-name").value = b.name || "";
  $("#brand-tagline").value = b.tagline || "";
  $("#brand-intro-title").value = b.intro_title || "";
  $("#brand-intro-sub").value = b.intro_subtitle || "";
  $("#brand-outro").value = b.outro_text || "";
  $("#brand-cap-pos").value = b.caption_position || "bottom";
  if ($("#brand-cap-style")) $("#brand-cap-style").value = b.caption_style || "minimal";
  $("#c-primary").value = b.palette?.primary || "#a78bfa";
  $("#c-secondary").value = b.palette?.secondary || "#3b82f6";
  $("#c-accent").value = b.palette?.accent || "#f472b6";
  $("#c-bg").value = b.palette?.background || "#06060a";
  $("#c-fg").value = b.palette?.foreground || "#ffffff";
}

async function doExport() {
  if (!state.current) return;
  try {
    const res = await api(`/api/projects/${state.current.id}/export`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ aspect: $("#export-aspect").value }),
    });
    log(`Exportado: ${res.export}`, "ok");
    $("#download-link").href = res.url;
    $("#result").src = res.url;
  } catch (e) {
    log(`Export erro: ${e.message}`, "err");
  }
}

function clearActiveProject() {
  state.current = null;
  try { localStorage.removeItem("hfvp.current_project"); } catch {}
  if (location.hash.startsWith("#p/")) history.replaceState(null, "", location.pathname);
  $("#project-view").classList.add("hidden");
  $("#empty").classList.remove("hidden");
}

async function deleteProject() {
  if (!state.current) return;
  if (!(await brandedConfirm(
    `Excluir o projeto "${state.current.name}"? Isso apaga toda a mídia + os planos. Não tem como desfazer.`,
    {title: "Excluir projeto", okText: "Excluir"}
  ))) return;
  await api(`/api/projects/${state.current.id}`, { method: "DELETE" });
  clearActiveProject();
  await refreshList();
}

async function uploadAngle(file) {
  if (!state.current) return;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", $("#angle-name").value || `Ângulo ${(state.current.angles?.length || 0) + 1}`);

  // Sticky log line we update in place with upload progress
  const el = $("#pipeline-log");
  const line = document.createElement("div");
  line.innerHTML = `<span data-icon="upload-cloud"></span> Subindo ângulo: ${file.name} (${fmtBytes(file.size)}) — 0%`;
  el?.appendChild(line);
  el && (el.scrollTop = el.scrollHeight);

  try {
    const r = await apiUpload(`/api/projects/${state.current.id}/angles`, fd, {
      onProgress: ({ loaded, total, pct }) => {
        line.innerHTML = `<span data-icon="upload-cloud"></span> Subindo ${file.name} — ${(pct * 100).toFixed(0)}% (${fmtBytes(loaded)} / ${fmtBytes(total)})`;
        if (window.HFIcons) HFIcons.render(line);
      },
    });
    line.classList.add("ok");
    line.innerHTML = `<span data-icon="check"></span> "${r.name}" recebido — normalizando em background…`;
    if (window.HFIcons) HFIcons.render(line);
    $("#angle-name").value = "";
    // Poll project state so the angle reflects "ok" when the background
    // ffmpeg finalize finishes. SSE events would be nicer but this is reliable.
    let attempts = 0;
    const poll = async () => {
      attempts++;
      try {
        const p = await api(`/api/projects/${state.current.id}`);
        const target = (p.angles || []).find(a => a.filename === r.filename);
        if (target?.status === "ok") {
          state.current = p;
          line.innerHTML = `<span data-icon="check"></span> "${target.name}" pronto (${(target.duration || 0).toFixed(1)}s)`;
          if (window.HFIcons) HFIcons.render(line);
          await loadProject(state.current.id);
          return;
        }
        if (target?.status === "error") {
          line.classList.add("err");
          line.innerHTML = `<span data-icon="alert-triangle"></span> "${target.name}" falhou: ${target.error || "ffmpeg"}`;
          if (window.HFIcons) HFIcons.render(line);
          return;
        }
        if (attempts < 240) setTimeout(poll, 2000);  // up to 8 min
      } catch {
        if (attempts < 240) setTimeout(poll, 4000);
      }
    };
    poll();
  } catch (e) {
    line.classList.add("err");
    line.innerHTML = `<span data-icon="alert-triangle"></span> Ângulo: ${e.message}`;
    if (window.HFIcons) HFIcons.render(line);
  }
}

async function suggestMusic() {
  if (!state.current) return;
  const btn = $("#music-suggest-btn");
  btn.disabled = true;
  setBtnHTML(btn, "<span data-icon=&quot;sparkles&quot;></span> Pensando...");
  log("<span data-icon=&quot;play&quot;></span> music suggest");
  try {
    const s = await api(`/api/projects/${state.current.id}/music/suggest`, { method: "POST" });
    log(`<span data-icon=&quot;check&quot;></span> Sugestão: ${s.description}`, "ok");
    renderMusicSuggestion(s);
  } catch (e) {
    log(`✗ music suggest: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    setBtnHTML(btn, "<span data-icon=&quot;sparkles&quot;></span> Sugerir música");
  }
}

async function searchMusic() {
  if (!state.current) return;
  const list = $("#music-tracks");
  list.innerHTML = "";
  log("<span data-icon=&quot;play&quot;></span> music search");
  try {
    const r = await api(`/api/projects/${state.current.id}/music/search`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ limit: 10 }),
    });
    if (!r.provider) {
      log(`ℹ ${r.hint || "Nenhum provider configurado"}`, "");
      if (r.epidemic_search_url) {
        const a = $("#epidemic-link");
        a.href = r.epidemic_search_url;
        a.style.display = "inline-block";
        log("Use 'Abrir busca no site' pra pesquisar manualmente.", "");
      }
      return;
    }
    if (!r.tracks?.length) {
      log("Sem resultados.", "");
      return;
    }
    for (const t of r.tracks) {
      const li = document.createElement("li");
      li.innerHTML = `
        <div class="t-title">${escapeHtml(t.title)}<div class="t-meta">${escapeHtml(t.artist || "")} · ${t.bpm ? t.bpm + " BPM" : ""} · ${t.duration ? Math.round(t.duration) + "s" : ""}</div></div>
        ${t.preview_url ? `<audio controls src="${t.preview_url}"></audio>` : ""}
        <button class="btn-ghost" data-pick='${JSON.stringify(t).replace(/'/g, "&apos;")}'>Selecionar</button>
      `;
      li.querySelector("button").onclick = async (e) => {
        const track = JSON.parse(e.currentTarget.dataset.pick.replace(/&apos;/g, "'"));
        await api(`/api/projects/${state.current.id}/music/select`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ track }),
        });
        log(`Track selecionada: ${track.title}`, "ok");
      };
      list.appendChild(li);
    }
    log(`<span data-icon=&quot;check&quot;></span> ${r.tracks.length} faixas encontradas via ${r.provider}`, "ok");
  } catch (e) {
    log(`✗ music search: ${e.message}`, "err");
  }
}

async function exportFcpxml() {
  if (!state.current) return;
  log("<span data-icon=&quot;play&quot;></span> FCPXML export");
  try {
    const res = await api(`/api/projects/${state.current.id}/export/fcpxml`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        multicam: $("#fcpxml-multicam").checked,
        use_cuts: $("#fcpxml-cuts").checked,
        use_roughcut: $("#fcpxml-roughcut")?.checked || false,
        include_broll: $("#fcpxml-broll")?.checked || false,
        include_word_markers: $("#fcpxml-markers")?.checked || false,
        use_camera_plan: $("#fcpxml-plan")?.checked !== false,
        include_chapters: $("#fcpxml-chapters")?.checked !== false,
        include_soundbites: $("#fcpxml-bites")?.checked !== false,
        include_speakers: $("#fcpxml-speakers")?.checked !== false,
        include_questions: $("#fcpxml-questions")?.checked !== false,
      }),
    });
    log(`<span data-icon=&quot;check&quot;></span> FCPXML ${res.kind} · ${res.bytes} bytes`, "ok");
    const link = $("#fcpxml-link");
    link.href = res.url;
    link.style.display = "inline-block";
    setBtnHTML(link, `<span data-icon=&quot;download&quot;></span> Baixar ${res.export}`);
    refreshNleExport();
  } catch (e) {
    log(`✗ FCPXML: ${e.message}`, "err");
  }
}

// ---- Source-video pickers (burn, audio, music, render) --------------------

function annotateSourcePickers(p) {
  // Each option (graded/roughcut/source/highlights) is disabled when the
  // corresponding file doesn't exist yet. The label is annotated with a
  // small marker so the user sees what's actually pickable.
  const availability = {
    graded:     (p?.has_render || p?.has_cuts || p?.has_fillers || p?.has_lut) ? "ok" : "missing",
    roughcut:   p?.has_roughcut ? "ok" : "missing",
    source:     p?.source_filename ? "ok" : "missing",
    highlights: (p?.last_export || "").includes("highlight") ? "ok" : "maybe",
  };
  for (const sel of ["#render-source", "#burn-source", "#audio-source", "#music-source"]) {
    const el = document.querySelector(sel);
    if (!el) continue;
    for (const opt of el.options) {
      const status = availability[opt.value];
      const baseLabel = opt.value;
      if (status === "ok") {
        opt.textContent = baseLabel;
        opt.disabled = false;
      } else if (status === "missing") {
        opt.textContent = `${baseLabel} (não gerado)`;
        opt.disabled = true;
      } else {
        opt.textContent = `${baseLabel} (?)`;
        opt.disabled = false;
      }
    }
    // If currently-selected option is now disabled, fall back to first
    // enabled one.
    if (el.selectedOptions[0]?.disabled) {
      for (const opt of el.options) {
        if (!opt.disabled) { el.value = opt.value; break; }
      }
    }
  }
}

// ---- Color profile (LOG support) ------------------------------------------

const COLOR_PROFILE_LABELS = {
  rec709:   "Rec. 709",
  slog3:    "Sony S-Log3",
  clog3:    "Canon C-Log3",
  vlog:     "Panasonic V-Log",
  applelog: "Apple Log",
  logc:     "ARRI Log-C",
  custom:   "Custom LUT",
};

function syncColorProfileSelect(p) {
  const sel = $("#color-profile");
  if (!sel) return;
  const profile = p?.source_color_profile || "rec709";
  if (sel.value !== profile) sel.value = profile;
  const hint = $("#color-profile-hint");
  if (hint) {
    if (profile === "rec709") {
      hint.textContent = "";
    } else if (p?.color_profile_locked) {
      hint.textContent = "· definido manualmente";
    } else {
      hint.textContent = "· detectado automaticamente do arquivo";
    }
  }
}

async function saveColorProfile(profile) {
  if (!state.current) return;
  try {
    await api(`/api/projects/${state.current.id}/color-profile`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile }),
    });
    state.current.source_color_profile = profile;
    state.current.color_profile_locked = true;
    log(`<span data-icon=&quot;check&quot;></span> perfil de cor: ${COLOR_PROFILE_LABELS[profile] || profile}`, "ok");
    refreshNleExport();
    syncColorProfileSelect(state.current);
  } catch (e) {
    log(`✗ perfil de cor: ${e.message}`, "err");
  }
}

// ---- NLE export panel ------------------------------------------------------

function fmtTimecode(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = (s % 60).toFixed(2).padStart(5, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

async function refreshNleExport() {
  const card = $("#nle-card");
  if (!card || !state.current) return;
  const pills = $("#nle-pills");
  const warnings = $("#nle-warnings");
  const body = $("#nle-edl-body");
  const count = $("#nle-edl-count");
  try {
    const data = await api(`/api/projects/${state.current.id}/export/fcpxml/preview`);
    const a = data.available || {};
    const color = data.color || { profile: "rec709", is_log: false, label: "Rec. 709" };
    const has = (k) => a[k] ? "ok" : "muted";
    const colorPill = color.is_log
      ? `<span class="nle-pill log" title="${escapeHtml(color.fcp_lut || '')}"><span data-icon=&quot;palette&quot;></span> LOG · ${escapeHtml(color.label)}</span>`
      : `<span class="nle-pill muted"><span data-icon=&quot;palette&quot;></span> Rec. 709</span>`;
    pills.innerHTML = [
      colorPill,
      `<span class="nle-pill ${has("multicam")}">${a.multicam ? "✓" : "·"} multicam (${(a.angles || []).length} ângulos)</span>`,
      `<span class="nle-pill ${has("camera_plan")}">${a.camera_plan ? "✓" : "·"} plano de câmera${a.camera_plan ? ` · ${data.edl_count} cortes` : ""}</span>`,
      `<span class="nle-pill ${has("rough_cut")}">${a.rough_cut ? "✓" : "·"} rough cut</span>`,
      `<span class="nle-pill ${has("silence_cuts")}">${a.silence_cuts ? "✓" : "·"} silêncios</span>`,
      `<span class="nle-pill ${has("chapters")}">${a.chapters ? "✓" : "·"} chapters</span>`,
      `<span class="nle-pill ${has("soundbites")}">${a.soundbites ? "✓" : "·"} soundbites</span>`,
      `<span class="nle-pill ${has("questions")}">${a.questions ? "✓" : "·"} perguntas${a.questions_count ? ` (${a.questions_count})` : ""}</span>`,
      `<span class="nle-pill ${has("speakers")}">${a.speakers ? "✓" : "·"} speakers</span>`,
    ].join("");
    warnings.innerHTML = (data.warnings || []).map(w =>
      `<div class="nle-warn"><span data-icon=&quot;alert-triangle&quot;></span> ${escapeHtml(w)}</div>`
    ).join("");

    // Smart defaults: pre-check things that are available, hide irrelevant.
    const mc = $("#fcpxml-multicam");
    if (mc) mc.checked = !!a.multicam;
    const plan = $("#fcpxml-plan");
    if (plan) {
      plan.checked = !!a.camera_plan;
      plan.closest("label").style.display = a.multicam ? "" : "none";
    }
    const broll = $("#fcpxml-broll");
    if (broll) broll.closest("label").style.display = a.broll_placement ? "" : "none";
    const cuts = $("#fcpxml-cuts");
    if (cuts) cuts.checked = !!a.silence_cuts;
    const chap = $("#fcpxml-chapters");
    if (chap) chap.closest("label").style.display = a.chapters ? "" : "none";
    const bites = $("#fcpxml-bites");
    if (bites) bites.closest("label").style.display = a.soundbites ? "" : "none";
    const sp = $("#fcpxml-speakers");
    if (sp) sp.closest("label").style.display = a.speakers ? "" : "none";

    const edl = data.edl || [];
    count.textContent = String(edl.length);
    if (edl.length === 0) {
      body.innerHTML = a.multicam
        ? `<div class="muted">Sem plano de câmera ainda — rode <em>multicam-pick</em> antes de exportar pra ter cortes automáticos.</div>`
        : `<div class="muted">Não é multicam — XML vai ter um clip único do source.</div>`;
    } else {
      body.innerHTML = `<table class="nle-edl-table">
        <thead><tr><th>De</th><th>→</th><th>Até</th><th>Dur</th><th>Câmera</th><th>Razão</th></tr></thead>
        <tbody>${edl.map(r => `<tr>
          <td>${fmtTimecode(r.start)}</td><td>→</td><td>${fmtTimecode(r.end)}</td>
          <td>${r.duration.toFixed(2)}s</td>
          <td><strong>${escapeHtml(r.angle_name || "—")}</strong></td>
          <td class="muted">${escapeHtml(r.reason || "")}</td>
        </tr>`).join("")}</tbody>
      </table>`;
    }
  } catch (e) {
    if (pills) pills.innerHTML = `<span class="nle-pill muted">preview indisponível</span>`;
  }
}

// ---- Command palette (⌘K) ---------------------------------------------------

const COMMANDS = [
  { id: "new",       title: "Novo projeto",                         section: "Projeto", icon: "+", run: () => newProject() },
  { id: "delete",    title: "Excluir projeto atual",                section: "Projeto", icon: "🗑", run: () => deleteProject() },
  { id: "duplicate", title: "Duplicar projeto",                     section: "Projeto", icon: "🪞", run: () => duplicateProject() },
  { id: "snapshot",  title: "Tirar snapshot",                       section: "Projeto", icon: "📸", run: () => takeSnapshot() },
  { id: "theme",     title: "Alternar tema (claro/escuro)",         section: "App",     icon: "◐", run: () => $("#theme-toggle")?.click() },

  { id: "transcribe", title: "Transcrever vídeo (Whisper)",         section: "Pipeline", icon: "📝", run: () => runStage("transcribe") },
  { id: "silence",    title: "Detectar silêncios (sentence-safe)",  section: "Pipeline", icon: "🔇", run: () => runStage("silence") },
  { id: "fillers",    title: "Remover muletas",                     section: "Pipeline", icon: "✂", run: () => runStage("fillers") },
  { id: "apply",      title: "Aplicar edição → graded.mp4",         section: "Pipeline", icon: "⚙", run: () => runStage("apply") },
  { id: "render",     title: "Renderizar com Hyperframes",          section: "Pipeline", icon: "🎬", run: () => runStage("render") },

  { id: "sb",        title: "Extrair soundbites",                   section: "Eddie",    icon: "🎯", run: () => extractSoundbites() },
  { id: "story",     title: "Propor roteiro",                       section: "Eddie",    icon: "📜", run: () => buildStory() },
  { id: "rc",        title: "Gerar rough cut",                      section: "Eddie",    icon: "⚡", run: () => buildRoughCut() },
  { id: "hl",        title: "Highlights reel",                      section: "Eddie",    icon: "⚡", run: () => buildHighlights() },
  { id: "hook",      title: "Gerar hook 4s",                        section: "Eddie",    icon: "🪝", run: () => makeHook() },
  { id: "social",    title: "Gerar social copy",                    section: "Eddie",    icon: "✍", run: () => generateSocialCopy() },

  { id: "music",     title: "Sugerir música",                       section: "Áudio",    icon: "🎵", run: () => suggestMusic() },
  { id: "musearch",  title: "Buscar música",                        section: "Áudio",    icon: "🔎", run: () => searchMusic() },
  { id: "mix",       title: "Mixar música com voz",                 section: "Áudio",    icon: "🎚", run: () => mixMusic() },
  { id: "audio",     title: "Exportar áudio (MP3/M4A)",             section: "Áudio",    icon: "🎧", run: () => exportAudio() },

  { id: "podcast",   title: "Podcast pipeline 1-click",             section: "Podcast",  icon: "🎙", run: () => runPodcastPipeline() },
  { id: "speakers",  title: "Detectar falas",                       section: "Podcast",  icon: "🎤", run: () => detectSpeakers() },
  { id: "levels",    title: "Nivelar speakers",                     section: "Podcast",  icon: "🎚", run: () => levelSpeakers() },
  { id: "chapters",  title: "Capítulos por tópico",                 section: "Podcast",  icon: "📑", run: () => detectChapters() },

  { id: "vlog",      title: "Vlog 1-click (auto-monta o top)",      section: "Vlog",     icon: "⚡", run: () => vlogAutoPipeline() },
  { id: "vltrans",   title: "Transcrever todos os clipes",          section: "Vlog",     icon: "📝", run: () => vlogTranscribeAll() },
  { id: "vlnar",     title: "Sugerir narrativas",                   section: "Vlog",     icon: "🧭", run: () => vlogProposeNarratives() },
  { id: "vlmus",     title: "Música pro vlog",                      section: "Vlog",     icon: "🎵", run: () => vlogMusicSuggest() },
  { id: "faces",     title: "Identificar pessoas",                  section: "Vlog",     icon: "👥", run: () => detectFaceIdentities() },

  { id: "shorts",    title: "Gerar N shorts",                       section: "Multicam", icon: "✂", run: () => generateShorts() },
  { id: "mcpick",    title: "Multicam: escolher câmera por turno",  section: "Multicam", icon: "🎬", run: () => multicamPick() },
  { id: "mcsync",    title: "Multicam: sync de áudio",              section: "Multicam", icon: "🔗", run: () => multicamSync() },
  { id: "mcrender",  title: "Multicam: renderizar",                 section: "Multicam", icon: "🎞", run: () => multicamRender() },

  { id: "fcpxml",    title: "Exportar FCPXML",                      section: "Exportar", icon: "📄", run: () => exportFcpxml() },
  { id: "premiere",  title: "Exportar Premiere/Resolve XML",        section: "Exportar", icon: "📄", run: () => exportPremiere() },
  { id: "srt",       title: "Exportar SRT",                         section: "Exportar", icon: "📝", run: () => exportCaptions("srt") },
  { id: "vtt",       title: "Exportar VTT",                         section: "Exportar", icon: "📝", run: () => exportCaptions("vtt") },
  { id: "ass",       title: "Exportar ASS karaoke",                 section: "Exportar", icon: "📝", run: () => exportCaptions("ass") },
  { id: "burn",      title: "Queimar legendas no MP4",              section: "Exportar", icon: "🔥", run: () => burnCaptions() },
  { id: "yt",        title: "Thumbnail YouTube",                    section: "Exportar", icon: "⭐", run: () => peakThumb() },
  { id: "mp3",       title: "MP3 com chapter markers",              section: "Exportar", icon: "🎧", run: () => $("#audio-btn")?.click() },
  { id: "bundle",    title: "Bundle .zip com tudo",                 section: "Exportar", icon: "📦", run: () => exportBundle() },
];

let cmdkIndex = 0;
let cmdkFiltered = COMMANDS;

function openCmdK() {
  $("#cmdk-backdrop").classList.remove("hidden");
  const inp = $("#cmdk-input");
  inp.value = "";
  cmdkFiltered = COMMANDS;
  cmdkIndex = 0;
  renderCmdK();
  setTimeout(() => inp.focus(), 30);
}

function closeCmdK() {
  $("#cmdk-backdrop").classList.add("hidden");
}

function renderCmdK() {
  const list = $("#cmdk-list");
  if (!cmdkFiltered.length) {
    list.innerHTML = `<li class="ck-empty">Nada encontrado</li>`;
    return;
  }
  list.innerHTML = cmdkFiltered.map((c, i) => `
    <li data-idx="${i}" ${i === cmdkIndex ? 'aria-selected="true"' : ""}>
      <span class="ck-icon">${c.icon}</span>
      <span class="ck-title">${escapeHtml(c.title)}</span>
      <span class="ck-section">${escapeHtml(c.section)}</span>
    </li>
  `).join("");
  for (const li of list.querySelectorAll("li[data-idx]")) {
    li.onclick = () => {
      const cmd = cmdkFiltered[parseInt(li.dataset.idx, 10)];
      closeCmdK();
      try { cmd.run(); } catch (e) { log(`✗ ${cmd.id}: ${e.message}`, "err"); }
    };
  }
}

function filterCmdK(q) {
  const needle = (q || "").trim().toLowerCase();
  if (!needle) {
    cmdkFiltered = COMMANDS;
  } else {
    cmdkFiltered = COMMANDS.filter(c =>
      c.title.toLowerCase().includes(needle) ||
      c.section.toLowerCase().includes(needle) ||
      c.id.toLowerCase().includes(needle)
    );
  }
  cmdkIndex = 0;
  renderCmdK();
}

function bindCmdK() {
  document.addEventListener("keydown", (e) => {
    const meta = e.metaKey || e.ctrlKey;
    if (meta && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const isOpen = !$("#cmdk-backdrop").classList.contains("hidden");
      if (isOpen) closeCmdK(); else openCmdK();
      return;
    }
    const isOpen = !$("#cmdk-backdrop").classList.contains("hidden");
    if (!isOpen) return;
    if (e.key === "Escape") { e.preventDefault(); closeCmdK(); }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      cmdkIndex = Math.min(cmdkFiltered.length - 1, cmdkIndex + 1);
      renderCmdK();
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      cmdkIndex = Math.max(0, cmdkIndex - 1);
      renderCmdK();
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const cmd = cmdkFiltered[cmdkIndex];
      if (cmd) { closeCmdK(); try { cmd.run(); } catch (err) { log(`✗ ${cmd.id}: ${err.message}`, "err"); } }
    }
  });
  $("#cmdk-input").addEventListener("input", (e) => filterCmdK(e.target.value));
  $("#cmdk-backdrop").addEventListener("click", (e) => {
    if (e.target === $("#cmdk-backdrop")) closeCmdK();
  });
}

// ---- Workspace navigation (uma seção visível por vez) ----------------------

const SECTION_CATS = {
  overview:  ["overview"],
  upload:    ["upload"],
  pipeline:  ["pipeline"],
  edit:      ["edit"],
  soundbites:["soundbites"],
  podcast:   ["podcast"],
  vlog:      ["vlog"],
  multicam:  ["multicam"],
  reels:     ["reels"],
  brand:     ["brand"],
  music:     ["music"],
  export:    ["export"],
  all:       ["overview", "upload", "pipeline", "edit", "soundbites", "podcast", "vlog", "multicam", "reels", "brand", "music", "export"],
};

function activeSection() {
  return localStorage.getItem("hfvp.section") || "overview";
}

// The active project "kind" drives which sidebar sections are visible.
// It's persisted server-side (state.kind) — frozen for the project unless
// the user explicitly changes it via the topbar "Tipo" pill.
function activeKind() {
  return (state.current && state.current.kind) || "podcast";
}

// Kept under the old name so all existing call sites keep working,
// but it now reads from project.kind instead of localStorage.
function applyModeFiltering() {
  const kind = activeKind();
  for (const btn of document.querySelectorAll(".ws-section")) {
    const modes = (btn.dataset.modes || "").split(/\s+/);
    btn.style.display = modes.includes(kind) ? "" : "none";
  }
  // Show the angle-uploader row whenever the workflow could plausibly
  // use multiple cameras — multicam podcast, reels (multi-cam vertical
  // edits are common), and advanced. Plain podcast (1 câm) stays hidden.
  const angleRow = document.getElementById("angle-row");
  if (angleRow) {
    const showAngles = kind === "multicam_podcast" || kind === "reels" || kind === "general";
    angleRow.classList.toggle("hidden", !showAngles);
  }
  // Update topbar kind pill to match.
  updateKindPill(kind);
  // If the current section is now hidden, fall back to overview.
  const currentBtn = document.querySelector(`.ws-section[data-section="${activeSection()}"]`);
  if (currentBtn && currentBtn.dataset.modes && !currentBtn.dataset.modes.split(/\s+/).includes(kind)) {
    setSection("overview");
  }
}

const KIND_META = {
  podcast:          { label: "Podcast",          icon: "mic" },
  multicam_podcast: { label: "Podcast multicam", icon: "film" },
  reels:            { label: "Reels",            icon: "sparkles" },
  vlog:             { label: "Vlog",             icon: "video" },
  general:          { label: "Tudo",             icon: "settings" },
};

function updateKindPill(kind) {
  const pill = document.getElementById("topbar-kind");
  if (!pill) return;
  const meta = KIND_META[kind] || KIND_META.podcast;
  const iconEl = pill.querySelector(".kp-icon");
  const labelEl = pill.querySelector(".kp-label");
  if (iconEl) {
    iconEl.dataset.icon = meta.icon;
    if (window.HFIcons) HFIcons.render(pill);
  }
  if (labelEl) labelEl.textContent = meta.label;
}

async function setKind(kind) {
  if (!state.current || !KIND_META[kind]) return;
  try {
    const updated = await api(`/api/projects/${state.current.id}/kind`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind }),
    });
    state.current.kind = updated.kind;
    applyModeFiltering();
    toast?.(`Tipo do projeto: ${KIND_META[kind].label}`, "ok");
  } catch (e) {
    toast?.(`Falha: ${e.message}`, "err");
  }
}

// Backwards-compat shim: anything still calling activeMode/setMode/etc
// continues to work without further refactor (they now go through kind).
const activeMode = activeKind;
function setMode(_) { /* no-op — sidebar is now driven by project.kind */ }
function inferMode(p) { return (p && p.kind) || "podcast"; }
function maybeSuggestMulticamMode() { /* superseded by explicit kind choice */ }

function setSection(section) {
  const cats = new Set(SECTION_CATS[section] || SECTION_CATS.overview);
  for (const card of document.querySelectorAll(".card[data-cat]")) {
    card.style.display = cats.has(card.dataset.cat) ? "" : "none";
  }
  for (const btn of document.querySelectorAll(".ws-section")) {
    btn.classList.toggle("active", btn.dataset.section === section);
  }
  localStorage.setItem("hfvp.section", section);
  const label = document.querySelector(`.ws-section[data-section="${section}"] span:last-child`);
  const drawerLabel = $("#ws-drawer-label");
  if (drawerLabel && label) drawerLabel.textContent = label.textContent;
  const topbarSec = $("#topbar-section");
  if (topbarSec && label) topbarSec.textContent = label.textContent;
  // Close mobile drawer on selection.
  document.body.classList.remove("ws-drawer-open");
  if (section === "overview") refreshOverview();
  if (section === "export") refreshNleExport();
  if (section === "reels") refreshReelsOnLoad();
}

function refreshHeader() {
  const p = state.current;
  if (!p) return;
  const status = $("#topbar-status");
  if (status) {
    if (p.render_active) {
      status.style.display = "";
      status.dataset.tone = "warn";
      status.textContent = "Renderizando";
    } else if (p.has_render) {
      status.style.display = "";
      status.dataset.tone = "success";
      status.textContent = "Pronto";
    } else {
      status.style.display = "none";
    }
  }
  // Mirror next-step into topbar
  const tbNext = $("#topbar-next-step");
  if (tbNext && typeof computeNextStep === "function") {
    const ns = computeNextStep(p);
    if (ns) {
      tbNext.style.display = "";
      tbNext.innerHTML = `${ns.msg} <span data-icon="arrow-right" data-icon-size="14"></span>`;
      tbNext.onclick = () => setSection(ns.section);
    } else {
      tbNext.style.display = "none";
    }
  }
}

function bindSections() {
  for (const btn of document.querySelectorAll(".ws-section")) {
    btn.addEventListener("click", () => setSection(btn.dataset.section));
  }
  const drawer = document.querySelector(".ws-drawer-toggle");
  if (drawer) {
    drawer.addEventListener("click", () => {
      const open = document.body.classList.toggle("ws-drawer-open");
      drawer.setAttribute("aria-expanded", String(open));
    });
  }
  // Topbar pill — opens the kind popover.
  const pill = document.getElementById("topbar-kind");
  if (pill) pill.addEventListener("click", openKindPopover);
  // Topbar folder button — toggles the project files panel (previously
  // a permanent card; now hidden by default, surfaced on demand).
  const filesBtn = document.getElementById("topbar-files");
  if (filesBtn) filesBtn.addEventListener("click", () => {
    const card = document.getElementById("media-card");
    if (!card) return;
    const wasHidden = card.classList.contains("hidden");
    card.classList.toggle("hidden");
    if (wasHidden) {
      refreshMediaList?.();
      card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  // Bind every kind-card click (both in the create modal and the popover)
  // via event delegation; selection logic differs per host.
  document.addEventListener("click", (e) => {
    const card = e.target.closest("#kind-popover .kind-card, #kind-popover .cp-advanced");
    if (card) {
      const kind = card.dataset.kind;
      if (kind) { setKind(kind); closeKindPopover(); }
    }
  });
  // Drop the legacy localStorage key — kind now lives on the project.
  try { localStorage.removeItem("hfvp.mode"); } catch {}
  // Initial filter pass uses the current project's kind (or "podcast" if
  // no project is open yet).
  applyModeFiltering();
}

function openKindPopover() {
  const pop = document.getElementById("kind-popover");
  const pill = document.getElementById("topbar-kind");
  if (!pop || !pill) return;
  // Position the popover anchored to the pill.
  const rect = pill.getBoundingClientRect();
  pop.style.top = `${rect.bottom + 6}px`;
  pop.style.right = `${window.innerWidth - rect.right}px`;
  pop.classList.remove("hidden");
  // Mark the current kind as selected.
  const current = activeKind();
  for (const c of pop.querySelectorAll(".kind-card, .cp-advanced")) {
    c.classList.toggle("selected", c.dataset.kind === current);
  }
  // Click outside closes it.
  setTimeout(() => document.addEventListener("click", _kindPopoverOutside), 0);
}
function closeKindPopover() {
  const pop = document.getElementById("kind-popover");
  if (pop) pop.classList.add("hidden");
  document.removeEventListener("click", _kindPopoverOutside);
}
function _kindPopoverOutside(e) {
  if (e.target.closest("#kind-popover") || e.target.closest("#topbar-kind")) return;
  closeKindPopover();
}

// ---- Pre-flight checks (disable actions whose prerequisites are missing) ---

const PREREQS = {
  story:           p => p.has_soundbites,
  roughcut:        p => p.has_story || p.has_soundbites,
  highlights:      p => p.has_story,
  shorts:          p => p.has_story || p.has_soundbites,
  multicam_pick:   p => (p.angles || []).length >= 1 && p.has_transcript && p.has_speakers,
  multicam_render: p => p.has_camera_plan,
  fcpxml:          p => !!p.source_filename,
  burn_caps:       p => p.has_render && p.has_transcript,
  podcast_1click:  p => !!p.source_filename,   // works single-cam too; angles make it richer
};

const PREREQ_LABELS = {
  story:           "Requer soundbites",
  roughcut:        "Requer roteiro ou soundbites",
  highlights:      "Requer roteiro",
  shorts:          "Requer roteiro ou soundbites",
  multicam_pick:   "Precisa: ângulos + transcrição + speakers",
  multicam_render: "Rode 'escolher câmera' primeiro",
  fcpxml:          "Suba o vídeo de origem",
  burn_caps:       "Precisa de render + transcrição",
  podcast_1click:  "Suba o vídeo de origem primeiro",
};

function applyPrereqs() {
  const p = state.current;
  if (!p) return;
  for (const btn of document.querySelectorAll("[data-prereq]")) {
    const key = btn.dataset.prereq;
    const check = PREREQS[key];
    if (!check) continue;
    const ok = check(p);
    btn.disabled = !ok;
    btn.classList.toggle("disabled", !ok);
    if (!ok) {
      btn.title = PREREQ_LABELS[key] || "Pré-requisito faltando";
      if (!btn.dataset.origHint && btn.nextElementSibling?.classList?.contains("prereq-hint")) {
        // already shown
      } else if (!btn.querySelector(".prereq-hint")) {
        // Append inline hint on first miss.
        const hint = document.createElement("span");
        hint.className = "prereq-hint";
        hint.textContent = " · " + (PREREQ_LABELS[key] || "indisponível");
        btn.appendChild(hint);
      }
    } else {
      btn.title = "";
      const h = btn.querySelector(".prereq-hint");
      if (h) h.remove();
    }
  }
}

function computeNextStep(p) {
  if (!p) return null;
  const mode = activeMode();
  const renders = p.has_render;
  if (!p.source_filename) return { msg: "Suba o vídeo de origem", section: "upload" };
  // Fresh project with source uploaded → point at the 1-click pipeline
  // (works for single-cam too; angles make the output richer).
  if (!p.has_transcript) {
    return (mode === "podcast" || mode === "multicam")
      ? { msg: "Roda a edição automática (Multicam → 1-clique)", section: "multicam" }
      : { msg: "Rode a transcrição", section: "edit" };
  }
  if (mode === "multicam" && (p.angles || []).length === 0)
    return { msg: "Suba os outros ângulos", section: "multicam" };
  if (mode === "multicam" && !p.has_speakers)
    return { msg: "Detecte os speakers (ou roda o 1-clique)", section: "multicam" };
  if (mode === "multicam" && !p.has_camera_plan)
    return { msg: "Rode 'escolher câmera por turno' (ou o 1-clique)", section: "multicam" };
  if (mode === "multicam" && p.has_camera_plan && !renders)
    return { msg: "Renderize o multicam", section: "multicam" };
  if (mode === "podcast" && !p.has_cuts && !p.has_fillers)
    return { msg: "Detecte silêncios e muletas", section: "edit" };
  if (mode === "podcast" && !p.has_soundbites)
    return { msg: "Extraia soundbites pra montar um rough cut", section: "soundbites" };
  if (renders && !p.last_export?.endsWith?.(".fcpxml"))
    return { msg: "Exporte FCPXML pro Final Cut", section: "export" };
  return { msg: "Pronto — refinar no Final Cut", section: "export" };
}

// Compute a rough ETA per step using the source duration as a baseline.
// These are "wall-time multipliers" against the source duration based on
// what I measured on a typical Mac M1: transcribe ≈ 0.15× (Whisper API),
// multicam render ≈ 0.6× (libx264 veryfast on 1080p), etc.
function podcastEtaHint(p) {
  const dur = (p && p.source_duration) || 0;
  if (!dur) return "~5–15 min";
  const transcribe = dur * 0.15;
  const apply = dur * 0.10;
  const level = dur * 0.05;
  const sync = Math.min(dur * 0.04, 45);
  const render = dur * 0.6;
  const total = transcribe + apply + level + sync + render + 30;  // +30s overhead
  if (total < 60) return `~${Math.round(total)}s`;
  if (total < 600) return `~${Math.round(total / 60)} min`;
  return `~${Math.round(total / 60)} min`;
}

function refreshOverview() {
  const root = $("#overview-grid");
  if (!root) return;
  const p = state.current;
  if (!p) { root.innerHTML = ""; return; }

  applyPrereqs();

  // Next-step hint — when it points at the 1-click pipeline, render an
  // upsell that's hard to miss so the layman knows exactly where to go.
  const next = computeNextStep(p);
  const nextEl = $("#ph-next-step");
  if (nextEl) {
    if (next) {
      nextEl.style.display = "";
      const pointsAt1Click = /1\-clique|1-click/i.test(next.msg);
      if (pointsAt1Click) {
        const eta = podcastEtaHint(p);
        nextEl.classList.add("ns-cta");
        nextEl.innerHTML = `
          <div class="ns-cta-icon"><span data-icon="zap" data-icon-size="20"></span></div>
          <div class="ns-cta-body">
            <div class="ns-cta-title">Edição automática disponível</div>
            <div class="ns-cta-sub">A pipeline 1-clique transcreve, corta, sincroniza câmeras, escolhe ângulos e gera o FCPXML. ${eta}.</div>
          </div>
          <span class="ns-cta-go">Abrir Multicam <span data-icon="arrow-right" data-icon-size="14"></span></span>`;
        if (window.HFIcons) HFIcons.render(nextEl);
      } else {
        nextEl.classList.remove("ns-cta");
        nextEl.innerHTML = `<span class="ns-arrow">→</span> <span class="ns-msg">${escapeHtml(next.msg)}</span> <span class="ns-link">Abrir</span>`;
      }
      nextEl.onclick = () => setSection(next.section);
    } else {
      nextEl.style.display = "none";
    }
  }

  function cell(label, value, sub, cls, target) {
    return `<button class="ov-card cell-link ${cls || ""}" type="button" data-target="${target || ""}">
      <div class="ov-label">${escapeHtml(label)}</div>
      <div class="ov-value">${escapeHtml(String(value))}</div>
      ${sub ? `<div class="ov-sub">${escapeHtml(sub)}</div>` : ""}
    </button>`;
  }

  const dur = p.source_duration ? `${p.source_duration.toFixed(1)}s` : "—";
  const modeLabel = (p.clips && p.clips.length) ? "Vlog" : ((p.angles || []).length ? "Multicam" : "Single");
  const cells = [];
  cells.push(cell("Modo", modeLabel, modeLabel === "Vlog" ? `${p.clips.length} clipes` : (p.source_filename || "sem upload"), "", modeLabel === "Vlog" ? "vlog" : ((p.angles || []).length ? "multicam" : "upload")));
  cells.push(cell("Duração", dur, p.source_filename || "", "", "upload"));
  cells.push(cell("Transcrição", p.has_transcript ? "ok" : "—", p.has_transcript ? "" : "rode a transcrição", p.has_transcript ? "ok" : "muted", "edit"));
  cells.push(cell("Cortes", p.has_cuts ? "ok" : (p.has_fillers ? "fillers" : "—"), "", (p.has_cuts || p.has_fillers) ? "ok" : "muted", "edit"));
  cells.push(cell("Soundbites", p.has_soundbites ? "ok" : "—", "", p.has_soundbites ? "ok" : "muted", "soundbites"));
  cells.push(cell("Roteiro", p.has_story ? "ok" : "—", "", p.has_story ? "ok" : "muted", "soundbites"));
  cells.push(cell("Rough cut", p.has_roughcut ? "ok" : "—", "", p.has_roughcut ? "ok" : "muted", "export"));
  cells.push(cell("Speakers", p.has_speakers ? "ok" : "—", "", p.has_speakers ? "ok" : "muted", "podcast"));
  cells.push(cell("Multicam", (p.angles || []).length, (p.angles || []).length ? (p.has_camera_plan ? "plano ok" : "sem plano") : "sem ângulos", (p.angles || []).length ? (p.has_camera_plan ? "ok" : "warn") : "muted", "multicam"));
  cells.push(cell("Brand", p.has_brand ? "ok" : "—", "", p.has_brand ? "ok" : "muted", "brand"));
  cells.push(cell("Render", p.has_render ? "ok" : "—", p.has_render ? (p.last_export || "") : "rode o render", p.has_render ? "ok" : "muted", "export"));
  if (p.render_active) cells.push(cell("Status", "renderizando", "", "warn"));

  root.innerHTML = cells.join("");
  for (const btn of root.querySelectorAll(".cell-link")) {
    const target = btn.dataset.target;
    if (target) btn.addEventListener("click", () => setSection(target));
  }
}

// ---- (legado) Workflow filter horizontal — ainda no DOM mas escondido -----
const WORKFLOW_PRESETS = {
  caption:  ["upload", "edit", "brand", "export"],
  podcast:  ["upload", "edit", "podcast", "brand", "export"],
  vlog:     ["vlog", "brand", "music", "export"],
  multicam: ["upload", "edit", "multicam", "export"],
  eddie:    ["upload", "edit", "soundbites", "brand", "export"],
  all:      ["upload", "edit", "soundbites", "podcast", "vlog", "multicam", "brand", "music", "export"],
};

const ALL_CATS = WORKFLOW_PRESETS.all;

function activeCats() {
  try {
    const raw = localStorage.getItem("hfvp.workflow_cats");
    if (raw) return JSON.parse(raw);
  } catch {}
  return ALL_CATS.slice();
}

function activePreset() {
  return localStorage.getItem("hfvp.workflow_preset") || "all";
}

function applyWorkflow(cats) {
  // Show / hide cards by data-cat
  const set = new Set(cats);
  for (const card of document.querySelectorAll(".card[data-cat]")) {
    const c = card.dataset.cat;
    card.style.display = set.has(c) ? "" : "none";
  }
  // Mark cat buttons as active / muted
  for (const btn of document.querySelectorAll(".wb-cat")) {
    if (set.has(btn.dataset.cat)) {
      btn.classList.add("active");
      btn.classList.remove("muted-cat");
    } else {
      btn.classList.remove("active");
      btn.classList.add("muted-cat");
    }
  }
  localStorage.setItem("hfvp.workflow_cats", JSON.stringify(cats));
}

function setPreset(preset) {
  const cats = WORKFLOW_PRESETS[preset] || WORKFLOW_PRESETS.all;
  for (const btn of document.querySelectorAll(".wb-preset")) {
    btn.classList.toggle("active", btn.dataset.preset === preset);
  }
  applyWorkflow(cats);
  localStorage.setItem("hfvp.workflow_preset", preset);
}

function bindWorkflow() {
  for (const btn of document.querySelectorAll(".wb-preset")) {
    btn.addEventListener("click", () => setPreset(btn.dataset.preset));
  }
  for (const btn of document.querySelectorAll(".wb-cat")) {
    btn.addEventListener("click", () => {
      const cur = new Set(activeCats());
      const cat = btn.dataset.cat;
      if (cur.has(cat)) cur.delete(cat);
      else cur.add(cat);
      // toggling individual cats moves us into "custom" preset
      for (const p of document.querySelectorAll(".wb-preset")) {
        p.classList.toggle("active", false);
      }
      localStorage.removeItem("hfvp.workflow_preset");
      applyWorkflow(Array.from(cur));
    });
  }
  // Initial state: restore last preset, fall back to active cats
  const preset = activePreset();
  if (preset && WORKFLOW_PRESETS[preset]) {
    setPreset(preset);
  } else {
    applyWorkflow(activeCats());
  }
}

function bind() {
  bindCmdK();
  bindSections();
  // Keep the legacy filter wiring alive in case data exists, but hidden:
  try { bindWorkflow(); } catch {}
  $("#cmdk-open")?.addEventListener("click", () => openCmdK());
  $("#topbar-cmdk")?.addEventListener("click", () => openCmdK());
  // Theme toggle (dark ↔ light, persisted in localStorage)
  const themeBtn = $("#theme-toggle");
  if (themeBtn) {
    const setIcon = () => {
      const isLight = document.documentElement.getAttribute("data-theme") === "light";
      themeBtn.innerHTML = `<span data-icon="${isLight ? "sun" : "moon"}"></span>`;
    };
    setIcon();
    themeBtn.onclick = () => {
      const isLight = document.documentElement.getAttribute("data-theme") === "light";
      const next = isLight ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("hfvp.theme", next);
      setIcon();
    };
  }

  $("#new-project").onclick = newProject;
  $("#empty-new").onclick = newProject;
  $("#empty-cmdk")?.addEventListener("click", () => openCmdK());
  $("#delete-project").onclick = deleteProject;
  $("#save-brand").onclick = saveBrand;
  $("#do-export").onclick = doExport;

  const fi = $("#file-input");
  $("#dropzone").addEventListener("click", () => fi.click());
  $("#dropzone").addEventListener("dragover", (e) => { e.preventDefault(); });
  $("#dropzone").addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer?.files?.[0]) uploadFile(e.dataTransfer.files[0]);
  });
  fi.addEventListener("change", () => fi.files[0] && uploadFile(fi.files[0]));

  const li = $("#lut-input");
  $("#lut-zone").addEventListener("click", () => li.click());
  li.addEventListener("change", () => li.files[0] && uploadLut(li.files[0]));

  const cp = $("#color-profile");
  if (cp) cp.addEventListener("change", () => saveColorProfile(cp.value));

  // angle uploader — name input is OUTSIDE the dropzone now so we don't
  // re-trigger the file picker when the user types in the name field.
  const ai = $("#angle-input");
  const az = $("#angle-zone");
  if (az) {
    az.addEventListener("click", () => ai.click());
    az.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ai.click(); }
    });
    az.addEventListener("dragover", (e) => { e.preventDefault(); az.classList.add("drag"); });
    az.addEventListener("dragleave", () => az.classList.remove("drag"));
    az.addEventListener("drop", (e) => {
      e.preventDefault(); az.classList.remove("drag");
      if (e.dataTransfer?.files?.[0]) uploadAngle(e.dataTransfer.files[0]);
    });
  }
  ai.addEventListener("change", () => ai.files[0] && uploadAngle(ai.files[0]));

  // music + fcpxml + premiere + soundbites + story + roughcut
  $("#music-suggest-btn").onclick = suggestMusic;
  $("#music-search-btn").onclick = searchMusic;
  $("#fcpxml-btn").onclick = exportFcpxml;
  $("#premiere-btn").onclick = exportPremiere;
  $("#soundbites-btn").onclick = extractSoundbites;
  $("#story-btn").onclick = buildStory;
  $("#roughcut-btn").onclick = buildRoughCut;
  $("#place-broll-btn").onclick = placeBroll;
  $("#smart-reframe-btn").onclick = smartReframe;
  $("#srt-btn").onclick = () => exportCaptions("srt");
  $("#vtt-btn").onclick = () => exportCaptions("vtt");
  $("#snap-take-btn").onclick = takeSnapshot;
  $("#save-preset-btn").onclick = saveBrandPreset;
  $("#apply-preset-btn").onclick = applyBrandPreset;
  $("#bundle-btn").onclick = exportBundle;
  $("#dup-btn").onclick = duplicateProject;
  $("#speakers-btn").onclick = detectSpeakers;
  $("#speakers-level-btn").onclick = levelSpeakers;
  $("#chapters-btn").onclick = detectChapters;
  $("#multicam-sync-btn").onclick = multicamSync;

  const cpb = $("#media-copy-path");
  if (cpb) cpb.onclick = () => {
    const p = $("#media-project-path")?.dataset?.path || $("#media-project-path")?.textContent;
    if (p && p !== "—") copyToClipboard(p);
  };
  const rrb = $("#media-reveal-root");
  if (rrb) rrb.onclick = () => revealInFinder(null);
  $("#podcast-pipeline-btn").onclick = runPodcastPipeline;
  $("#yt-desc-btn").onclick = (e) => { e.preventDefault(); downloadYoutubeDescription(); };
  $("#bite-thumbs-btn").onclick = generateBiteThumbs;
  $("#shorts-btn").onclick = generateShorts;
  $("#vlog-transcribe-all").onclick = vlogTranscribeAll;
  $("#vlog-narratives-btn").onclick = vlogProposeNarratives;
  $("#vlog-auto-btn").onclick = vlogAutoPipeline;
  $("#vlog-music-btn").onclick = vlogMusicSuggest;
  $("#face-ids-btn").onclick = detectFaceIdentities;
  $("#people-thumbs-btn").onclick = generatePeopleThumbs;
  $("#subject-tl-btn").onclick = buildSourceSubjectTimeline;
  $("#multicam-pick-btn").onclick = multicamPick;
  $("#multicam-render-btn").onclick = multicamRender;
  const ci = $("#clip-input");
  if (ci) {
    $("#clip-zone").addEventListener("click", () => ci.click());
    ci.addEventListener("change", () => ci.files.length && uploadClips(Array.from(ci.files)));
    $("#clip-zone").addEventListener("dragover", (e) => e.preventDefault());
    $("#clip-zone").addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer?.files?.length) uploadClips(Array.from(e.dataTransfer.files));
    });
  }
  $("#thumbs-btn").onclick = chapterThumbs;
  $("#tx-clear-btn").onclick = txClear;
  $("#tx-keep-btn").onclick = txKeep;
  $("#hl-btn").onclick = buildHighlights;
  $("#social-btn").onclick = generateSocialCopy;
  $("#apply-template-btn").onclick = applyTemplate;
  $("#audio-btn").onclick = exportAudio;
  $("#cancel-render-btn").onclick = cancelRender;
  $("#ass-btn").onclick = () => exportCaptions("ass");
  $("#burn-btn").onclick = burnCaptions;
  $("#hook-btn").onclick = makeHook;
  $("#peak-thumb-btn").onclick = peakThumb;
  $("#emojify-btn").onclick = emojifyCaptions;
  $("#waveform-btn").onclick = showWaveform;
  $("#archive-btn").onclick = archiveProject;
  $("#mix-btn").onclick = mixMusic;

  // Reels
  bindReels();
  const mf = $("#music-file");
  $("#music-zone").addEventListener("click", () => mf.click());
  mf.addEventListener("change", () => mf.files[0] && uploadMusic(mf.files[0]));
  $("#search-q").addEventListener("input", (e) => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => runSearch(e.target.value.trim()), 200);
  });
  attachVideoSync();

  for (const btn of $$("[data-run]")) {
    btn.addEventListener("click", () => runStage(btn.dataset.run));
  }
}

// ── 1-click podcast multicam pipeline ──────────────────────────────────────

const POD1CLICK_STEPS = [
  { key: "transcribe",      label: "Transcrever (Whisper)" },
  { key: "speakers",        label: "Detectar speakers" },
  { key: "chapters",        label: "Detectar capítulos" },
  { key: "silences",        label: "Silêncios + muletas" },
  { key: "apply_edits",     label: "Aplicar cortes + LUT" },
  { key: "level_speakers",  label: "Nivelar speakers" },
  { key: "multicam_sync",   label: "Sincronizar câmeras" },
  { key: "multicam_pick",   label: "Escolher câmera por turno" },
  { key: "multicam_render", label: "Renderizar multicam" },
  { key: "fcpxml_export",   label: "Gerar FCPXML" },
  { key: "social_copy",     label: "Copy social" },
];

let _pod1clickJobId = null;

function bindPodcast1Click() {
  const btn = document.getElementById("podcast-1click-btn");
  if (!btn) return;
  btn.addEventListener("click", startPodcast1Click);
  const cancelBtn = document.getElementById("hp-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", cancelPodcast1Click);
  // Render the step list shell once
  const stepsEl = document.getElementById("hp-steps");
  if (stepsEl) {
    stepsEl.innerHTML = POD1CLICK_STEPS.map(s =>
      `<li class="hp-row" data-step="${s.key}">
        <span class="hp-mark"><span data-icon="chevron-right" data-icon-size="14"></span></span>
        <span class="hp-name">${escapeHtml(s.label)}</span>
        <span class="hp-msg muted"></span>
      </li>`
    ).join("");
    if (window.HFIcons) HFIcons.render(stepsEl);
  }
}

async function startPodcast1Click() {
  if (!state.current) return;
  const btn = document.getElementById("podcast-1click-btn");
  const progress = document.getElementById("podcast-1click-progress");
  const result = document.getElementById("podcast-1click-result");
  if (!confirm("Vai rodar a pipeline inteira de edição (~5–15 min). Quer começar?")) return;
  btn.disabled = true;
  progress.classList.remove("hidden");
  result.classList.add("hidden");
  result.innerHTML = "";
  // Reset step rows
  for (const li of progress.querySelectorAll(".hp-row")) {
    li.classList.remove("done", "warn", "running");
    const msg = li.querySelector(".hp-msg"); if (msg) msg.textContent = "";
    const mark = li.querySelector(".hp-mark");
    if (mark) {
      mark.innerHTML = `<span data-icon="chevron-right" data-icon-size="14"></span>`;
      if (window.HFIcons) HFIcons.render(mark);
    }
  }
  document.getElementById("hp-step").textContent = "Iniciando…";
  document.getElementById("hp-pct").textContent = "0%";
  document.getElementById("hp-fill").style.width = "0%";
  const lang = document.getElementById("podcast-1click-lang")?.value || "";
  try {
    const res = await api(`/api/projects/${state.current.id}/podcast-multicam-pipeline`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(lang ? { language: lang } : {}),
    });
    _pod1clickJobId = res.job_id;
  } catch (e) {
    document.getElementById("hp-step").textContent = `✗ ${e.message}`;
    btn.disabled = false;
  }
}

// Hook into the existing SSE stream — extend the message handler to
// recognize our job events.
const _origAttachES = attachEventStream;
attachEventStream = function(pid) {
  _origAttachES(pid);
  // Also listen for job events directly on state.sse (already attached)
  if (state.sse) {
    state.sse.addEventListener("message", (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch { return; }
      if (data.type === "job" && data.job_id === _pod1clickJobId) {
        handlePodcast1ClickEvent(data);
      }
    });
  }
};

function handlePodcast1ClickEvent(data) {
  const pct = Math.round((data.progress || 0) * 100);
  document.getElementById("hp-fill").style.width = `${pct}%`;
  document.getElementById("hp-pct").textContent = `${pct}%`;
  if (data.message) document.getElementById("hp-step").textContent = data.message;

  if (data.status === "done") {
    document.getElementById("hp-step").textContent = "✓ Tudo pronto!";
    document.getElementById("hp-fill").style.width = "100%";
    renderPodcast1ClickResult(data.result || {});
    document.getElementById("podcast-1click-btn").disabled = false;
  } else if (data.status === "error" || data.status === "cancelled") {
    document.getElementById("hp-step").textContent = `✗ ${data.message || "falhou"}`;
    const btn = document.getElementById("podcast-1click-btn");
    btn.disabled = false;
    // Offer one-click retry on failure (cancel keeps state as-is so a
    // retry is also safe — the pipeline is idempotent.).
    const result = document.getElementById("podcast-1click-result");
    if (result) {
      result.classList.remove("hidden");
      const summary = data.status === "cancelled" ? "Cancelado." : `Falhou: ${data.message || "erro"}`;
      result.innerHTML = `
        <div class="hr-head"><span data-icon="alert-triangle" data-icon-size="16"></span> <strong>${escapeHtml(summary)}</strong></div>
        <div style="font-size:12px;color:var(--text-dim);margin:4px 0 8px">A pipeline é idempotente — clica em <em>Tentar de novo</em> e ela pula os passos já feitos.</div>
        <button id="hp-retry" class="btn btn-primary btn-sm"><span data-icon="rotate-cw" data-icon-size="14"></span> Tentar de novo</button>`;
      if (window.HFIcons) HFIcons.render(result);
      const retry = document.getElementById("hp-retry");
      if (retry) retry.addEventListener("click", startPodcast1Click);
    }
  }

  // Step list: which step is "running" based on progress fraction.
  // Use cumulative weights to figure out which step is active.
  const cumPct = pct / 100;
  let active = -1;
  let acc = 0;
  // Weights mirroring the server. Out of sync isn't catastrophic since the
  // server already sends a label.
  const weights = [0.18,0.06,0.03,0.04,0.10,0.06,0.05,0.05,0.35,0.02,0.06];
  for (let i = 0; i < weights.length; i++) {
    const next = acc + weights[i];
    if (cumPct >= acc && cumPct < next) { active = i; break; }
    acc = next;
  }
  if (active < 0 && cumPct >= 0.999) active = weights.length;
  // Apply states
  const rows = document.querySelectorAll("#hp-steps .hp-row");
  rows.forEach((row, i) => {
    row.classList.remove("running", "done");
    let icon = "chevron-right";
    if (i < active) { row.classList.add("done"); icon = "check"; }
    else if (i === active) { row.classList.add("running"); icon = "rotate-cw"; }
    const mark = row.querySelector(".hp-mark");
    if (mark) {
      mark.innerHTML = `<span data-icon="${icon}" data-icon-size="14"></span>`;
      if (window.HFIcons) HFIcons.render(mark);
    }
  });
}

async function cancelPodcast1Click() {
  if (!_pod1clickJobId || !state.current) return;
  if (!confirm("Cancelar a edição automática? Os passos já completados ficam salvos.")) return;
  try {
    await api(`/api/projects/${state.current.id}/jobs/${_pod1clickJobId}/cancel`, { method: "POST" });
  } catch (e) {
    log(`✗ cancel: ${e.message}`, "err");
  }
}

function renderPodcast1ClickResult(result) {
  const root = document.getElementById("podcast-1click-result");
  if (!root) return;
  const outputs = result.outputs || {};
  const warnings = result.warnings || [];
  const cards = [];
  function card(title, info, urlOpts) {
    if (!info) return "";
    const bytes = info.bytes ? fmtBytes(info.bytes) : "";
    return `<a class="hr-card" href="${info.url}" download>
      <div class="hr-icon"><span data-icon="download" data-icon-size="18"></span></div>
      <div class="hr-meta">
        <div class="hr-title">${escapeHtml(title)}</div>
        <div class="hr-sub muted">${escapeHtml(info.name || "")} · ${escapeHtml(bytes)}</div>
      </div>
    </a>`;
  }
  cards.push(card("Vídeo multicam editado",  outputs.multicam));
  cards.push(card("Vídeo single-cam (graded)", outputs.graded));
  cards.push(card("Áudio com volumes nivelados", outputs.levelled));
  cards.push(card("FCPXML pro Final Cut Pro",   outputs.fcpxml));
  const fcpxmlPath = outputs.fcpxml && outputs.fcpxml.url
    ? `${pdirAbs(state.media)}/exports/${outputs.fcpxml.name}`
    : null;
  root.innerHTML = `
    <div class="hr-head">
      <span data-icon="check" data-icon-size="16"></span>
      <strong>Tudo pronto.</strong>
      ${warnings.length ? `<span class="muted">${warnings.length} aviso(s) — veja o log.</span>` : ""}
    </div>
    <div class="hr-grid">${cards.join("")}</div>
    <div class="hr-cta">
      ${outputs.fcpxml ? `<button id="hr-open-fcp" class="btn btn-primary btn-sm" type="button"><span data-icon="film" data-icon-size="14"></span> Abrir no Final Cut Pro</button>` : ""}
      ${outputs.multicam ? `<button id="hr-reveal-mc" class="btn btn-ghost btn-sm" type="button"><span data-icon="arrow-right" data-icon-size="14"></span> Mostrar multicam no Finder</button>` : ""}
    </div>
    ${warnings.length ? `<details class="hr-warnings"><summary>${warnings.length} avisos</summary><ul>${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul></details>` : ""}
  `;
  root.classList.remove("hidden");
  if (window.HFIcons) HFIcons.render(root);
  const openBtn = document.getElementById("hr-open-fcp");
  if (openBtn && outputs.fcpxml) {
    openBtn.onclick = async () => {
      if (!state.current) return;
      try {
        await api(`/api/projects/${state.current.id}/reveal`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            path: `${pdirAbs(state.media)}/exports/${outputs.fcpxml.name}`,
            mode: "open",
          }),
        });
        toast?.("Abrindo no Final Cut Pro…", "ok");
      } catch (e) { log(`✗ open: ${e.message}`, "err"); }
    };
  }
  const revealBtn = document.getElementById("hr-reveal-mc");
  if (revealBtn && outputs.multicam) {
    revealBtn.onclick = async () => {
      if (!state.current) return;
      try {
        await api(`/api/projects/${state.current.id}/reveal`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            path: `${pdirAbs(state.media)}/exports/${outputs.multicam.name}`,
            mode: "reveal",
          }),
        });
      } catch (e) { log(`✗ reveal: ${e.message}`, "err"); }
    };
  }
}

// Returns the absolute project dir cached on state.media (populated by
// refreshMediaList), so we can ask the server to open files in /exports.
function pdirAbs(media) {
  return media && media.project_dir ? media.project_dir : "";
}

async function restoreActiveProject() {
  // Priority: URL hash (so shared links work) → localStorage (so a plain
  // refresh keeps context). Falls back to empty state on failure.
  let want = null;
  const m = location.hash.match(/^#p\/([\w-]+)/);
  if (m) want = m[1];
  if (!want) {
    try { want = localStorage.getItem("hfvp.current_project"); } catch {}
  }
  if (!want) return;
  try {
    await loadProject(want);
  } catch (e) {
    // Stale id (project deleted, etc) — clear and stay on home.
    try { localStorage.removeItem("hfvp.current_project"); } catch {}
    if (location.hash.startsWith("#p/")) history.replaceState(null, "", location.pathname);
  }
}

// Back/forward navigation — sync project switch with the URL.
window.addEventListener("hashchange", () => {
  const m = location.hash.match(/^#p\/([\w-]+)/);
  if (m && m[1] !== state.current?.id) {
    loadProject(m[1]).catch(() => {});
  } else if (!m && state.current) {
    clearActiveProject();
  }
});

// ---- Reels (animation overlays) -------------------------------------------

const REELS_STATE = { animations: [], types: {}, duration: 0, editing_index: -1 };

async function loadReels() {
  if (!state.current) return;
  try {
    const data = await api(`/api/projects/${state.current.id}/reels/animations`);
    REELS_STATE.animations = data.animations || [];
    REELS_STATE.types = data.types || {};
    REELS_STATE.duration = data.source_duration || 0;
    populateReelsTypeSelect();
    renderReelsList();
    await Promise.all([loadReelTemplates(), loadSfxLibrary()]);
  } catch (e) {
    log(`✗ reels: ${e.message}`, "err");
  }
}

async function loadSfxLibrary() {
  try {
    const data = await api(`/api/sfx`);
    REELS_STATE.sfx_presets = data.presets || [];
    REELS_STATE.sfx_defaults_by_type = data.defaults_by_type || {};
    populateReelsSfxSelect();
  } catch (e) {
    REELS_STATE.sfx_presets = [];
  }
}

function populateReelsSfxSelect() {
  const sel = $("#ra-sfx");
  if (!sel) return;
  const opts = [`<option value="none">— sem som —</option>`,
                `<option value="_default">padrão pro tipo</option>`];
  for (const p of REELS_STATE.sfx_presets || []) {
    opts.push(`<option value="${escapeHtml(p.name)}">${escapeHtml(p.label || p.name)}</option>`);
  }
  sel.innerHTML = opts.join("");
}

async function loadReelTemplates() {
  const sel = $("#reels-template-select");
  if (!sel) return;
  try {
    const data = await api(`/api/reel-templates`);
    REELS_STATE.templates = data.templates || [];
    sel.innerHTML = REELS_STATE.templates.map(t =>
      `<option value="${escapeHtml(t._id)}" ${t.builtin ? 'data-builtin="1"' : ""}>${escapeHtml(t.name)}${t.builtin ? " · embutido" : ""}</option>`
    ).join("");
    if (REELS_STATE.templates.length === 0) {
      sel.innerHTML = `<option value="">— sem templates —</option>`;
    }
    updateReelTemplateHint();
  } catch (e) {
    sel.innerHTML = `<option value="">erro: ${escapeHtml(e.message)}</option>`;
  }
}

function updateReelTemplateHint() {
  const sel = $("#reels-template-select");
  const hint = $("#reels-template-hint");
  const delBtn = $("#reels-template-delete-btn");
  if (!sel || !hint) return;
  const tpl = REELS_STATE.templates?.find(t => t._id === sel.value);
  if (!tpl) {
    hint.textContent = "";
    if (delBtn) delBtn.disabled = true;
    return;
  }
  hint.textContent = tpl.description || `${(tpl.animations || []).length} animações`;
  if (delBtn) delBtn.disabled = !!tpl.builtin;
}

async function applyReelTemplate() {
  if (!state.current) return;
  const sel = $("#reels-template-select");
  const tid = sel.value;
  if (!tid) return;
  const append = $("#reels-template-append").checked;
  try {
    const res = await api(`/api/projects/${state.current.id}/reels/apply-template/${tid}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ replace: !append, append_to_existing: append }),
    });
    REELS_STATE.animations = res.animations;
    renderReelsList();
    log(`<span data-icon=&quot;check&quot;></span> template aplicado: ${res.count} animações`, "ok");
    const iframe = $("#reels-preview-iframe");
    if (iframe && !$("#reels-preview-pane").classList.contains("hidden")) {
      reloadReelsPreview();
    }
  } catch (e) {
    log(`✗ aplicar template: ${e.message}`, "err");
  }
}

async function saveReelsAsTemplate() {
  if (!state.current) return;
  if (REELS_STATE.animations.length === 0) {
    log("✗ sem animações pra salvar", "err");
    return;
  }
  const name = prompt("Nome do template:");
  if (!name) return;
  const description = prompt("Descrição (opcional):") || "";
  try {
    await api(`/api/projects/${state.current.id}/reels/save-as-template`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, description }),
    });
    log(`<span data-icon=&quot;check&quot;></span> template '${name}' salvo`, "ok");
    await loadReelTemplates();
  } catch (e) {
    log(`✗ salvar template: ${e.message}`, "err");
  }
}

async function deleteReelTemplate() {
  const sel = $("#reels-template-select");
  const tid = sel.value;
  if (!tid) return;
  const tpl = REELS_STATE.templates?.find(t => t._id === tid);
  if (!tpl || tpl.builtin) return;
  if (!confirm(`Apagar template '${tpl.name}'?`)) return;
  try {
    await api(`/api/reel-templates/${tid}`, { method: "DELETE" });
    log(`<span data-icon=&quot;check&quot;></span> template '${tpl.name}' apagado`, "ok");
    await loadReelTemplates();
  } catch (e) {
    log(`✗ delete template: ${e.message}`, "err");
  }
}

function populateReelsTypeSelect() {
  const sel = $("#ra-type");
  if (!sel) return;
  sel.innerHTML = Object.entries(REELS_STATE.types).map(([k, v]) =>
    `<option value="${k}">${escapeHtml(v.label || k)}</option>`
  ).join("");
  sel.onchange = () => populateReelsVariantSelect();
  populateReelsVariantSelect();
}

function populateReelsVariantSelect(selected) {
  const sel = $("#ra-variant");
  if (!sel) return;
  const t = $("#ra-type").value;
  const spec = REELS_STATE.types[t] || {};
  const variants = spec.variants || ["default"];
  sel.innerHTML = variants.map(v =>
    `<option value="${v}">${escapeHtml(v)}</option>`
  ).join("");
  if (selected && variants.includes(selected)) sel.value = selected;
  // Hide variant select if there's only one (cosmetic).
  sel.closest("label").style.display = variants.length > 1 ? "" : "none";
  // Logo + TTS rows only relevant for hook/cta.
  const hookish = (t === "hook_card" || t === "cta_end");
  const logoRow = $("#ra-logo-row");
  const ttsRow = $("#ra-tts-row");
  if (logoRow) logoRow.style.display = hookish ? "" : "none";
  if (ttsRow) ttsRow.style.display = hookish ? "" : "none";
}

function renderReelsList() {
  const root = $("#reels-list");
  if (!root) return;
  const anims = REELS_STATE.animations;
  if (anims.length === 0) {
    root.innerHTML = `<div class="reels-empty">Sem animações ainda. Clique em <strong><span data-icon=&quot;wand-2&quot;></span> Sugerir animações</strong> ou <strong><span data-icon=&quot;plus&quot;></span> Adicionar manual</strong>.</div>`;
    return;
  }
  root.innerHTML = `<table class="reels-table">
    <thead><tr><th>Início</th><th>Dur</th><th>Tipo</th><th>Texto</th><th>Origem</th><th></th></tr></thead>
    <tbody>${anims.map((a, i) => `<tr data-idx="${i}">
      <td>${a.start.toFixed(2)}s</td>
      <td>${a.duration.toFixed(1)}s</td>
      <td class="ra-type-cell">${escapeHtml((REELS_STATE.types[a.type]?.label) || a.type)}${a.variant && a.variant !== "default" ? `<br><span class="muted ra-variant-tag">${escapeHtml(a.variant)}</span>` : ""}</td>
      <td><strong>${escapeHtml(a.text || "")}</strong>${a.sub ? `<br><span class="muted">${escapeHtml(a.sub)}</span>` : ""}${(a.type === "hook_card" || a.type === "cta_end") && a.show_logo ? ` <span class="ra-logo-tag" title="usa logo da marca">🏷</span>` : ""}${a.sfx && a.sfx !== "none" ? ` <span class="ra-sfx-tag" title="SFX: ${escapeHtml(a.sfx === "_default" ? "padrão" : a.sfx)}">🔊</span>` : ""}${a.tts ? ` <span class="ra-tts-tag" title="TTS: ${escapeHtml(a.tts)}">🗣</span>` : ""}</td>
      <td class="muted">${escapeHtml(a.source || a.reason || "")}</td>
      <td class="ra-actions">
        <button class="btn-ghost reels-edit" data-idx="${i}" title="Editar">✎</button>
        <button class="btn-ghost reels-delete" data-idx="${i}" title="Remover">✕</button>
      </td>
    </tr>`).join("")}</tbody>
  </table>`;
  for (const btn of root.querySelectorAll(".reels-edit")) {
    btn.addEventListener("click", () => openReelsForm(Number(btn.dataset.idx)));
  }
  for (const btn of root.querySelectorAll(".reels-delete")) {
    btn.addEventListener("click", () => deleteReelAnim(Number(btn.dataset.idx)));
  }
}

function openReelsForm(idx) {
  REELS_STATE.editing_index = idx;
  const form = $("#reels-add-form");
  form.classList.remove("hidden");
  const a = (idx >= 0) ? REELS_STATE.animations[idx] : { start: 0, duration: 1.5, type: "text_callout", text: "", sub: "", emoji: "", variant: "", show_logo: true, sfx: "_default", sfx_volume: 0.7, tts: "", tts_voice: "alloy" };
  $("#ra-start").value = a.start ?? 0;
  $("#ra-duration").value = a.duration ?? 1.5;
  $("#ra-type").value = a.type || "text_callout";
  populateReelsVariantSelect(a.variant);
  $("#ra-text").value = a.text || "";
  $("#ra-sub").value = a.sub || "";
  $("#ra-emoji").value = a.emoji || "";
  const sl = $("#ra-show-logo");
  if (sl) sl.checked = a.show_logo !== false;
  const sfxSel = $("#ra-sfx");
  if (sfxSel) sfxSel.value = a.sfx == null ? "none" : (a.sfx === "_default" ? "_default" : a.sfx);
  $("#ra-sfx-volume").value = a.sfx_volume ?? 0.7;
  $("#ra-tts").value = a.tts || "";
  $("#ra-tts-voice").value = a.tts_voice || "alloy";
}

function closeReelsForm() {
  REELS_STATE.editing_index = -1;
  $("#reels-add-form").classList.add("hidden");
}

async function saveReelAnim() {
  const idx = REELS_STATE.editing_index;
  const sfx = $("#ra-sfx")?.value || "_default";
  const anim = {
    start: parseFloat($("#ra-start").value) || 0,
    duration: parseFloat($("#ra-duration").value) || 1.5,
    type: $("#ra-type").value,
    variant: $("#ra-variant").value || undefined,
    text: $("#ra-text").value.trim(),
    sub: $("#ra-sub").value.trim() || null,
    emoji: $("#ra-emoji").value.trim() || null,
    show_logo: $("#ra-show-logo")?.checked ?? true,
    sfx: sfx === "none" ? null : sfx,
    sfx_volume: parseFloat($("#ra-sfx-volume")?.value) || 0.7,
    tts: $("#ra-tts")?.value.trim() || null,
    tts_voice: $("#ra-tts-voice")?.value || "alloy",
    source: "manual",
  };
  if (idx >= 0) REELS_STATE.animations[idx] = anim;
  else REELS_STATE.animations.push(anim);
  await persistReels();
  closeReelsForm();
  // If the preview is open, reload it so changes show up immediately.
  const iframe = $("#reels-preview-iframe");
  if (iframe && !$("#reels-preview-pane").classList.contains("hidden")) {
    reloadReelsPreview();
  }
}

async function deleteReelAnim(idx) {
  REELS_STATE.animations.splice(idx, 1);
  await persistReels();
}

async function persistReels() {
  if (!state.current) return;
  try {
    const res = await api(`/api/projects/${state.current.id}/reels/animations`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ animations: REELS_STATE.animations }),
    });
    REELS_STATE.animations = res.animations;
    renderReelsList();
    log(`<span data-icon=&quot;check&quot;></span> reels: ${res.count} animações salvas`, "ok");
  } catch (e) {
    log(`✗ reels save: ${e.message}`, "err");
  }
}

async function suggestReelAnims() {
  if (!state.current) return;
  const useLlm = $("#reels-use-llm").checked;
  const replace = $("#reels-replace").checked;
  const status = $("#reels-status");
  status.textContent = useLlm ? "pedindo pro modelo…" : "rodando heurísticas…";
  status.className = "status running";
  try {
    const res = await api(`/api/projects/${state.current.id}/reels/suggest`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ use_llm: useLlm, max_animations: 10, replace }),
    });
    REELS_STATE.animations = res.animations;
    renderReelsList();
    setBtnHTML(status, `<span data-icon=&quot;check&quot;></span> ${res.count} sugestões`);
    status.className = "status done";
    log(`<span data-icon=&quot;check&quot;></span> reels suggest: ${res.count} animações`, "ok");
  } catch (e) {
    status.textContent = `✗ ${e.message}`;
    status.className = "status error";
    log(`✗ reels suggest: ${e.message}`, "err");
  }
}

// REELS_STATE.preview_mode: "html" (live iframe) or "mp4" (quick MP4)
function openReelsPreview() {
  if (!state.current) return;
  REELS_STATE.preview_mode = "html";
  $("#reels-preview-pane").classList.remove("hidden");
  $("#reels-preview-iframe").classList.remove("hidden");
  $("#reels-preview-video").classList.add("hidden");
  $("#reels-preview-title").textContent = "Preview HTML";
  $("#reels-preview-stats").textContent = "instantâneo · GSAP timeline";
  reloadReelsPreview();
}

function reloadReelsPreview() {
  if (!state.current) return;
  if (REELS_STATE.preview_mode === "mp4") return quickPreviewReel();
  const iframe = $("#reels-preview-iframe");
  if (!iframe) return;
  const p = state.current;
  let pick = "source";
  if (p.has_roughcut) pick = "roughcut";
  else if (p.has_render || p.has_cuts) pick = "graded";
  iframe.src = `/api/projects/${state.current.id}/reels/preview?source=${pick}&t=${Date.now()}`;
}

async function quickPreviewReel() {
  if (!state.current) return;
  REELS_STATE.preview_mode = "mp4";
  const pane = $("#reels-preview-pane");
  pane.classList.remove("hidden");
  $("#reels-preview-iframe").classList.add("hidden");
  const video = $("#reels-preview-video");
  video.classList.remove("hidden");
  $("#reels-preview-title").textContent = "Preview MP4";
  $("#reels-preview-stats").textContent = "renderizando…";
  const p = state.current;
  let pick = "source";
  if (p.has_roughcut) pick = "roughcut";
  else if (p.has_render || p.has_cuts) pick = "graded";
  try {
    const res = await api(`/api/projects/${state.current.id}/reels/quick-preview`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source: pick, width: 540 }),
    });
    video.src = `${res.url}?t=${Date.now()}`;
    video.load();
    video.play().catch(() => {});
    setBtnHTML($("#reels-preview-stats"), `<span data-icon=&quot;check&quot;></span> ${(res.took_ms / 1000).toFixed(1)}s · ${res.rasterized} novas, ${res.reused} reusadas · ${res.width}×${res.height}`);
    log(`<span data-icon=&quot;check&quot;></span> quick preview: ${res.took_ms}ms`, "ok");
  } catch (e) {
    $("#reels-preview-stats").textContent = `✗ ${e.message}`;
    log(`✗ quick preview: ${e.message}`, "err");
    // Fall back to HTML preview so the pane isn't dead
    REELS_STATE.preview_mode = "html";
    $("#reels-preview-iframe").classList.remove("hidden");
    video.classList.add("hidden");
    reloadReelsPreview();
  }
}

function closeReelsPreview() {
  $("#reels-preview-pane").classList.add("hidden");
  $("#reels-preview-iframe").src = "about:blank";
  const video = $("#reels-preview-video");
  video.pause();
  video.src = "";
}

function bindReels() {
  const b1 = $("#reels-suggest-btn");
  if (b1) b1.onclick = suggestReelAnims;
  const b2 = $("#reels-add-btn");
  if (b2) b2.onclick = () => openReelsForm(-1);
  const b3 = $("#reels-save-btn");
  if (b3) b3.onclick = saveReelAnim;
  const b4 = $("#reels-cancel-btn");
  if (b4) b4.onclick = closeReelsForm;
  const b5 = $("#reels-preview-btn");
  if (b5) b5.onclick = openReelsPreview;
  const b5b = $("#reels-quick-preview-btn");
  if (b5b) b5b.onclick = quickPreviewReel;
  const b6 = $("#reels-preview-reload");
  if (b6) b6.onclick = reloadReelsPreview;
  const b7 = $("#reels-preview-close");
  if (b7) b7.onclick = closeReelsPreview;
  const t1 = $("#reels-template-select");
  if (t1) t1.addEventListener("change", updateReelTemplateHint);
  const t2 = $("#reels-template-apply-btn");
  if (t2) t2.onclick = applyReelTemplate;
  const t3 = $("#reels-template-save-btn");
  if (t3) t3.onclick = saveReelsAsTemplate;
  const t4 = $("#reels-template-delete-btn");
  if (t4) t4.onclick = deleteReelTemplate;

  const s1 = $("#ra-sfx-preview-btn");
  if (s1) s1.onclick = () => {
    const name = $("#ra-sfx").value;
    if (!name || name === "none") return;
    const actual = name === "_default"
      ? (REELS_STATE.sfx_defaults_by_type?.[$("#ra-type").value] || "ding")
      : name;
    const a = new Audio(`/api/sfx/${encodeURIComponent(actual)}.mp3?t=${Date.now()}`);
    a.volume = Math.min(1, parseFloat($("#ra-sfx-volume").value) || 0.7);
    a.play().catch(() => {});
  };
  const s2 = $("#ra-tts-preview-btn");
  if (s2) s2.onclick = async () => {
    const text = $("#ra-tts").value.trim();
    if (!text || !state.current) return;
    const btn = s2;
    btn.disabled = true;
    btn.textContent = "⏳";
    try {
      const res = await api(`/api/projects/${state.current.id}/reels/tts-preview`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, voice: $("#ra-tts-voice").value }),
      });
      const a = new Audio(`${res.url}?t=${Date.now()}`);
      a.play().catch(() => {});
    } catch (e) {
      log(`✗ tts: ${e.message}`, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "🔊";
    }
  };
}

async function refreshReelsOnLoad() {
  if (state.current && document.querySelector('.card[data-cat="reels"]')) {
    await loadReels();
  }
}

// ---- INIT (must stay at the very bottom, after every const/let/function
// declaration above. Calling bind*() before those declarations triggers
// TDZ errors that abort module evaluation — see commit history for the
// 1-click / Reels regression). ----
bind();
refreshList();
restoreActiveProject();
bindPodcast1Click();
