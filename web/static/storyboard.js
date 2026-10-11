export function mount(root, Studio, ImageSettings, initialProjectId = "") {
let active = false;
const lifetime = new AbortController();
function listen(target, type, listener) { target.addEventListener(type, listener, {signal:lifetime.signal}); }
"use strict";

const $ = (id) => root.getElementById(id);
const state = {
  projects: [], project: null, frames: [], selectedId: "", config: null,
  drafts: new Map(), saves: new Map(), timers: new Map(), jobs: new Map(),
  queue: [], epoch: 0, suppressScrollUntil: 0, loadingProject: false, creatingFrame: false,
  librarySelection: new Set(), cardType: "character", applyingCards: false, deletingFrame: "",
  workspaceDrafts: new Map(), workspaceSaves: new Map(), workspaceTimers: new Map(),
  cardDraft: { card_type: "character", name: "", content: "" },
};
let storagePrefix = "workflow.storyboard.v2:";
function readLocal(key) {
  try { return JSON.parse(localStorage.getItem(storagePrefix + key) || "null"); } catch { return null; }
}
function writeLocal(key, value) {
  try { localStorage.setItem(storagePrefix + key, JSON.stringify(value)); }
  catch { message("浏览器无法保存本地草稿，请等待数据库显示已保存后再关闭页面。", true); }
}
function removeLocal(key) { try { localStorage.removeItem(storagePrefix + key); } catch { /* Database save already succeeded. */ } }
function rememberDraft(key, draft) { state.drafts.set(key, draft); writeLocal(`draft:${key}`, draft); }
function rememberProject(id) { writeLocal("last-project", id); }
const labels = { draft: "待生成", queued: "排队中", generating: "生成中", ready: "已完成", failed: "失败，可重试" };
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
const keyFor = (projectId, frameId) => `${projectId}/${frameId}`;
const currentFrame = () => state.frames.find((f) => f.id === state.selectedId);
const currentKey = (frame) => keyFor(state.project.id, frame.id);
function content(frame) { return { ...frame, ...(state.drafts.get(currentKey(frame)) || {}) }; }
function frameStatus(frame) { return state.jobs.get(currentKey(frame))?.status || frame.status; }
function busy(frame) { return ["queued", "generating"].includes(frameStatus(frame)); }
function message(text, error = false) { $("status").textContent = text; $("status").dataset.error = String(error); }
async function api(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw new Error(`请求失败（HTTP ${response.status}），请检查服务连接`); }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（HTTP ${response.status}）`);
  return data;
}
function jsonPost(url, data, options = {}) { return api(url, { ...options, method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }); }
function keepaliveOptions(data) { return { signal: AbortSignal.timeout(15000), keepalive: new Blob([JSON.stringify(data)]).size < 60000 }; }

function rememberWorkspace() {
  const id = state.project?.id; if (!id || state.loadingProject) return;
  const draft = { selected_frame_id: state.selectedId, library_selection: [...state.librarySelection], base_url: $("baseUrl").value.trim(), card_draft: state.cardDraft };
  state.workspaceDrafts.set(id, draft); writeLocal(`workspace:${id}`, draft);
  clearTimeout(state.workspaceTimers.get(id));
  state.workspaceTimers.set(id, setTimeout(() => saveWorkspace(id).catch(() => {}), 300));
}
function saveWorkspace(id) {
  clearTimeout(state.workspaceTimers.get(id)); state.workspaceTimers.delete(id);
  const prior = state.workspaceSaves.get(id) || Promise.resolve();
  const next = prior.catch(() => {}).then(async () => {
    const draft = state.workspaceDrafts.get(id); if (!draft) return;
    const saved = await jsonPost(`/api/projects/${id}/workspace`, draft, keepaliveOptions(draft));
    if (state.project?.id === id) state.project.workspace = saved;
    if (state.workspaceDrafts.get(id) === draft) { state.workspaceDrafts.delete(id); removeLocal(`workspace:${id}`); }
  });
  state.workspaceSaves.set(id, next);
  next.then(() => { if (state.workspaceSaves.get(id) === next) state.workspaceSaves.delete(id); }, () => {
    if (state.workspaceSaves.get(id) === next) state.workspaceSaves.delete(id);
    if (state.workspaceDrafts.has(id)) state.workspaceTimers.set(id, setTimeout(() => saveWorkspace(id).catch(() => {}), 3000));
    message("工作区状态暂未同步到数据库，已保留本地副本并自动重试。", true);
  });
  return next;
}
function frameBody(frame) {
  const item = content(frame);
  return { title: item.title, prompt: item.prompt, system_prompt: item.system_prompt || "", model: item.model, size: item.size,
    character_card_ids: item.character_card_ids || [], scene_card_ids: item.scene_card_ids || [], marked: Boolean(item.marked) };
}
function applyFrame(projectId, frame) {
  if (state.project?.id !== projectId) return;
  const index = state.frames.findIndex((f) => f.id === frame.id);
  if (index !== -1) state.frames[index] = frame;
}

function renderProjects() {
  $("projectSelect").innerHTML = '<option value="">选择项目</option>' + state.projects.map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join("");
  $("projectSelect").value = state.project?.id || "";
}
function referenceCards() {
  return [ ...(state.project?.character_cards || []).map((c) => ({ ...c, type: "character" })),
    ...(state.project?.scene_cards || []).map((c) => ({ ...c, type: "scene" })) ];
}
function cardMarkup(card, checked, disabled = false) {
  return `<label class="reference-card"><input type="checkbox" data-type="${card.type}" value="${escapeHtml(card.id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>${card.image_url ? `<img src="${escapeHtml(card.image_url)}" alt="">` : ""}<span>${escapeHtml(card.name)}<small>${card.type === "character" ? "角色参考" : "场景参考"}${card.image_url ? " · 含参考图" : " · 文字设定"}</small><small class="card-description">${escapeHtml(card.content)}</small></span></label>`;
}
function renderCards() {
  const frame = currentFrame();
  if (!state.project) { $("cardList").innerHTML = '<p class="hint">先选择或创建一个项目。</p>'; return; }
  const item = frame ? content(frame) : {};
  const disabled = !frame || busy(frame) || state.applyingCards || Boolean(state.deletingFrame);
  const cards = referenceCards();
  $("cardList").innerHTML = cards.length ? cards.map((card) => cardMarkup(card, (item[`${card.type}_card_ids`] || []).includes(card.id), disabled)).join("") : '<p class="hint">在左侧创建角色卡或场景卡。</p>';
  $("frameCardsAll").disabled = disabled || !cards.length;
  $("frameCardsNone").disabled = disabled || !cards.length;
}
function renderLibrary() {
  const cards = referenceCards();
  $("libraryCards").innerHTML = cards.length ? cards.map((card) => cardMarkup(card, state.librarySelection.has(card.id), state.applyingCards || Boolean(state.deletingFrame))).join("") : '<p class="hint">暂无参考卡，点击上方按钮创建。</p>';
  for (const id of ["createCharacterCard", "createSceneCard", "libraryAll", "libraryNone"]) $(id).disabled = !state.project || state.loadingProject || state.applyingCards || Boolean(state.deletingFrame);
  $("applyCards").disabled = !state.frames.length || !state.librarySelection.size || state.loadingProject || state.applyingCards || Boolean(state.deletingFrame);
  $("applyCards").textContent = `应用到镜头…${state.librarySelection.size ? `（${state.librarySelection.size} 张卡）` : ""}`;
}
function renderFilm() {
  const track = $("filmTrack");
  // Reuse film elements so progress updates do not reset scrolling or keyboard focus.
  const existing = new Map(Array.from(track.querySelectorAll(".film-frame")).map((el) => [el.dataset.id, el]));
  for (const [id, element] of existing) {
    if (!state.frames.some((frame) => frame.id === id)) element.remove();
  }
  state.frames.forEach((frame, index) => {
    let button = existing.get(frame.id);
    if (!button) {
      button = document.createElement("div"); button.className = "film-frame";
      button.dataset.id = frame.id;
      button.innerHTML = '<button type="button" class="frame-select"></button><button type="button" class="frame-mark"></button><button type="button" class="frame-delete">×</button>';
      track.insertBefore(button, $("filmCreate"));
      button.querySelector(".frame-select").addEventListener("click", () => selectFrame(frame.id, true));
      button.querySelector(".frame-mark").addEventListener("click", () => toggleMark(frame.id));
      button.querySelector(".frame-delete").addEventListener("click", () => deleteFrame(frame.id));
    }
    const item = content(frame), status = frameStatus(frame), selected = frame.id === state.selectedId;
    button.classList.toggle("active", selected); button.dataset.state = status;
    button.classList.toggle("is-marked", Boolean(item.marked));
    const selectButton = button.querySelector(".frame-select"), markButton = button.querySelector(".frame-mark");
    selectButton.setAttribute("aria-pressed", String(selected));
    selectButton.setAttribute("aria-label", `第 ${index + 1} 镜，${item.title}，${labels[status] || status}`);
    markButton.setAttribute("aria-pressed", String(Boolean(item.marked)));
    markButton.setAttribute("aria-label", `${item.marked ? "取消标记" : "标记"}第 ${index + 1} 镜`);
    markButton.title = item.marked ? "取消标记" : "标记后统一生成或更新";
    markButton.textContent = item.marked ? "★" : "☆";
    markButton.disabled = busy(frame) || state.loadingProject || state.applyingCards || Boolean(state.deletingFrame);
    const deleteButton = button.querySelector(".frame-delete");
    deleteButton.disabled = markButton.disabled;
    deleteButton.setAttribute("aria-label", `删除第 ${index + 1} 镜，${item.title}`);
    deleteButton.title = "删除这个镜头";
    const html = `<div class="frame-picture">${frame.image_url ? `<img src="${escapeHtml(frame.image_url)}" alt="${escapeHtml(item.title)}">` : `<span class="frame-placeholder">${String(index + 1).padStart(2, "0")}<small>填写提示词，让画面显影</small></span>`}${busy(frame) ? `<span class="frame-busy"><span class="spinner"></span><span>${status === "queued" ? "镜头排队中…" : "画面正在显影…"}</span></span>` : ""}</div><div class="frame-caption"><span>${String(index + 1).padStart(2, "0")} / ${escapeHtml(item.title)}</span><span class="frame-state">${labels[status] || status}</span></div>`;
    if (selectButton.innerHTML !== html) selectButton.innerHTML = html;
  });
  layoutFilm();
}
function renderPreview() {
  const frame = currentFrame(), index = state.frames.indexOf(frame);
  $("previewNumber").textContent = frame ? `FRAME ${String(index + 1).padStart(2, "0")}` : "FRAME —";
  $("previewTitle").textContent = frame ? content(frame).title : "暂无镜头";
  const status = frame ? frameStatus(frame) : "draft";
  $("previewStatus").textContent = frame ? labels[status] : "未选择";
  $("previewStatus").dataset.state = status;
  $("previewEmpty").hidden = state.frames.length > 0;
  $("download").hidden = !frame?.image_url;
  if (frame?.image_url) $("download").href = frame.image_url;
  $("previousFrame").disabled = index <= 0;
  $("nextFrame").disabled = !frame || index >= state.frames.length - 1;
  $("selectedPosition").textContent = frame ? `${String(index + 1).padStart(2, "0")} / ${String(state.frames.length).padStart(2, "0")}` : "— / —";
  $("frameError").hidden = !frame?.error;
  $("frameError").textContent = frame?.error || "";
  $("versionsPanel").hidden = !frame?.versions?.length;
  $("versionList").innerHTML = (frame?.versions || []).map((v, i) => `<a class="version-link" href="${escapeHtml(v.image_url)}" target="_blank" rel="noopener" title="${escapeHtml(v.prompt)}"><img src="${escapeHtml(v.image_url)}" alt="历史版本 ${i + 1}">版本 ${frame.versions.length - i}</a>`).join("");
}
function renderEditor() {
  const frame = currentFrame(), item = frame ? content(frame) : {}, disabled = !frame || busy(frame) || state.applyingCards || Boolean(state.deletingFrame) || state.loadingProject;
  for (const [id, field] of [["frameTitle", "title"], ["systemPrompt", "system_prompt"], ["prompt", "prompt"], ["model", "model"], ["size", "size"]]) {
    $(id).disabled = disabled;
    const value = item[field] ?? (field === "model" ? state.config?.default_model : field === "size" ? state.config?.default_size : "") ?? "";
    if ($(id).tagName === "SELECT" && value && !Array.from($(id).options).some((o) => o.value === value)) $(id).add(new Option(value, value));
    if ($(id).value !== value) $(id).value = value;
  }
  $("generateFrame").disabled = disabled;
  $("deleteFrame").disabled = disabled;
  $("deleteFrame").textContent = state.deletingFrame === frame?.id ? "删除中…" : "删除这个镜头";
  $("generateFrame").textContent = frame && busy(frame) ? labels[frameStatus(frame)] + "…" : frame?.image_url ? "↻ 重新生成这个镜头" : "▶ 生成这个镜头";
  $("saveState").textContent = frame && state.drafts.has(currentKey(frame)) ? "有修改待保存" : "已保存";
  ImageSettings.syncSize();
}
function render() {
  renderFilm(); renderPreview(); renderEditor(); renderCards(); renderLibrary();
  $("projectSelect").disabled = state.applyingCards || Boolean(state.deletingFrame) || state.loadingProject;
  $("createProject").disabled = state.applyingCards || Boolean(state.deletingFrame) || state.loadingProject;
  $("frameCount").textContent = String(state.frames.length).padStart(2, "0");
  const ready = state.frames.filter((f) => f.status === "ready").length;
  const active = state.frames.filter(busy).length;
  $("progress").textContent = state.frames.length ? `${ready} / ${state.frames.length} 已完成${active ? ` · ${active} 个进行中` : ""}` : "尚未创建镜头";
  $("addFrame").disabled = !state.config || state.loadingProject || state.creatingFrame || state.applyingCards || Boolean(state.deletingFrame);
  $("filmCreate").disabled = $("addFrame").disabled;
  $("filmCreate").querySelector("small").textContent = state.creatingFrame ? "创建中…" : "点击创建空白镜头";
  const marked = state.frames.filter((frame) => content(frame).marked);
  $("batchGenerate").disabled = !marked.some((frame) => !busy(frame)) || state.loadingProject || state.applyingCards || Boolean(state.deletingFrame);
  $("batchGenerate").textContent = `▶ 生成 / 更新已标记（${marked.length}）`;
}
function layoutFilm() {
  const viewport = $("filmScroll"), width = Math.round(viewport.clientWidth * .76);
  if (!width) return;
  viewport.style.setProperty("--frame-width", `${width}px`);
  viewport.style.setProperty("--film-padding", `${(viewport.clientWidth - width) / 2}px`);
  updateFilmScale();
}
function updateFilmScale() {
  const viewport = $("filmScroll"), outer = viewport.getBoundingClientRect(), center = outer.left + viewport.clientWidth / 2;
  let nearest = null, distance = Infinity;
  $("filmTrack").querySelectorAll(".film-frame").forEach((element) => {
    const box = element.getBoundingClientRect(), delta = Math.abs(box.left + box.width / 2 - center);
    const proximity = Math.min(delta / (element.offsetWidth * .88), 1);
    element.style.setProperty("--zoom", String(1 - proximity * .24));
    element.style.setProperty("--fade", String(1 - proximity * .36));
    if (delta < distance) { nearest = element.dataset.id; distance = delta; }
  });
  return nearest;
}
function centerSelected(smooth = true) {
  const button = Array.from($("filmTrack").children).find((el) => el.dataset.id === state.selectedId);
  if (!button) return;
  state.suppressScrollUntil = Date.now() + 550;
  const viewport = $("filmScroll");
  const rect = button.getBoundingClientRect(), outer = viewport.getBoundingClientRect();
  viewport.scrollTo({ left: viewport.scrollLeft + rect.left + rect.width / 2 - outer.left - outer.width / 2,
    behavior: smooth && !matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "instant" });
  updateFilmScale();
}
function selectFrame(id, center = false) {
  const previous = currentFrame();
  if (previous && previous.id !== id && state.drafts.has(currentKey(previous))) saveDraft(state.project.id, previous.id).catch(() => {});
  state.selectedId = id; render();
  rememberWorkspace();
  if (center) centerSelected();
}

