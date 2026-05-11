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

function toast(msg, kind = "") {
  let el = document.querySelector(".live-toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "live-toast";
    document.body.appendChild(el);
  }
  el.className = `live-toast ${kind}`;
  el.textContent = msg;
  requestAnimationFrame(() => el.classList.add("show"));
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 2400);
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

function log(msg, kind = "") {
  const el = $("#pipeline-log");
  if (!el) return;
  const ts = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  line.className = kind;
  line.textContent = `[${ts}] ${msg}`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
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
  refreshStats();
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
  attachEventStream(id);
  renderStaleWarnings(p);
  renderCutsTimeline(p);
  renderProgressBar(p);
  $("#cancel-render-btn").style.display = p.render_active ? "inline-block" : "none";
  $("#empty").classList.add("hidden");
  $("#project-view").classList.remove("hidden");
  $("#project-name").textContent = p.name;

  const preview = $("#preview");
  if (p.source_filename) {
    preview.src = `/api/projects/${p.id}/files/source.mp4`;
    $("#upload-status").textContent = `${p.source_filename} · ${p.source_duration?.toFixed?.(2) || 0}s`;
    $("#upload-status").className = "status ok";
  } else {
    preview.removeAttribute("src");
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

  // result
  const result = $("#result");
  if (p.has_render && p.last_export) {
    const url = `/api/projects/${p.id}/exports/${p.last_export}`;
    result.src = url;
    $("#download-link").href = url;
    $("#download-link").style.opacity = 1;
    $("#download-link").style.pointerEvents = "auto";
  } else {
    result.removeAttribute("src");
    $("#download-link").href = "#";
    $("#download-link").style.opacity = 0.5;
    $("#download-link").style.pointerEvents = "none";
  }

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
  log("▶ bite thumbs");
  try {
    const r = await api(`/api/projects/${state.current.id}/bite-thumbnails`, { method: "POST" });
    log(`✓ ${r.thumbs.length} thumbs`, "ok");
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
      ? ` <span class="tag warn">⚠ ${qc.quality}</span>` : (qc.quality === "ok" ? ' <span class="tag ok">✓ ok</span>' : '');
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
        <button class="btn-ghost a-tag" data-idx="${a.index}">${a.tags ? "🔄 Re-taggear" : "🏷 Tag IA"}</button>
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
      btn.textContent = "🤖 Analisando...";
      try {
        const updated = await api(`/api/projects/${p.id}/angles/${a.index}/tag`, { method: "POST" });
        log(`✓ Ângulo "${updated.name}" → ${updated.tags.summary}`, "ok");
        await loadProject(p.id);
      } catch (err) {
        log(`✗ tag: ${err.message}`, "err");
        btn.disabled = false;
        btn.textContent = "🏷 Tag IA";
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
      if (data.status === "done") toast(`✓ ${data.stage}: ${data.message || ""}`, "ok");
      if (data.status === "error") toast(`✗ ${data.stage}: ${data.message || ""}`, "error");
      if (data.status === "running") {
        if (data.stage === "render") {
          $("#cancel-render-btn").style.display = "inline-block";
        }
        if (typeof data.progress !== "number") toast(`▶ ${data.stage}…`);
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
  const name = prompt("Nome do projeto:", "Meu vídeo");
  if (!name) return;
  const p = await api("/api/projects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  await refreshList();
  await loadProject(p.id);
}

async function uploadFile(file) {
  if (!state.current) return;
  const fd = new FormData();
  fd.append("file", file);
  $("#upload-status").textContent = `Enviando ${file.name}...`;
  $("#upload-status").className = "status warn";
  try {
    await api(`/api/projects/${state.current.id}/upload`, { method: "POST", body: fd });
    await loadProject(state.current.id);
    log(`Upload concluído: ${file.name}`, "ok");
  } catch (e) {
    $("#upload-status").textContent = `Falha: ${e.message}`;
    $("#upload-status").className = "status error";
    log(`Upload erro: ${e.message}`, "err");
  }
}

async function uploadLut(file) {
  if (!state.current) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    await api(`/api/projects/${state.current.id}/lut`, { method: "POST", body: fd });
    log(`LUT enviada: ${file.name}`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`LUT erro: ${e.message}`, "err");
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
  log(`▶ ${name}`);
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
    log(`✓ ${name}: ${JSON.stringify(res).slice(0, 200)}`, "ok");
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
  btn.textContent = "🎯 Analisando...";
  log("▶ soundbites");
  try {
    const a = await api(`/api/projects/${state.current.id}/soundbites`, { method: "POST" });
    log(`✓ ${a.soundbites.length} soundbites · ${a.topics.length} tópicos`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ soundbites: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "🎯 Extrair soundbites";
  }
}

async function buildStory() {
  if (!state.current) return;
  const btn = $("#story-btn");
  btn.disabled = true;
  btn.textContent = "📜 Pensando...";
  log("▶ story");
  try {
    const s = await api(`/api/projects/${state.current.id}/story`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ structure: $("#story-structure").value }),
    });
    log(`✓ Roteiro: ${s.title} (${s.chapters.length} capítulos)`, "ok");
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ story: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "📜 Propor roteiro";
  }
}

async function buildRoughCut() {
  if (!state.current) return;
  const btn = $("#roughcut-btn");
  const status = $("#roughcut-status");
  btn.disabled = true;
  status.textContent = "encoding...";
  status.className = "status warn";
  log("▶ roughcut");

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
    log(`✓ rough cut · ${r.duration.toFixed(1)}s`, "ok");
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
  log("▶ Premiere XML");
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
    log(`✓ Premiere XML · ${res.bytes}b`, "ok");
    const link = $("#premiere-link");
    link.href = res.url;
    link.style.display = "inline-block";
    link.textContent = `⬇ Baixar ${res.export}`;
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
    log(`✓ B-roll placement · ${r.placements.length} inserts`, "ok");
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
  btn.textContent = "✨ Detectando subject...";
  log("▶ smart reframe");
  try {
    const r = await api(`/api/projects/${state.current.id}/smart-reframe`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        aspect: $("#export-aspect").value,
        use_roughcut: state.current.has_roughcut,
      }),
    });
    log(`✓ smart-crop anchor=${r.anchor_x.toFixed(2)} · ${r.url}`, "ok");
    $("#download-link").href = r.url;
    $("#result").src = r.url;
  } catch (e) {
    log(`✗ smart-reframe: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "✨ Smart crop (IA)";
  }
}

async function exportCaptions(fmt) {
  if (!state.current) return;
  log(`▶ ${fmt}`);
  try {
    const useRoughcut = $("#caps-roughcut").checked;
    const speakers = $("#caps-speakers")?.checked || false;
    const style = $("#ass-style")?.value || "minimal";
    const url = `/api/projects/${state.current.id}/export/captions?fmt=${fmt}&use_roughcut=${useRoughcut}&style=${style}&speaker_labels=${speakers}`;
    const res = await api(url, { method: "POST" });
    log(`✓ ${fmt} · ${res.cues} cues`, "ok");
    const link = $("#caps-link");
    link.href = res.url;
    link.style.display = "inline-block";
    link.textContent = `⬇ ${res.export}`;
  } catch (e) {
    log(`✗ ${fmt}: ${e.message}`, "err");
  }
}

async function burnCaptions() {
  if (!state.current) return;
  log("▶ burn captions");
  try {
    const r = await api(`/api/projects/${state.current.id}/export/burn-captions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source: $("#burn-source").value,
        style: $("#burn-style").value,
      }),
    });
    log(`✓ burned · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#burn-link");
    a.href = r.url;
    a.style.display = "inline-block";
    a.textContent = `⬇ ${r.export}`;
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
          log(`✓ snapshot restaurado`, "ok");
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
    log(`✓ snapshot: ${label}`, "ok");
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
    log(`✓ Preset salvo: ${name}`, "ok");
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
    log(`✓ cuts manuais · kept ${plan.kept_duration.toFixed(2)}s`, "ok");
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
  log(`▶ highlights ${target}s`);
  try {
    const r = await api(`/api/projects/${state.current.id}/highlights`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ target_seconds: target, apply_lut: true }),
    });
    log(`✓ highlights · ${r.duration.toFixed(1)}s · ${r.segments} bites`, "ok");
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
    log("✓ social copy", "ok");
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
    log(`✓ template aplicado: ${r.label}`, "ok");
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
      transcribe: { label: "▶ Transcrever agora", fn: () => runStage("transcribe") },
      cuts:       { label: "▶ Detectar silêncios + muletas", fn: () => runStage("silence") },
      soundbites: { label: "🎯 Extrair soundbites", fn: () => extractSoundbites() },
      story:      { label: "📜 Propor roteiro",   fn: () => buildStory() },
      render:     { label: "🎬 Renderizar",       fn: () => runStage("render") },
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
      div.textContent = `⚠ ${msg}`;
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
  log("▶ audio export");
  try {
    const r = await api(`/api/projects/${state.current.id}/export/audio`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        format: $("#audio-format").value,
        source: $("#audio-source").value,
      }),
    });
    log(`✓ audio · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#audio-link");
    a.href = r.url;
    a.style.display = "inline-block";
    a.textContent = `⬇ ${r.export}`;
  } catch (e) {
    log(`✗ audio: ${e.message}`, "err");
  }
}

