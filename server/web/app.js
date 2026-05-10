const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { current: null, sse: null };

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
      <div class="pi-name">${p.name}</div>
      <div class="pi-meta"><span class="dot ${p.has_render ? "ok" : ""}"></span>${new Date(p.updated_at).toLocaleString()}</div>
    `;
    btn.onclick = () => loadProject(p.id);
    root.appendChild(btn);
  }
}

async function loadProject(id) {
  const p = await api(`/api/projects/${id}`);
  state.current = p;
  attachEventStream(id);
  renderStaleWarnings(p);
  renderCutsTimeline(p);
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

function renderSoundbites(analysis) {
  const root = $("#topics-list");
  root.innerHTML = "";
  const byTopic = {};
  for (const sb of analysis.soundbites || []) {
    (byTopic[sb.topic] = byTopic[sb.topic] || []).push(sb);
  }
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
        ${bites.map(b => `
          <label class="bite">
            <input type="checkbox" data-bite="${b.id}" />
            <div class="bite-body">
              <div class="bite-meta">
                <span>${b.start.toFixed(1)}s – ${b.end.toFixed(1)}s</span>
                <span class="bite-score ${b.score >= 80 ? 'high' : b.score >= 60 ? 'mid' : 'low'}">${b.score}</span>
                ${b.summary ? `<span>${escapeHtml(b.summary)}</span>` : ""}
              </div>
              <div class="bite-text">${escapeHtml(b.text)}</div>
            </div>
          </label>
        `).join("")}
      </div>
    `;
    root.appendChild(card);
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
    li.innerHTML = `
      <span class="a-name">${escapeHtml(a.name)}</span>
      <span class="a-meta">${(a.duration || 0).toFixed(1)}s · ${a.filename}${a.tags?.summary ? ' · ' + escapeHtml(a.tags.summary) : ''}</span>
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

  // Pick: if any checkboxes are manually checked beyond what the story has,
  // use those as soundbite_ids; otherwise use the story.
  const checked = $$("input[data-bite]").filter(c => c.checked).map(c => c.dataset.bite);
  const useStory = state.current.has_story && checked.length === 0;

  try {
    const r = await api(`/api/projects/${state.current.id}/roughcut`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        use_story: useStory,
        soundbite_ids: useStory ? null : checked,
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
    const res = await api(`/api/projects/${state.current.id}/export/captions?fmt=${fmt}&use_roughcut=${useRoughcut}`, { method: "POST" });
    log(`✓ ${fmt} · ${res.cues} cues`, "ok");
    const link = $("#caps-link");
    link.href = res.url;
    link.style.display = "inline-block";
    link.textContent = `⬇ ${res.export}`;
  } catch (e) {
    log(`✗ ${fmt}: ${e.message}`, "err");
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
        if (!confirm(`Restaurar ${s.label}?`)) return;
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
  if (!confirm(`Duplicar "${state.current.name}"?`)) return;
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
    const r = await api(`/api/projects/${state.current.id}/speakers`, { method: "POST" });
    log(`✓ falas · ${r.turns.length} turnos`, "ok");
    toast(`${r.turns.length} turnos detectados`, "ok");
  } catch (e) {
    log(`✗ speakers: ${e.message}`, "err");
  }
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
  if (!confirm(`Excluir "${state.current.name}"?`)) return;
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

function bind() {
  $("#new-project").onclick = newProject;
  $("#empty-new").onclick = newProject;
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
  $("#thumbs-btn").onclick = chapterThumbs;
  $("#tx-clear-btn").onclick = txClear;
  $("#tx-keep-btn").onclick = txKeep;
  $("#hl-btn").onclick = buildHighlights;
  $("#social-btn").onclick = generateSocialCopy;
  $("#apply-template-btn").onclick = applyTemplate;
  $("#audio-btn").onclick = exportAudio;
  $("#cancel-render-btn").onclick = cancelRender;
  attachVideoSync();

  for (const btn of $$("[data-run]")) {
    btn.addEventListener("click", () => runStage(btn.dataset.run));
  }
}

bind();
refreshList();
