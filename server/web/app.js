const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { current: null };

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
    $("#brand-name").value = b.name || "";
    $("#brand-tagline").value = b.tagline || "";
    $("#brand-intro-title").value = b.intro_title || "";
    $("#brand-intro-sub").value = b.intro_subtitle || "";
    $("#brand-outro").value = b.outro_text || "";
    $("#brand-cap-pos").value = b.caption_position || "bottom";
    $("#c-primary").value = b.palette?.primary || "#a78bfa";
    $("#c-secondary").value = b.palette?.secondary || "#3b82f6";
    $("#c-accent").value = b.palette?.accent || "#f472b6";
    $("#c-bg").value = b.palette?.background || "#06060a";
    $("#c-fg").value = b.palette?.foreground || "#ffffff";
  } catch {}

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
  const body = {
    name: $("#brand-name").value || "Brand",
    tagline: $("#brand-tagline").value || null,
    intro_title: $("#brand-intro-title").value || null,
    intro_subtitle: $("#brand-intro-sub").value || null,
    outro_text: $("#brand-outro").value || null,
    caption_position: $("#brand-cap-pos").value,
    palette: {
      primary: $("#c-primary").value,
      secondary: $("#c-secondary").value,
      accent: $("#c-accent").value,
      background: $("#c-bg").value,
      foreground: $("#c-fg").value,
    },
  };
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
      body = { language: $("#opt-filler-lang").value, pad: 0.04 };
    } else if (name === "render") {
      body = { aspect: $("#render-aspect").value, include_captions: true };
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

  for (const btn of $$("[data-run]")) {
    btn.addEventListener("click", () => runStage(btn.dataset.run));
  }
}

bind();
refreshList();