async function cancelRender() {
  if (!state.current) return;
  try {
    const r = await api(`/api/projects/${state.current.id}/render/cancel`, { method: "POST" });
    log(r.cancelled ? "✓ render cancelado" : "ℹ nenhum render rodando", r.cancelled ? "ok" : "");
    $("#cancel-render-btn").style.display = "none";
  } catch (e) {
    log(`✗ cancel: ${e.message}`, "err");
  }
}

async function exportBundle() {
  if (!state.current) return;
  log("▶ bundle");
  try {
    const r = await api(`/api/projects/${state.current.id}/export/bundle`, { method: "POST" });
    log(`✓ bundle · ${r.files} files · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#bundle-link");
    a.href = r.url;
    a.style.display = "inline-block";
    a.textContent = `⬇ ${r.export}`;
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
    log(`✓ duplicado: ${p.name}`, "ok");
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
    log(`✓ ${r.backend} · ${r.turns.length} turnos · ${speakers}`, "ok");
    toast(`${r.stats?.speaker_count || 0} speakers detectados (${r.backend})`, "ok");
  } catch (e) {
    log(`✗ speakers: ${e.message}`, "err");
  }
}

async function levelSpeakers() {
  if (!state.current) return;
  log("▶ leveling speakers");
  try {
    const r = await api(`/api/projects/${state.current.id}/speaker-levels`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ target_dbfs: -18.0, source: "graded" }),
    });
    const gains = Object.entries(r.gains).map(([sp, g]) => `${sp}:${g > 0 ? "+" : ""}${g}dB`).join("  ");
    log(`✓ gains: ${gains}`, "ok");
    toast(`Nivelado: ${gains}`, "ok");
    await refreshHistory();
  } catch (e) {
    log(`✗ speaker-levels: ${e.message}`, "err");
  }
}

async function detectChapters() {
  if (!state.current) return;
  log("▶ chapters");
  try {
    const r = await api(`/api/projects/${state.current.id}/chapters`, { method: "POST" });
    log(`✓ ${r.chapters.length} capítulos`, "ok");
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
  log("▶ podcast pipeline");
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
  log("▶ shorts batch");
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
    log("✓ YouTube description gerada", "ok");
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
    log(`▶ uploading clip ${f.name}`);
    try {
      const r = await api(`/api/projects/${state.current.id}/clips`, { method: "POST", body: fd });
      log(`✓ clip ${r.name} (${r.duration.toFixed(1)}s)`, "ok");
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
        <span class="a-name">${escapeHtml(c.name)}${c.has_transcript ? ' <span class="tag ok">✓ txt</span>' : ' <span class="tag warn">— sem txt</span>'} ${peopleBadges}</span>
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
  log("▶ transcribe all clips");
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
  log("▶ vlog narratives");
  try {
    const r = await api(`/api/projects/${state.current.id}/vlog/narratives`, { method: "POST" });
    renderNarratives(r.narratives || []);
    log(`✓ ${r.narratives?.length || 0} narrativas propostas`, "ok");
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
        <button class="btn-ghost n-broll" data-id="${n.id}">🎯 B-roll</button>
        <button class="btn-primary n-assemble" data-id="${n.id}">🎬 Montar este vlog</button>
        <a class="btn-ghost n-link" href="#" download style="display:none">⬇ vlog.mp4</a>
      </div>
    `;
    card.querySelector(".n-storyboard").onclick = async () => {
      try {
        const r = await api(`/api/projects/${state.current.id}/vlog/narratives/${n.id}/storyboard`, { method: "POST" });
        log(`✓ storyboard ${n.id}: ${r.panels.length} painéis`, "ok");
        toast(`Storyboard com ${r.panels.length} painéis`, "ok");
      } catch (e) { log(`✗ storyboard: ${e.message}`, "err"); }
    };
    card.querySelector(".n-broll").onclick = async () => {
      try {
        const r = await api(`/api/projects/${state.current.id}/vlog/place-broll?narrative_id=${n.id}`, { method: "POST" });
        log(`✓ B-roll ${n.id}: ${r.placements.length} inserts`, "ok");
        toast(`${r.placements.length} B-roll inserts planejados`, "ok");
      } catch (e) { log(`✗ B-roll: ${e.message}`, "err"); }
    };
    card.querySelector(".n-assemble").onclick = async () => {
      const aspect = card.querySelector(".n-aspect").value;
      log(`▶ assemble narrative ${n.id}`);
      try {
        const r = await api(`/api/projects/${state.current.id}/vlog/assemble`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ narrative_id: n.id, aspect, loudnorm: true }),
        });
        log(`✓ vlog · ${r.bytes} bytes`, "ok");
        const a = card.querySelector(".n-link");
        a.href = r.url; a.style.display = "inline-block"; a.textContent = `⬇ ${r.export}`;
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
  log("▶ multicam pick");
  try {
    const r = await api(`/api/projects/${state.current.id}/multicam-pick`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ intervals: "turns", min_dur: 1.4 }),
    });
    log(`✓ ${r.cuts.length} cam cuts (intervalos: ${r.intervals})`, "ok");
    toast(`Câmera escolhida pra ${r.cuts.length} segmentos`, "ok");
  } catch (e) {
    log(`✗ multicam-pick: ${e.message}`, "err");
  }
}