function toggleMark(frameId) {
  const frame = state.frames.find((item) => item.id === frameId);
  if (!frame || busy(frame) || state.loadingProject || state.applyingCards || state.deletingFrame) return;
  const key = currentKey(frame), projectId = state.project.id;
  rememberDraft(key, { ...frameBody(frame), marked: !content(frame).marked });
  state.epoch++; render();
  saveDraft(projectId, frameId).catch((error) => message(`标记保存失败：${error.message}`, true));
}

function editCurrent() {
  const frame = currentFrame(); if (!frame || busy(frame) || state.applyingCards || state.deletingFrame || state.loadingProject) return;
  const key = currentKey(frame);
  rememberDraft(key, { ...frameBody(frame), title: $("frameTitle").value, prompt: $("prompt").value, system_prompt: $("systemPrompt").value,
    model: $("model").value, size: ImageSettings.normalizeSize($("size").value),
    character_card_ids: Array.from($("cardList").querySelectorAll('[data-type="character"]:checked')).map((el) => el.value),
    scene_card_ids: Array.from($("cardList").querySelectorAll('[data-type="scene"]:checked')).map((el) => el.value) });
  state.epoch++; $("saveState").textContent = "保存中…";
  clearTimeout(state.timers.get(key));
  const projectId = state.project.id;
  state.timers.set(key, setTimeout(() => saveDraft(projectId, frame.id).catch((error) => message(error.message, true)), 400));
  renderFilm(); renderPreview();
}
function saveDraft(projectId, frameId) {
  const key = keyFor(projectId, frameId);
  clearTimeout(state.timers.get(key)); state.timers.delete(key);
  const prior = state.saves.get(key) || Promise.resolve();
  const next = prior.catch(() => {}).then(async () => {
    const draft = state.drafts.get(key); if (!draft) return;
    const saved = await jsonPost(`/api/projects/${projectId}/frames/${frameId}`, draft, keepaliveOptions(draft));
    applyFrame(projectId, saved); state.epoch++;
    if (state.drafts.get(key) === draft) { state.drafts.delete(key); removeLocal(`draft:${key}`); }
    if (state.project?.id === projectId) render();
  });
  state.saves.set(key, next);
  next.then(() => { if (state.saves.get(key) === next) state.saves.delete(key); }, () => {
    if (state.saves.get(key) === next) state.saves.delete(key);
    if (state.project?.id === projectId && state.selectedId === frameId) $("saveState").textContent = "同步失败，草稿已保留，正在重试";
    if (state.drafts.has(key)) state.timers.set(key, setTimeout(() => saveDraft(projectId, frameId).catch(() => {}), 3000));
  });
  return next;
}

