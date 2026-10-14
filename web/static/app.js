export function mount(root, Studio, ImageSettings, initialProjectId = "") {
let active = false;
const lifetime = new AbortController();
function listen(target, type, listener) { target.addEventListener(type, listener, {signal:lifetime.signal}); }
const state = {
  config: null,
  projects: [],
  currentProjectId: "",
};

const els = {
  submitBtn: root.getElementById("submitBtn"),
  submitStatus: root.getElementById("submitStatus"),
  configStatus: root.getElementById("configStatus"),
  apiKey: root.getElementById("apiKey"),
  baseUrl: root.getElementById("baseUrl"),
  projectSelect: root.getElementById("projectSelect"),
  projectName: root.getElementById("projectName"),
  characterName: root.getElementById("characterName"),
  characterContent: root.getElementById("characterContent"),
  characterImage: root.getElementById("characterImage"),
  characterImageText: root.getElementById("characterImageText"),
  sceneName: root.getElementById("sceneName"),
  sceneContent: root.getElementById("sceneContent"),
  sceneImage: root.getElementById("sceneImage"),
  sceneImageText: root.getElementById("sceneImageText"),
  addCharacterCardBtn: root.getElementById("addCharacterCardBtn"),
  addSceneCardBtn: root.getElementById("addSceneCardBtn"),
  characterCardList: root.getElementById("characterCardList"),
  sceneCardList: root.getElementById("sceneCardList"),
  prompt: root.getElementById("prompt"),
  systemPrompt: root.getElementById("systemPrompt"),
  referenceImages: root.getElementById("referenceImages"),
  referenceImagesText: root.getElementById("referenceImagesText"),
  referencePreview: root.getElementById("referencePreview"),
  model: root.getElementById("model"),
  size: root.getElementById("size"),
  resultEmpty: root.getElementById("resultEmpty"),
  resultCard: root.getElementById("resultCard"),
  resultImage: root.getElementById("resultImage"),
  resultMeta: root.getElementById("resultMeta"),
  resultPrompt: root.getElementById("resultPrompt"),
  resultComposedPrompt: root.getElementById("resultComposedPrompt"),
  resultLink: root.getElementById("resultLink"),
  historyList: root.getElementById("historyList"),
};

function setStatus(text) {
  els.submitStatus.textContent = text;
}

let singleDraftPrefix = "workflow.single.v2:";
const singleDrafts = new Map(), singleSaves = new Map(), singleTimers = new Map();
function readSingleDraft(key) { try { return JSON.parse(localStorage.getItem(singleDraftPrefix + key) || "null"); } catch { return null; } }
function writeSingleDraft(key, draft) { try { localStorage.setItem(singleDraftPrefix + key, JSON.stringify(draft)); } catch { setStatus("无法保存本地草稿，请等待数据库同步后再关闭页面。"); } }
function captureSingleDraft() {
  return { prompt: els.prompt.value, system_prompt: els.systemPrompt.value, model: els.model.value, size: els.size.value,
    project_name: els.projectName.value, character_card_ids: selectedCardIds("character"), scene_card_ids: selectedCardIds("scene"),
    character_name: els.characterName.value, character_content: els.characterContent.value, scene_name: els.sceneName.value, scene_content: els.sceneContent.value };
}
function rememberSingleDraft() {
  const id = state.currentProjectId || "new", draft = captureSingleDraft();
  singleDrafts.set(id, draft); writeSingleDraft(id, draft);
  clearTimeout(singleTimers.get(id));
  if (id !== "new") singleTimers.set(id, setTimeout(() => saveSingleDraft(id).catch(() => {}), 400));
}
function restoreSingleDraft() {
  const id = state.currentProjectId || "new", local = readSingleDraft(id), draft = local || currentProject()?.workspace?.single_image_draft;
  if (!draft) return;
  for (const [element, field] of [[els.prompt, "prompt"], [els.systemPrompt, "system_prompt"], [els.model, "model"], [els.size, "size"],
    [els.projectName, "project_name"], [els.characterName, "character_name"], [els.characterContent, "character_content"], [els.sceneName, "scene_name"], [els.sceneContent, "scene_content"]]) element.value = draft[field] || "";
  applySelectedCards("character", draft.character_card_ids); applySelectedCards("scene", draft.scene_card_ids);
  ImageSettings.syncSize(); updateReferencePreview();
  if (local && id !== "new") { singleDrafts.set(id, draft); saveSingleDraft(id).catch(() => {}); }
}
function resetProjectEditor() {
  for (const element of [els.prompt, els.systemPrompt, els.projectName, els.characterName, els.characterContent, els.sceneName, els.sceneContent,
    els.characterImage, els.sceneImage, els.referenceImages]) element.value = "";
  els.model.value = state.config.default_model;
  els.size.value = state.config.default_size;
  ImageSettings.syncSize();
  updateSingleFileText(els.characterImage, els.characterImageText, "选择参考图");
  updateSingleFileText(els.sceneImage, els.sceneImageText, "选择参考图");
  els.resultEmpty.classList.remove("is-hidden");
  els.resultCard.classList.add("is-hidden");
  els.resultImage.removeAttribute("src"); els.resultLink.href = "#";
  els.resultMeta.textContent = ""; els.resultPrompt.textContent = ""; els.resultComposedPrompt.textContent = "";
}
function saveSingleDraft(id) {
  clearTimeout(singleTimers.get(id)); singleTimers.delete(id);
  const prior = singleSaves.get(id) || Promise.resolve();
  const next = prior.catch(() => {}).then(async () => {
    const draft = singleDrafts.get(id); if (!draft) return;
    const body = JSON.stringify({ single_image_draft: draft });
    const response = await fetch(`/photodream/api/projects/${id}/workspace`, { method: "POST", headers: { "Content-Type": "application/json" },
      body, signal: AbortSignal.timeout(15000), keepalive: new Blob([body]).size < 60000 });
    if (!response.ok) throw new Error("草稿同步失败");
    const workspace = await response.json();
    const project = state.projects.find((p) => p.id === id); if (project) project.workspace = workspace;
    if (singleDrafts.get(id) === draft) {
      singleDrafts.delete(id); try { localStorage.removeItem(singleDraftPrefix + id); } catch { /* Saved to database. */ }
      if (state.currentProjectId === id) setStatus("草稿已保存到数据库");
    }
  });
  singleSaves.set(id, next);
  next.then(() => { if (singleSaves.get(id) === next) singleSaves.delete(id); }, () => {
    if (singleSaves.get(id) === next) singleSaves.delete(id);
    if (singleDrafts.has(id)) singleTimers.set(id, setTimeout(() => saveSingleDraft(id).catch(() => {}), 3000));
    if (state.currentProjectId === id) setStatus("草稿暂未同步，已保留本地副本并自动重试");
  });
  return next;
}
function flushSingleDrafts() { for (const id of singleDrafts.keys()) if (id !== "new" && !singleSaves.has(id)) saveSingleDraft(id).catch(() => {}); }

function currentProject() {
  return state.projects.find((item) => item.id === state.currentProjectId) || null;
}

function selectedCardIds(type) {
  const selector = type === "character"
    ? '[data-card-type="character"]:checked'
    : '[data-card-type="scene"]:checked';
  return Array.from(root.querySelectorAll(selector)).map((el) => el.value);
}

function applySelectedCards(type, ids) {
  const selector = type === "character"
    ? '[data-card-type="character"]'
    : '[data-card-type="scene"]';
  const selected = new Set(ids || []);
  root.querySelectorAll(selector).forEach((el) => {
    el.checked = selected.has(el.value);
  });
}

function collectSelectionSnapshot() {
  return {
    character: selectedCardIds("character"),
    scene: selectedCardIds("scene"),
  };
}

function applySelectionSnapshot(snapshot) {
  if (!snapshot) return;
  applySelectedCards("character", snapshot.character || []);
  applySelectedCards("scene", snapshot.scene || []);
}

function selectFirstCardSetIfEmpty() {
  if (selectedCardIds("character").length === 0) {
    const firstCharacter = root.querySelector('[data-card-type="character"]');
    if (firstCharacter) firstCharacter.checked = true;
  }
  if (selectedCardIds("scene").length === 0) {
    const firstScene = root.querySelector('[data-card-type="scene"]');
    if (firstScene) firstScene.checked = true;
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fillSelect(selectEl, values, activeValue) {
  const options = (values || []).map((value) => {
    const escaped = escapeHtml(value);
    const selected = value === activeValue ? " selected" : "";
    return `<option value="${escaped}"${selected}>${escaped}</option>`;
  });
  selectEl.innerHTML = options.join("");
  if (activeValue) {
    selectEl.value = activeValue;
  }
}

function renderProjectOptions() {
  const options = ['<option value="">新建项目</option>'].concat(
    state.projects.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`)
  );
  els.projectSelect.innerHTML = options.join("");
  els.projectSelect.value = state.currentProjectId;
}

function renderCardList(container, cards, type) {
  if (!cards.length) {
    container.innerHTML = '<div class="empty-state">暂无卡片。</div>';
    return;
  }

  container.innerHTML = cards.map((card) => {
    const thumbHtml = card.image_url
      ? `<img class="card-chip__thumb" src="${escapeHtml(Studio.asset(card.image_url))}" alt="${escapeHtml(card.name)}">`
      : "";
    return `
    <div class="card-resource"><label class="card-chip">
      <input type="checkbox" data-card-type="${type}" value="${escapeHtml(card.id)}">
      ${thumbHtml}
      <span>
        <strong>${escapeHtml(card.name)}</strong>
        <small>${escapeHtml(card.content)}</small>
      </span>
    </label>${Studio.cardActions(card, type, false, "single")}</div>
  `;
  }).join("");
}

function renderProjectDetails() {
  const project = currentProject();
  root.getElementById("generationHistory").href = project ? `/photodream/?project=${project.id}#generations` : "/photodream/#generations";
  if (!project) {
    renderCardList(els.characterCardList, [], "character");
    renderCardList(els.sceneCardList, [], "scene");
    return;
  }
  renderCardList(els.characterCardList, project.character_cards || [], "character");
  renderCardList(els.sceneCardList, project.scene_cards || [], "scene");
}

function refillFromShot(shot) {
  if (!shot) return;
  els.prompt.value = shot.prompt || "";
  els.systemPrompt.value = shot.system_prompt || "";
  if (shot.model) {
    els.model.value = shot.model;
  }
  if (shot.size) {
    els.size.value = shot.size;
  }
  ImageSettings.syncSize();
  applySelectedCards("character", shot.character_card_ids || []);
  applySelectedCards("scene", shot.scene_card_ids || []);
  updateReferencePreview();
  setStatus(`已从第 ${shot.shot_number} 镜回填参数`);
}

async function downloadShot(shot) {
  try {
    const resp = await fetch(Studio.asset(shot.image_url));
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `shot_${shot.shot_number}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setStatus(`第 ${shot.shot_number} 镜下载完成`);
  } catch (err) {
    setStatus(`下载失败：${err.message}`);
  }
}

function renderResult(shot) {
  els.resultEmpty.classList.add("is-hidden");
  els.resultCard.classList.remove("is-hidden");
  els.resultImage.src = Studio.asset(shot.image_url);
  els.resultLink.href = Studio.asset(shot.image_url);
  root.getElementById("resultDownload").href = Studio.downloadUrl(shot.image_url);
  els.resultMeta.textContent = `第 ${shot.shot_number} 镜 / ${shot.mode} / ${shot.model} / ${shot.size}`;
  els.resultPrompt.textContent = `系统提示词：${shot.system_prompt || "（未填写）"}\n用户提示词：${shot.prompt || ""}`;
  els.resultComposedPrompt.textContent = `最终发送提示词：${shot.composed_prompt || ""}`;
}

function renderHistory(project) {
  const shots = project && project.shots ? project.shots : [];
  if (!shots.length) {
    els.historyList.innerHTML = '<div class="empty-state">暂无镜头历史。</div>';
    return;
  }

  els.historyList.innerHTML = shots.map((shot) => `
    <article class="history-item">
      <img src="${escapeHtml(Studio.asset(shot.image_url))}" alt="历史镜头">
      <div>
        <strong>第 ${shot.shot_number} 镜</strong>
        <p>${escapeHtml(shot.mode)} / ${escapeHtml(shot.model)} / ${escapeHtml(shot.size)}</p>
        <p>角色卡：${(shot.character_card_ids || []).length} / 场景卡：${(shot.scene_card_ids || []).length}</p>
        <p>${escapeHtml(shot.created_at)}</p>
        <div class="history-actions">
          <button class="btn btn--small history-action" type="button" data-action="refill" data-shot-id="${escapeHtml(shot.id)}">回填</button>
          <button class="btn btn--small history-action" type="button" data-action="retry" data-shot-id="${escapeHtml(shot.id)}">重试</button>
          <button class="btn btn--small history-action" type="button" data-action="download" data-shot-id="${escapeHtml(shot.id)}">下载</button>
          <button class="btn btn--small danger history-action" type="button" data-action="remove" data-shot-id="${escapeHtml(shot.id)}">移除</button>
        </div>
      </div>
    </article>
  `).join("");

  root.querySelectorAll(".history-action").forEach((button) => {
    button.addEventListener("click", () => {
      const targetShot = shots.find((item) => item.id === button.dataset.shotId);
      if (!targetShot) return;

      if (button.dataset.action === "download") {
        downloadShot(targetShot);
        return;
      }
      if (button.dataset.action === "remove") {
        removeHistoryShot(targetShot).catch(error => setStatus(error.message));
        return;
      }

      refillFromShot(targetShot);
      if (button.dataset.action === "retry") {
        submitForm(new Event("submit")).catch((error) => setStatus(`失败：${error.message}`));
      }
    });
  });
}

async function removeHistoryShot(shot) {
  if (!state.currentProjectId || !confirm("从工作台历史中移除这张图片？原图和当时的提示词仍保留在生成日志中。")) return;
  const id = state.currentProjectId;
  const project = await Studio.api(`/photodream/api/projects/${id}/shots/${shot.id}`, {method:"DELETE"});
  if (state.currentProjectId !== id) return;
  state.projects[state.projects.findIndex(p => p.id === id)] = project;
  renderHistory(project);
  if (els.resultImage.getAttribute("src") === Studio.asset(shot.image_url)) {
    if (project.shots.length) renderResult(project.shots[0]);
    else { els.resultEmpty.classList.remove("is-hidden"); els.resultCard.classList.add("is-hidden"); }
  }
  setStatus("图片已移出工作台，生成日志中仍可查看和下载。");
}

function updateSingleFileText(inputEl, textEl, emptyText) {
  if (!inputEl.files || !inputEl.files.length) {
    textEl.textContent = emptyText;
    return;
  }
  textEl.textContent = inputEl.files[0].name;
}

let refObjectUrls = [];

function updateReferencePreview() {
  refObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  refObjectUrls = [];

  const project = currentProject();
  const files = Array.from(els.referenceImages.files || []);
  const characterIds = selectedCardIds("character");
  const sceneIds = selectedCardIds("scene");

  const cardRefs = [];
  if (project) {
    for (const card of (project.character_cards || [])) {
      if (characterIds.includes(card.id) && card.image_url) {
        cardRefs.push({ url: card.image_url, label: `来自角色卡 ${card.name}` });
      }
    }
    for (const card of (project.scene_cards || [])) {
      if (sceneIds.includes(card.id) && card.image_url) {
        cardRefs.push({ url: card.image_url, label: `来自拍场景卡 ${card.name}`.replace("拍场景", "场景") });
      }
    }
  }

  const totalItems = cardRefs.length + files.length;
  if (!totalItems) {
    els.referenceImagesText.textContent = "上传参考图（可多选）";
    els.referencePreview.className = "ref-grid empty-state";
    els.referencePreview.textContent = "还没有上传参考图（选中带图片的卡片或上传单独参考图）";
    return;
  }

  els.referenceImagesText.textContent = `将发送 ${totalItems} 张参考图`;

  let html = "";
  for (const ref of cardRefs) {
    html += `
      <div class="ref-chip">
        <img class="ref-chip__thumb" src="${escapeHtml(Studio.asset(ref.url))}" alt="${escapeHtml(ref.label)}">
        <span class="ref-chip__label">${escapeHtml(ref.label)}</span>
      </div>
    `;
  }

  for (const file of files) {
    const objUrl = URL.createObjectURL(file);
    refObjectUrls.push(objUrl);
    html += `
      <div class="ref-chip">
        <img class="ref-chip__thumb" src="${objUrl}" alt="${escapeHtml(file.name)}">
        <span class="ref-chip__label">单独参考图</span>
        <span class="ref-chip__name">${escapeHtml(file.name)}</span>
      </div>
    `;
  }

  els.referencePreview.className = "ref-grid";
  els.referencePreview.innerHTML = html;
}

async function loadConfig() {
  const data = await Studio.cached("/photodream/api/config",600000);
  state.config = data;
  els.baseUrl.value = data.base_url || "https://api.hi-code.cc/v1";
  ImageSettings.init(data);
  els.model.value = data.default_model;
  els.size.value = data.default_size;
  ImageSettings.syncSize();
  els.configStatus.textContent = data.has_api_key ? `接口已就绪：${data.base_url}` : "未填写 API Key";
}

async function loadProjects() {
  const previousSelection = collectSelectionSnapshot();
  state.projects = await Studio.cached("/photodream/api/projects");

  if (!state.currentProjectId && state.projects.length) {
    const requested = initialProjectId;
    state.currentProjectId = state.projects.some(p => p.id === requested) ? requested : state.projects[0].id;
  }

  renderProjectOptions();
  renderProjectDetails();

  const project = currentProject();
  if (project) {
    els.projectName.value = project.name || "";
    renderHistory(project);

    if (project.shots && project.shots.length) {
      const latestShot = project.shots[0];
      renderResult(latestShot);
      applySelectedCards("character", latestShot.character_card_ids || []);
      applySelectedCards("scene", latestShot.scene_card_ids || []);
    } else {
      applySelectionSnapshot(previousSelection);
      selectFirstCardSetIfEmpty();
    }
  } else {
    els.projectName.value = "";
    renderHistory(null);
  }

  updateReferencePreview();
  setStatus("就绪 - 请创建角色卡或直接生图");
  if (active) Studio.projectSelected(state.currentProjectId);
}

async function ensureActiveProject() {
  if (state.currentProjectId) {
    const project = currentProject();
    if (project) {
      return project;
    }
    state.currentProjectId = "";
  }

  const name = els.projectName.value.trim();
  if (!name) {
    throw new Error("请先填写项目名称");
  }

  const formData = new FormData();
  formData.append("name", name);

  const response = await fetch("/photodream/api/projects", { method: "POST", body: formData });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "创建项目失败");
  }

  state.currentProjectId = data.id;
  rememberSingleDraft();
  singleDrafts.delete("new");
  try { localStorage.removeItem(singleDraftPrefix + "new"); } catch { /* Project draft is retained. */ }
  await loadProjects();
  return currentProject();
}

async function createCard(type) {
  let project;
  try {
    project = await ensureActiveProject();
  } catch (e) {
    setStatus(`失败：${e.message}（请先在下拉框选择项目，或填写新项目名称后重试）`);
    return;
  }

  const name = type === "character" ? els.characterName.value.trim() : els.sceneName.value.trim();
  const content = type === "character" ? els.characterContent.value.trim() : els.sceneContent.value.trim();

  if (!name || !content) {
    const missing = [];
    if (!name) missing.push("名称");
    if (!content) missing.push("设定内容");
    setStatus(`请填写${type === "character" ? "角色" : "场景"}卡的：${missing.join("、")}`);
    return;
  }

  const formData = new FormData();
  formData.append("card_type", type);
  formData.append("name", name);
  formData.append("content", content);

  const imageInput = type === "character" ? els.characterImage : els.sceneImage;
  if (imageInput.files && imageInput.files.length) {
    formData.append("image", imageInput.files[0]);
  }

  const response = await fetch(`/photodream/api/projects/${project.id}/cards`, { method: "POST", body: formData });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "创建卡片失败");
  }

  if (type === "character") {
    els.characterName.value = "";
    els.characterContent.value = "";
    els.characterImage.value = "";
    els.characterImageText.textContent = "选择参考图";
  } else {
    els.sceneName.value = "";
    els.sceneContent.value = "";
    els.sceneImage.value = "";
    els.sceneImageText.textContent = "选择参考图";
  }

  await loadProjects();
  state.currentProjectId = data.id;
  renderProjectDetails();
  updateReferencePreview();
  const cardLabel = type === "character" ? "角色卡" : "场景卡";
  setStatus(`${cardLabel}「${name}」已创建`);
}

async function submitForm(event) {
  event.preventDefault();
  setStatus("正在处理请求...");
  els.submitBtn.disabled = true;

  try {
    if (!state.currentProjectId && !els.projectName.value.trim()) els.projectName.value = "单图项目";
    await ensureActiveProject();
    rememberSingleDraft();
    await saveSingleDraft(state.currentProjectId);
    const formData = new FormData();
    formData.append("api_key", els.apiKey.value.trim());
    formData.append("base_url", els.baseUrl.value.trim());
    formData.append("project_id", state.currentProjectId);
    formData.append("project_name", els.projectName.value.trim());
    formData.append("prompt", els.prompt.value.trim());
    formData.append("system_prompt", els.systemPrompt.value.trim());
    formData.append("model", els.model.value.trim());
    formData.append("size", ImageSettings.normalizeSize(els.size.value));
    formData.append("character_card_ids", selectedCardIds("character").join(","));
    formData.append("scene_card_ids", selectedCardIds("scene").join(","));
    formData.append("continue_from_last", "false");

    const refFiles = Array.from(els.referenceImages.files || []);
    refFiles.forEach((file) => formData.append("reference_images", file));

    const response = await fetch("/photodream/api/generate", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "请求失败");
    }

    state.currentProjectId = data.project.id;
    els.projectName.value = data.project.name;
    renderResult(data.shot);
    await loadProjects();
    setStatus("生图完成");
  } catch (error) {
    setStatus(`失败：${error.message}`);
  } finally {
    els.submitBtn.disabled = false;
  }
}

els.addCharacterCardBtn.addEventListener("click", () => {
  createCard("character").catch((error) => setStatus(`失败：${error.message}`));
});

els.addSceneCardBtn.addEventListener("click", () => {
  createCard("scene").catch((error) => setStatus(`失败：${error.message}`));
});

els.projectSelect.addEventListener("change", (event) => {
  flushSingleDrafts();
  state.currentProjectId = event.target.value;
  resetProjectEditor();
  const project = currentProject();

  if (project) {
    els.projectName.value = project.name;
    renderProjectDetails();

    if (project.shots.length) {
      applySelectedCards("character", project.shots[0].character_card_ids || []);
      applySelectedCards("scene", project.shots[0].scene_card_ids || []);
      renderResult(project.shots[0]);
    } else {
      selectFirstCardSetIfEmpty();
    }

    renderHistory(project);
  } else {
    els.projectName.value = "";
    renderCardList(els.characterCardList, [], "character");
    renderCardList(els.sceneCardList, [], "scene");
    renderHistory(null);
  }

  updateReferencePreview();
  restoreSingleDraft();
  if (active) Studio.projectSelected(state.currentProjectId);
});

els.characterImage.addEventListener("change", () => {
  updateSingleFileText(els.characterImage, els.characterImageText, "选择参考图");
});

els.sceneImage.addEventListener("change", () => {
  updateSingleFileText(els.sceneImage, els.sceneImageText, "选择参考图");
});

els.referenceImages.addEventListener("change", updateReferencePreview);

els.submitBtn.addEventListener("click", (event) => {
  event.preventDefault();
  submitForm(event).catch((error) => setStatus(`失败：${error.message}`));
});

root.addEventListener("change", (event) => {
  if (event.target.matches('[data-card-type="character"], [data-card-type="scene"]')) {
    updateReferencePreview();
    rememberSingleDraft();
  }
});

updateSingleFileText(els.characterImage, els.characterImageText, "选择参考图");
updateSingleFileText(els.sceneImage, els.sceneImageText, "选择参考图");
updateReferencePreview();

for (const element of [els.prompt, els.systemPrompt, els.model, els.size, els.projectName, els.characterName, els.characterContent, els.sceneName, els.sceneContent]) element.addEventListener("input", rememberSingleDraft);
listen(window,"pagehide", flushSingleDrafts);
listen(window,"online", flushSingleDrafts);
listen(document,"visibilitychange", () => { if (document.hidden) flushSingleDrafts(); });
for (const container of [els.characterCardList, els.sceneCardList]) container.addEventListener("click", async event => {
  const button = event.target.closest("[data-card-action]"); if (!button || !state.currentProjectId) return;
  const id = state.currentProjectId, selection = collectSelectionSnapshot(); button.disabled = true;
  try {
    const project = await Studio.removeCard(id, button.dataset.cardKind, button.dataset.cardId, button.dataset.cardAction === "image");
    if (!project || state.currentProjectId !== id) return;
    const index = state.projects.findIndex(p => p.id === id); state.projects[index] = project;
    renderProjectDetails(); applySelectionSnapshot(selection); updateReferencePreview(); rememberSingleDraft();
    setStatus("参考卡已更新，历史图片和日志已保留。");
  } catch (error) { setStatus(error.message); }
  finally { button.disabled = false; }
});
singleDraftPrefix += Studio.user.id + ":";
const ready = Promise.all([loadConfig(),loadProjects()]).then(restoreSingleDraft).catch(error => { setStatus(`读取失败：${error.message}`); throw error; });
async function synchronizeProject() {
  const id = state.currentProjectId; if (!id) return;
  try {
    const project = await Studio.api(`/photodream/api/projects/${id}`);
    if (!active || state.currentProjectId !== id) return;
    const previous = currentProject(), selection = collectSelectionSnapshot();
    const changed = JSON.stringify([previous?.character_cards,previous?.scene_cards]) !== JSON.stringify([project.character_cards,project.scene_cards]);
    const index = state.projects.findIndex(p => p.id === id); if (index !== -1) state.projects[index] = project;
    if (changed) { renderProjectDetails(); applySelectionSnapshot(selection); updateReferencePreview(); }
    renderHistory(project);
    const displayed = els.resultImage.getAttribute("src");
    if (displayed && !project.shots.some(shot => Studio.asset(shot.image_url) === displayed)) {
      els.resultImage.removeAttribute("src");
      els.resultCard.classList.add("is-hidden"); els.resultEmpty.classList.remove("is-hidden");
    }
  } catch (error) { if (active && state.currentProjectId === id) setStatus(error.message); }
}
return {
  ready,
  async activate(projectId) {
    active = true;
    if (projectId && projectId !== state.currentProjectId) {
      flushSingleDrafts(); state.currentProjectId = projectId;
      resetProjectEditor();
      await loadProjects(); restoreSingleDraft();
    }
    if (active) Studio.projectSelected(state.currentProjectId);
    synchronizeProject();
  },
  deactivate() { active = false; flushSingleDrafts(); },
  dispose() { active = false; lifetime.abort(); for (const timer of singleTimers.values()) clearTimeout(timer); },
};
}