async function multicamRender() {
  if (!state.current) return;
  log("▶ multicam render");
  try {
    const r = await api(`/api/projects/${state.current.id}/multicam-render`, { method: "POST" });
    log(`✓ multicam ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#multicam-link");
    a.href = r.url; a.style.display = "inline-block"; a.textContent = `⬇ ${r.export}`;
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
  log("▶ vlog 1-click");
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
  log("▶ vlog music suggest");
  try {
    const s = await api(`/api/projects/${state.current.id}/vlog/music-suggest?language=pt`, { method: "POST" });
    log(`✓ ${s.description}`, "ok");
    renderMusicSuggestion(s);
  } catch (e) {
    log(`✗ vlog music: ${e.message}`, "err");
  }
}

async function detectFaceIdentities() {
  if (!state.current) return;
  log("▶ face identities");
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
  log("▶ people thumbs");
  try {
    const r = await api(`/api/projects/${state.current.id}/face-identities/thumbnails`, { method: "POST" });
    log(`✓ ${r.thumbs.length} thumbs gerados`, "ok");
    // refresh people list
    const ids = await api(`/api/projects/${state.current.id}/files/face_identities.json`);
    renderPeople(ids.clusters || [], ids.presence || {});
  } catch (e) {
    log(`✗ people thumbs: ${e.message}`, "err");
  }
}

async function buildSourceSubjectTimeline() {
  if (!state.current) return;
  log("▶ subject timeline (source)");
  try {
    const r = await api(`/api/projects/${state.current.id}/subject-timeline?target=source&step_seconds=0.5`, { method: "POST" });
    log(`✓ ${r.events?.length || 0} samples · ${r.changes?.length || 0} mudanças`, "ok");
    toast(`${r.changes?.length || 0} mudanças de sujeito detectadas`, "ok");
  } catch (e) {
    log(`✗ subject timeline: ${e.message}`, "err");
  }
}

async function multicamSync() {
  if (!state.current) return;
  log("▶ multicam sync");
  try {
    const r = await api(`/api/projects/${state.current.id}/multicam-sync`, { method: "POST" });
    const desc = r.offsets.slice(1).map(o => `${o.name}: ${o.offset > 0 ? "+" : ""}${o.offset.toFixed(2)}s (${(o.score * 100).toFixed(0)}%)`).join(", ");
    log(`✓ ${r.offsets.length - 1} angles → ${desc}`, "ok");
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
  log(`▶ uploading music ${file.name}`);
  try {
    const r = await api(`/api/projects/${state.current.id}/music/upload`, { method: "POST", body: fd });
    log(`✓ music uploaded (${(r.bytes / 1024).toFixed(0)} KB)`, "ok");
    toast(`Música pronta: ${r.filename}`, "ok");
  } catch (e) {
    log(`✗ music upload: ${e.message}`, "err");
  }
}

async function mixMusic() {
  if (!state.current) return;
  log("▶ mixing music + voice");
  try {
    const r = await api(`/api/projects/${state.current.id}/music/mix`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source: $("#music-source").value,
        music_db: parseFloat($("#music-db").value || "-8"),
      }),
    });
    log(`✓ mix · ${(r.bytes / 1024).toFixed(0)} KB`, "ok");
    const a = $("#mix-link");
    a.href = r.url;
    a.style.display = "inline-block";
    a.textContent = `⬇ ${r.export}`;
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
    log(`✓ archived: ${r.archived}`, "ok");
    toast(`Arquivado: ${r.archived}`, "ok");
    state.current = null;
    $("#project-view").classList.add("hidden");
    $("#empty").classList.remove("hidden");
    await refreshList();
  } catch (e) {
    log(`✗ archive: ${e.message}`, "err");
  }
}

async function makeHook() {
  if (!state.current) return;
  log("▶ hook");
  try {
    const r = await api(`/api/projects/${state.current.id}/hook`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ target_seconds: 4 }),
    });
    log(`✓ hook ${r.duration.toFixed(1)}s @ ${r.start.toFixed(1)}s`, "ok");
    toast(`Hook pronto · ${r.duration.toFixed(1)}s`, "ok");
    await refreshHistory();
  } catch (e) {
    log(`✗ hook: ${e.message}`, "err");
  }
}

async function peakThumb() {
  if (!state.current) return;
  log("▶ peak thumbnail");
  try {
    const r = await api(`/api/projects/${state.current.id}/peak-thumbnail`, { method: "POST" });
    const grid = $("#thumbs-grid");
    const div = document.createElement("div");
    div.className = "thumb";
    div.innerHTML = `<img src="${r.url}?t=${Date.now()}" alt="peak"/><div class="label">⭐ Peak @ ${r.at.toFixed(1)}s</div>`;
    grid.prepend(div);
    log(`✓ peak thumb @ ${r.at.toFixed(1)}s`, "ok");
  } catch (e) {
    log(`✗ peak: ${e.message}`, "err");
  }
}

async function emojifyCaptions() {
  if (!state.current) return;
  log("▶ emojify");
  try {
    const r = await api(`/api/projects/${state.current.id}/captions/emojify`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ use_llm: true }),
    });
    log(`✓ ${r.updated} linhas decoradas`, "ok");
    toast(`${r.updated} legendas com emoji`, "ok");
  } catch (e) {
    log(`✗ emojify: ${e.message}`, "err");
  }
}

async function refreshHistory() {
  if (!state.current) return;
  try {
    const list = await api(`/api/projects/${state.current.id}/history`);
    const root = $("#history-list");
    root.innerHTML = "";
    for (const e of list.slice().reverse().slice(0, 30)) {
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
  } catch {}
}

async function chapterThumbs() {
  if (!state.current) return;
  log("▶ chapter thumbs");
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
    log(`✓ thumbs · ${r.thumbs.length}`, "ok");
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
    log(`✓ Preset aplicado`, "ok");
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

async function deleteProject() {
  if (!state.current) return;
  if (!(await brandedConfirm(
    `Excluir o projeto "${state.current.name}"? Isso apaga toda a mídia + os planos. Não tem como desfazer.`,
    {title: "Excluir projeto", okText: "Excluir"}
  ))) return;
  await api(`/api/projects/${state.current.id}`, { method: "DELETE" });
  state.current = null;
  $("#project-view").classList.add("hidden");
  $("#empty").classList.remove("hidden");
  await refreshList();
}

async function uploadAngle(file) {
  if (!state.current) return;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", $("#angle-name").value || `Ângulo ${(state.current.angles?.length || 0) + 1}`);
  log(`▶ Subindo ângulo: ${file.name}`);
  try {
    const r = await api(`/api/projects/${state.current.id}/angles`, { method: "POST", body: fd });
    log(`✓ Ângulo "${r.name}" pronto (${r.duration.toFixed(1)}s)`, "ok");
    $("#angle-name").value = "";
    await loadProject(state.current.id);
  } catch (e) {
    log(`✗ Ângulo: ${e.message}`, "err");
  }
}

async function suggestMusic() {
  if (!state.current) return;
  const btn = $("#music-suggest-btn");
  btn.disabled = true;
  btn.textContent = "✨ Pensando...";
  log("▶ music suggest");
  try {
    const s = await api(`/api/projects/${state.current.id}/music/suggest`, { method: "POST" });
    log(`✓ Sugestão: ${s.description}`, "ok");
    renderMusicSuggestion(s);
  } catch (e) {
    log(`✗ music suggest: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "✨ Sugerir música";
  }
}

async function searchMusic() {
  if (!state.current) return;
  const list = $("#music-tracks");
  list.innerHTML = "";
  log("▶ music search");
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
    log(`✓ ${r.tracks.length} faixas encontradas via ${r.provider}`, "ok");
  } catch (e) {
    log(`✗ music search: ${e.message}`, "err");
  }
}