function enqueue(frame) {
  if (busy(frame) || state.applyingCards || state.deletingFrame || state.loadingProject) return false;
  const body = frameBody(frame);
  if (!body.prompt.trim()) return false;
  if (!body.model.trim() || !ImageSettings.validSize(body.size)) {
    message(`「${body.title}」需要有效模型与分辨率，例如 1920x1080。`, true); return false;
  }
  const projectId = state.project.id, key = currentKey(frame);
  const job = { key, projectId, frameId: frame.id, status: "queued", body: {
    ...body, api_key: $("apiKey").value.trim(), base_url: $("baseUrl").value.trim(),
  } };
  state.jobs.set(key, job); state.queue.push(job); state.epoch++;
  return true;
}
async function runJob(job) {
  try {
    await saveDraft(job.projectId, job.frameId);
    job.status = "generating"; render();
    const frame = await jsonPost(`/api/projects/${job.projectId}/frames/${job.frameId}/generate`, job.body);
    applyFrame(job.projectId, frame);
    message(`「${frame.title}」生成完成`);
  } catch (error) {
    try {
      const frames = await api(`/api/projects/${job.projectId}/frames`);
      const frame = frames.find((f) => f.id === job.frameId);
      if (frame) applyFrame(job.projectId, frame);
    } catch { /* Polling can recover after a temporary connection loss. */ }
    message(`「${job.body.title}」：${error.message}`, true);
  } finally {
    state.jobs.delete(job.key); state.epoch++; render();
  }
}
function pump() {
  while (state.queue.length) {
    runJob(state.queue.shift());
  }
}
$("generateFrame").addEventListener("click", () => {
  const frame = currentFrame();
  if (frame && enqueue(frame)) { render(); pump(); }
  else message("请先填写当前镜头的提示词。", true);
});
$("batchGenerate").addEventListener("click", () => {
  const marked = state.frames.filter((frame) => content(frame).marked && !busy(frame));
  const empty = marked.filter((frame) => !content(frame).prompt.trim());
  if (empty.length) {
    message(`请先填写已标记镜头的提示词：${empty.map((frame) => content(frame).title).join("、")}`, true);
    selectFrame(empty[0].id, true); return;
  }
  const count = marked.filter(enqueue).length;
  render(); message(count ? `${count} 个已标记镜头开始生成 / 更新，完成后各自更新` : "请先标记需要生成或更新的镜头。"); pump();
});
for (const id of ["frameTitle", "systemPrompt", "prompt", "model", "size"]) $(id).addEventListener("input", editCurrent);
$("cardList").addEventListener("change", editCurrent);
$("frameCardsAll").addEventListener("click", () => selectFrameCards(true));
$("frameCardsNone").addEventListener("click", () => selectFrameCards(false));
function selectFrameCards(checked) {
  $("cardList").querySelectorAll("input:not(:disabled)").forEach((input) => { input.checked = checked; });
  editCurrent();
}
$("libraryCards").addEventListener("change", (event) => {
  const input = event.target;
  if (input.checked) state.librarySelection.add(input.value); else state.librarySelection.delete(input.value);
  renderLibrary(); rememberWorkspace();
});
$("libraryAll").addEventListener("click", () => { state.librarySelection = new Set(referenceCards().map((card) => card.id)); renderLibrary(); rememberWorkspace(); });
$("libraryNone").addEventListener("click", () => { state.librarySelection.clear(); renderLibrary(); rememberWorkspace(); });
$("baseUrl").addEventListener("input", rememberWorkspace);
$("operationPanel").addEventListener("toggle", async () => {
  if (!$("operationPanel").open || !state.project) return;
  const projectId = state.project.id;
  const names = { "project.create": "创建项目", "project.update": "修改项目", "frame.initialize": "初始化镜头", "frame.create": "添加镜头", "frame.save": "保存镜头修改", "frame.delete": "删除镜头", "cards.apply": "应用参考卡", "card.create": "创建参考卡", "workspace.save": "保存工作区与草稿", "frame.generate.start": "开始生成镜头", "frame.generate.complete": "镜头生成完成", "frame.generate.fail": "镜头生成失败", "frame.recover": "恢复中断的镜头状态", "image.generate.complete": "单图生成完成", "screenplay.save": "保存剧本" };
  Object.assign(names, {"project.metadata":"修改项目资料", "project.sharing":"修改项目共享", "project.legacy.claim":"分配历史项目归属", "cards.import":"导入资源卡片"});
  $("operationList").textContent = "加载中…";
  try {
    const events = await api(`/api/projects/${projectId}/events?limit=20`);
    if (state.project?.id !== projectId) return;
    $("operationList").innerHTML = events.length ? events.map((event) => `<p>${escapeHtml(names[event.operation] || "保存修改")}<br><small>${escapeHtml(new Date(event.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }))}</small></p>`).join("") : "暂无操作记录。";
  } catch (error) { $("operationList").textContent = error.message; }
});

let cardPreviewUrl;
for (const button of root.querySelectorAll("[data-close]")) button.addEventListener("click", () => $(button.dataset.close).close());
for (const [id, type] of [["createCharacterCard", "character"], ["createSceneCard", "scene"]]) $(id).addEventListener("click", () => {
  state.cardType = type; $("cardForm").reset(); $("cardFormError").hidden = true; $("cardImagePreview").hidden = true;
  if (cardPreviewUrl) { URL.revokeObjectURL(cardPreviewUrl); cardPreviewUrl = null; }
  $("cardDialogTitle").textContent = type === "character" ? "创建角色卡" : "创建场景卡";
  if (state.cardDraft.card_type === type) { $("cardName").value = state.cardDraft.name; $("cardContent").value = state.cardDraft.content; }
  $("cardDialog").showModal();
});
for (const id of ["cardName", "cardContent"]) $(id).addEventListener("input", () => {
  state.cardDraft = { card_type: state.cardType, name: $("cardName").value, content: $("cardContent").value };
  rememberWorkspace();
});
$("cardImage").addEventListener("change", () => {
  if (cardPreviewUrl) URL.revokeObjectURL(cardPreviewUrl);
  const file = $("cardImage").files[0]; cardPreviewUrl = file ? URL.createObjectURL(file) : null;
  $("cardImagePreview").hidden = !file;
  if (file) $("cardImagePreview").src = cardPreviewUrl;
});
$("cardForm").addEventListener("submit", async (event) => {
  event.preventDefault(); const projectId = state.project?.id; if (!projectId) return;
  const name = $("cardName").value.trim(), description = $("cardContent").value.trim();
  if (!name || !description) { $("cardFormError").textContent = "请填写名称和设定描述。"; $("cardFormError").hidden = false; return; }
  $("saveCard").disabled = true; $("cardFormError").hidden = true;
  try {
    const form = new FormData(); form.append("card_type", state.cardType); form.append("name", name); form.append("content", description);
    if ($("cardImage").files[0]) form.append("image", $("cardImage").files[0]);
    const project = await api(`/api/projects/${projectId}/cards`, { method: "POST", body: form });
    if (state.project?.id === projectId) {
      state.project.character_cards = project.character_cards; state.project.scene_cards = project.scene_cards;
      const cards = project[`${state.cardType}_cards`]; state.librarySelection.add(cards[cards.length - 1].id);
      render(); rememberWorkspace();
    }
    state.cardDraft = { card_type: state.cardType, name: "", content: "" }; rememberWorkspace();
    $("cardDialog").close(); message("参考卡已保存，选择镜头即可应用。");
  } catch (error) { $("cardFormError").textContent = error.message; $("cardFormError").hidden = false; }
  finally { $("saveCard").disabled = false; }
});
$("applyCards").addEventListener("click", () => {
  $("applySummary").textContent = `参考卡：${referenceCards().filter((card) => state.librarySelection.has(card.id)).map((card) => card.name).join("、")}`;
  $("targetFrames").innerHTML = state.frames.map((frame, index) => `<label class="reference-card"><input type="checkbox" value="${escapeHtml(frame.id)}" ${frame.id === state.selectedId && !busy(frame) ? "checked" : ""} ${busy(frame) ? "disabled" : ""}><span>${String(index + 1).padStart(2, "0")} · ${escapeHtml(content(frame).title)}<small>${busy(frame) ? "正在生成，暂不可应用" : "可应用参考卡"}</small></span></label>`).join("");
  $("applyError").hidden = true; $("applyDialog").showModal();
});
for (const [id, checked] of [["targetsAll", true], ["targetsNone", false]]) $(id).addEventListener("click", () => {
  $("targetFrames").querySelectorAll("input:not(:disabled)").forEach((input) => { input.checked = checked; });
});
$("confirmApply").addEventListener("click", async () => {
  const projectId = state.project?.id;
  const frameIds = Array.from($("targetFrames").querySelectorAll("input:checked:not(:disabled)")).map((input) => input.value);
  if (!frameIds.length) { $("applyError").textContent = "请至少选择一个镜头。"; $("applyError").hidden = false; return; }
  const cards = referenceCards().filter((card) => state.librarySelection.has(card.id));
  const payload = { frame_ids: frameIds, character_card_ids: cards.filter((card) => card.type === "character").map((card) => card.id), scene_card_ids: cards.filter((card) => card.type === "scene").map((card) => card.id) };
  state.applyingCards = true; state.epoch++; $("confirmApply").disabled = true; render();
  try {
    await Promise.all(frameIds.map((id) => saveDraft(projectId, id)));
    const updated = await jsonPost(`/api/projects/${projectId}/frames/apply-cards`, payload);
    updated.forEach((frame) => applyFrame(projectId, frame)); state.epoch++;
    $("applyDialog").close(); message(`参考卡已应用到 ${updated.length} 个镜头，生成时会使用这些设定和参考图。`);
  } catch (error) { $("applyError").textContent = error.message; $("applyError").hidden = false; }
  finally { state.applyingCards = false; $("confirmApply").disabled = false; render(); }
});