async function exportFcpxml() {
  if (!state.current) return;
  log("▶ FCPXML export");
  try {
    const res = await api(`/api/projects/${state.current.id}/export/fcpxml`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        multicam: $("#fcpxml-multicam").checked,
        use_cuts: $("#fcpxml-cuts").checked,
        use_roughcut: $("#fcpxml-roughcut")?.checked || false,
        include_broll: $("#fcpxml-broll")?.checked || false,
        include_word_markers: $("#fcpxml-markers").checked,
      }),
    });
    log(`✓ FCPXML ${res.kind} · ${res.bytes} bytes`, "ok");
    const link = $("#fcpxml-link");
    link.href = res.url;
    link.style.display = "inline-block";
    link.textContent = `⬇ Baixar ${res.export}`;
  } catch (e) {
    log(`✗ FCPXML: ${e.message}`, "err");
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

// ---- Workflow filter (mostra só os cards das categorias selecionadas) ------

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
  bindWorkflow();
  $("#cmdk-open")?.addEventListener("click", () => openCmdK());
  // Theme toggle (dark ↔ light, persisted in localStorage)
  const themeBtn = $("#theme-toggle");
  if (themeBtn) {
    const setIcon = () => {
      const isLight = document.documentElement.getAttribute("data-theme") === "light";
      themeBtn.textContent = isLight ? "☾" : "☀";
    };
    setIcon();
    themeBtn.onclick = () => {
      const isLight = document.documentElement.getAttribute("data-theme") === "light";
      if (isLight) {
        document.documentElement.removeAttribute("data-theme");
        localStorage.setItem("hfvp.theme", "dark");
      } else {
        document.documentElement.setAttribute("data-theme", "light");
        localStorage.setItem("hfvp.theme", "light");
      }
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

  // angle uploader (don't trigger file picker when typing in the name field)
  const ai = $("#angle-input");
  $("#angle-zone").addEventListener("click", (e) => {
    if (e.target.id === "angle-name") return;
    ai.click();
  });
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

bind();
refreshList();