async function switchProject(id) {
  flushPending();
  state.loadingProject = true; state.epoch++; render();
  const requested = ++switchProject.version;
  try {
    const project = id ? await api(`/api/projects/${id}`) : null;
    const frames = id ? await jsonPost(`/api/projects/${id}/frames/initialize`, {}) : [];
    if (requested !== switchProject.version) return;
    state.project = project; state.frames = frames;
    $("operationPanel").open = false; $("operationList").textContent = "展开后加载。";
    const workspace = state.workspaceDrafts.get(id) || readLocal(`workspace:${id}`) || project?.workspace || {};
    state.selectedId = frames.some((f) => f.id === workspace.selected_frame_id) ? workspace.selected_frame_id : frames[0]?.id || "";
    state.librarySelection = new Set((workspace.library_selection || []).filter((cardId) => referenceCards().some((c) => c.id === cardId)));
    $("baseUrl").value = workspace.base_url ?? state.config.base_url;
    state.cardDraft = workspace.card_draft || { card_type: "character", name: "", content: "" };
    for (const frame of frames) {
      const key = keyFor(id, frame.id), draft = readLocal(`draft:${key}`);
      if (draft && !state.drafts.has(key)) state.drafts.set(key, draft);
      if (state.drafts.has(key) && !busy(frame)) saveDraft(id, frame.id).catch(() => {});
    }
    rememberProject(id);
    renderProjects(); render(); centerSelected(false);
    if (active) Studio.projectSelected(id);
    message(frames.length ? "工作台已就绪，选择镜头继续创作" : "新增镜头，让故事从第一帧开始");
  } catch (error) { message(error.message, true); renderProjects(); }
  finally { if (requested === switchProject.version) { state.loadingProject = false; render(); rememberWorkspace(); } }
}
switchProject.version = 0;
$("projectSelect").addEventListener("change", (event) => switchProject(event.target.value));
$("createProject").addEventListener("click", async () => {
  const name = $("projectName").value.trim(); if (!name) { message("请填写新项目名称。", true); return; }
  $("createProject").disabled = true;
  try {
    const form = new FormData(); form.append("name", name);
    const project = await api("/api/projects", { method: "POST", body: form });
    state.projects.unshift(project); $("projectName").value = ""; await switchProject(project.id);
  } catch (error) { message(error.message, true); }
  finally { $("createProject").disabled = false; }
});
async function createBlankFrame() {
  if (!state.config || state.creatingFrame || state.loadingProject || state.deletingFrame || state.applyingCards) return;
  state.creatingFrame = true; state.epoch++; render();
  try {
    if (!state.project) {
      const form = new FormData(); form.append("name", "分镜项目");
      const project = await api("/api/projects", { method: "POST", body: form });
      state.projects.unshift(project); await switchProject(project.id);
      message("已创建项目和一个空白镜头。");
      return;
    }
    const projectId = state.project?.id;
    if (!projectId) throw new Error("项目加载失败，请重试。");
    const frame = await jsonPost(`/api/projects/${projectId}/frames`, { prompt: "", model: state.config.default_model, size: state.config.default_size });
    state.epoch++;
    if (state.project?.id === projectId) {
      state.frames.push(frame); selectFrame(frame.id, true);
    }
    message("已创建一个空白分镜，可在右侧填写提示词。");
  } catch (error) { message(error.message, true); }
  finally { state.creatingFrame = false; render(); }
}
$("addFrame").addEventListener("click", createBlankFrame);
$("filmCreate").addEventListener("click", createBlankFrame);

async function deleteFrame(frameId) {
  const frame = state.frames.find((item) => item.id === frameId);
  if (!frame || busy(frame) || state.deletingFrame || state.loadingProject || state.creatingFrame || state.applyingCards) return;
  const title = content(frame).title;
  if (!confirm(`删除「${title}」及其镜头历史？`)) return;
  const projectId = state.project.id, key = currentKey(frame), index = state.frames.indexOf(frame);
  state.deletingFrame = frameId; state.epoch++;
  clearTimeout(state.timers.get(key)); state.timers.delete(key); render();
  try {
    // Finish any in-flight autosave before removing the record.
    await (state.saves.get(key) || Promise.resolve()).catch(() => {});
    const frames = await api(`/api/projects/${projectId}/frames/${frameId}`, { method: "DELETE" });
    state.drafts.delete(key); state.saves.delete(key); removeLocal(`draft:${key}`); state.epoch++;
    if (state.project?.id === projectId) {
      const wasSelected = state.selectedId === frameId;
      state.frames = frames;
      if (wasSelected || !frames.some((item) => item.id === state.selectedId)) state.selectedId = frames[Math.min(index, frames.length - 1)]?.id || "";
      render();
      if (state.selectedId) centerSelected(false); else $("filmScroll").scrollTo({ left: 0, behavior: "instant" });
      rememberWorkspace();
    }
    message(`已删除「${title}」。${frames.length ? "" : "点击新建分镜可继续添加。"}`);
  } catch (error) { message(`删除失败：${error.message}`, true); }
  finally { state.deletingFrame = ""; render(); }
}
$("deleteFrame").addEventListener("click", () => { const frame = currentFrame(); if (frame) deleteFrame(frame.id); });

function step(direction) {
  const index = state.frames.findIndex((f) => f.id === state.selectedId);
  const frame = state.frames[index + direction]; if (frame) selectFrame(frame.id, true);
}
$("previousFrame").addEventListener("click", () => step(-1));
$("nextFrame").addEventListener("click", () => step(1));
root.addEventListener("keydown", (event) => {
  if (!active) return;
  if (/INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || root.querySelector("dialog[open]")) return;
  if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); }
});
let scrollTimer, scrollTick;
$("filmScroll").addEventListener("scroll", () => {
  if (!scrollTick) scrollTick = requestAnimationFrame(() => { scrollTick = null; updateFilmScale(); });
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(() => {
    if (Date.now() < state.suppressScrollUntil) return;
    const nearest = updateFilmScale();
    if (nearest && nearest !== state.selectedId) selectFrame(nearest);
  }, 100);
}, { passive: true });
$("filmScroll").addEventListener("wheel", (event) => {
  state.suppressScrollUntil = 0;
  if ($("filmScroll").scrollWidth <= $("filmScroll").clientWidth || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  event.preventDefault(); $("filmScroll").scrollLeft += event.deltaY;
}, { passive: false });
$("filmScroll").addEventListener("pointerdown", () => { state.suppressScrollUntil = 0; }, { passive: true });
const filmObserver = new ResizeObserver(() => { if (active) { layoutFilm(); centerSelected(false); } });
filmObserver.observe($("filmScroll"));
listen(window,"beforeunload", (event) => {
  if (state.drafts.size) { event.preventDefault(); event.returnValue = ""; }
});
function flushPending() {
  for (const key of state.drafts.keys()) {
    const [projectId, frameId] = key.split("/");
    if (!state.saves.has(key) && state.deletingFrame !== frameId) saveDraft(projectId, frameId).catch(() => {});
  }
  for (const id of state.workspaceDrafts.keys()) if (!state.workspaceSaves.has(id)) saveWorkspace(id).catch(() => {});
}
listen(window,"pagehide", flushPending);
listen(document,"visibilitychange", () => { if (document.hidden) flushPending(); });
listen(window,"online", flushPending);
let polling = false;
async function poll() {
  if (!active || polling || !state.project || state.loadingProject || state.creatingFrame || state.applyingCards || state.deletingFrame || document.hidden) return;
  polling = true; const projectId = state.project.id, epoch = state.epoch;
  try {
    const frames = await api(`/api/projects/${projectId}/frames`);
    if (state.project?.id !== projectId || state.epoch !== epoch || state.loadingProject || state.creatingFrame || state.applyingCards || state.deletingFrame) return;
    state.frames = frames; render(); $("connection").textContent = "已连接"; flushPending();
  } catch { $("connection").textContent = "连接中断，正在重试"; }
  finally { polling = false; }
}
async function init() {
  try {
    const [config, projects] = await Promise.all([Studio.cached("/api/config", 600000), Studio.cached("/api/projects")]);
    state.config = config; state.projects = projects;
    if (!state.projects.length) {
      const form = new FormData(); form.append("name", "分镜项目");
      state.projects.push(await api("/api/projects", { method: "POST", body: form }));
    }
    $("baseUrl").value = config.base_url;
    $("apiKey").placeholder = config.has_api_key ? "已配置服务端 Key，可留空" : "填写 API Key";
    ImageSettings.init(config);
    const id = initialProjectId || readLocal("last-project");
    await switchProject(projects.some((p) => p.id === id) ? id : projects[0]?.id || "");
    $("connection").textContent = "已连接";
  } catch (error) { message(error.message, true); $("connection").textContent = "连接失败"; throw error; }
}
storagePrefix += Studio.user.id + ":";
const ready = init(), pollTimer = setInterval(poll,2500);
async function synchronizeProject() {
  const id = state.project?.id; if (!id) return;
  try {
    const project = await api(`/api/projects/${id}`);
    if (!active || state.project?.id !== id) return;
    state.project.character_cards = project.character_cards; state.project.scene_cards = project.scene_cards;
    state.project.name = project.name;
    renderCards(); renderLibrary();
  } catch (error) { if (active && state.project?.id === id) message(error.message,true); }
}
return {
  ready,
  async activate(projectId) {
    active = true;
    if (projectId && projectId !== state.project?.id) {
      state.projects = await Studio.api("/api/projects"); await switchProject(projectId);
    }
    if (!active) return;
    Studio.projectSelected(state.project?.id || "");
    layoutFilm(); centerSelected(false); poll(); synchronizeProject();
  },
  deactivate() { active = false; flushPending(); },
  dispose() { active = false; lifetime.abort(); clearInterval(pollTimer); filmObserver.disconnect(); },
};
}
