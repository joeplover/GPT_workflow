const state = {
  config: null,
  projects: [],
  currentProjectId: "",
};

const els = {
  submitBtn: document.getElementById("submitBtn"),
  submitStatus: document.getElementById("submitStatus"),
  configStatus: document.getElementById("configStatus"),
  apiKey: document.getElementById("apiKey"),
  baseUrl: document.getElementById("baseUrl"),
  projectSelect: document.getElementById("projectSelect"),
  projectName: document.getElementById("projectName"),
  characterName: document.getElementById("characterName"),
  characterContent: document.getElementById("characterContent"),
  characterImage: document.getElementById("characterImage"),
  characterImageText: document.getElementById("characterImageText"),
  sceneName: document.getElementById("sceneName"),
  sceneContent: document.getElementById("sceneContent"),
  sceneImage: document.getElementById("sceneImage"),
  sceneImageText: document.getElementById("sceneImageText"),
  addCharacterCardBtn: document.getElementById("addCharacterCardBtn"),
  addSceneCardBtn: document.getElementById("addSceneCardBtn"),
  characterCardList: document.getElementById("characterCardList"),
  sceneCardList: document.getElementById("sceneCardList"),
  prompt: document.getElementById("prompt"),
  referenceImages: document.getElementById("referenceImages"),
  referenceImagesText: document.getElementById("referenceImagesText"),
  referencePreview: document.getElementById("referencePreview"),
  model: document.getElementById("model"),
  size: document.getElementById("size"),
  resultEmpty: document.getElementById("resultEmpty"),
  resultCard: document.getElementById("resultCard"),
  resultImage: document.getElementById("resultImage"),
  resultMeta: document.getElementById("resultMeta"),
  resultPrompt: document.getElementById("resultPrompt"),
  resultComposedPrompt: document.getElementById("resultComposedPrompt"),
  resultLink: document.getElementById("resultLink"),
  historyList: document.getElementById("historyList"),
};

function setStatus(text) {
  els.submitStatus.textContent = text;
}

function currentProject() {
  return state.projects.find((item) => item.id === state.currentProjectId) || null;
}

function selectedCardIds(type) {
  const selector = type === "character" ? '[data-card-type="character"]:checked' : '[data-card-type="scene"]:checked';
  return Array.from(document.querySelectorAll(selector)).map((el) => el.value);
}

function applySelectedCards(type, ids) {
  const selector = type === "character" ? '[data-card-type="character"]' : '[data-card-type="scene"]';
  const selected = new Set(ids || []);
  document.querySelectorAll(selector).forEach((el) => {
    el.checked = selected.has(el.value);
  });
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
      ? `<img class="card-chip__thumb" src="${escapeHtml(card.image_url)}" alt="${escapeHtml(card.name)}">`
      : "";
    return `
    <label class="card-chip">
      <input type="checkbox" data-card-type="${type}" value="${escapeHtml(card.id)}" checked>
      ${thumbHtml}
      <span>
        <strong>${escapeHtml(card.name)}</strong>
        <small>${escapeHtml(card.content)}</small>
      </span>
    </label>
  `;
  }).join("");
}

function renderProjectDetails() {
  const project = currentProject();
  if (!project) {
    renderCardList(els.characterCardList, [], "character");
    renderCardList(els.sceneCardList, [], "scene");
    return;
  }
  renderCardList(els.characterCardList, project.character_cards || [], "character");
  renderCardList(els.sceneCardList, project.scene_cards || [], "scene");
}

function refillFromShot(shot) {
  if (!shot) {
    return;
  }
  els.prompt.value = shot.prompt || "";
  if (shot.model) {
    els.model.value = shot.model;
  }
  if (shot.size) {
    els.size.value = shot.size;
  }
  applySelectedCards("character", shot.character_card_ids || []);
  applySelectedCards("scene", shot.scene_card_ids || []);
  updateReferencePreview();
  setStatus(`已从第 ${shot.shot_number} 镜回填参数`);
}

async function downloadShot(shot) {
  try {
    const resp = await fetch(shot.image_url);
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
  els.resultImage.src = shot.image_url;
  els.resultLink.href = shot.image_url;
  els.resultMeta.textContent = `第 ${shot.shot_number} 镜 / ${shot.mode} / ${shot.model} / ${shot.size}`;
  els.resultPrompt.textContent = `原始提示词：${shot.prompt || ""}`;
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
      <img src="${escapeHtml(shot.image_url)}" alt="历史镜头">
      <div>
        <strong>第 ${shot.shot_number} 镜</strong>
        <p>${escapeHtml(shot.mode)} / ${escapeHtml(shot.model)} / ${escapeHtml(shot.size)}</p>
        <p>角色卡：${(shot.character_card_ids || []).length} / 场景卡：${(shot.scene_card_ids || []).length}</p>
        <p>${escapeHtml(shot.created_at)}</p>
        <div class="history-actions">
          <button class="btn btn--small history-action" type="button" data-action="refill" data-shot-id="${escapeHtml(shot.id)}">回填</button>
          <button class="btn btn--small history-action" type="button" data-action="retry" data-shot-id="${escapeHtml(shot.id)}">重试</button>
          <button class="btn btn--small history-action" type="button" data-action="download" data-shot-id="${escapeHtml(shot.id)}">下载</button>
        </div>
      </div>
    </article>
  `).join("");

  document.querySelectorAll(".history-action").forEach((button) => {
    button.addEventListener("click", () => {
      const targetShot = shots.find((item) => item.id === button.dataset.shotId);
      if (!targetShot) {
        return;
      }
      if (button.dataset.action === "download") {
        downloadShot(targetShot);
        return;
      }
      refillFromShot(targetShot);
      if (button.dataset.action === "retry") {
        els.form.requestSubmit();
      }
    });
  });
}

function updateSingleFileText(inputEl, textEl, emptyText) {
  if (!inputEl.files || !inputEl.files.length) {
    textEl.textContent = emptyText;
    return;
  }
  textEl.textContent = inputEl.files[0].name;
}

let _refObjectUrls = [];

function updateReferencePreview() {
  _refObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  _refObjectUrls = [];

  const project = currentProject();
  const files = Array.from(els.referenceImages.files || []);
  const characterIds = selectedCardIds("character");
  const sceneIds = selectedCardIds("scene");

  const cardRefs = [];
  if (project) {
    for (const card of (project.character_cards || [])) {
      if (characterIds.includes(card.id) && card.image_url) {
        cardRefs.push({ url: card.image_url, label: `来自角色卡: ${card.name}`, isCard: true });
      }
    }
    for (const card of (project.scene_cards || [])) {
      if (sceneIds.includes(card.id) && card.image_url) {
        cardRefs.push({ url: card.image_url, label: `来自场景卡: ${card.name}`, isCard: true });
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
        <img class="ref-chip__thumb" src="${escapeHtml(ref.url)}" alt="${escapeHtml(ref.label)}">
        <span class="ref-chip__label">${escapeHtml(ref.label)}</span>
      </div>
    `;
  }

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const objUrl = URL.createObjectURL(file);
    _refObjectUrls.push(objUrl);
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
  const response = await fetch("/api/config");
  const data = await response.json();
  state.config = data;
  els.apiKey.value = data.api_key || "";
  els.baseUrl.value = data.base_url || "https://api.hi-code.cc/v1";
  fillSelect(els.model, data.suggested_models || ["gpt-image-1"], data.default_model);
  fillSelect(els.size, data.suggested_sizes || ["1536x1024"], data.default_size);
  els.configStatus.textContent = data.has_api_key ? `接口已就绪：${data.base_url}` : "未填写 API Key";
}

async function loadProjects() {
  const response = await fetch("/api/projects");
  state.projects = await response.json();
  if (!state.currentProjectId && state.projects.length) {
    state.currentProjectId = state.projects[0].id;
  }
  renderProjectOptions();
  renderProjectDetails();
  const project = currentProject();
  if (project) {
    els.projectName.value = project.name || "";
    renderHistory(project);
    if (project.shots && project.shots.length) {
      renderResult(project.shots[0]);
    }
  }
  setStatus("就绪 — 请创建角色卡或直接生图");
}

async function ensureActiveProject() {
  if (state.currentProjectId) {
    return currentProject();
  }

  const name = els.projectName.value.trim();
  if (!name) {
    throw new Error("请先填写项目名称");
  }

  const formData = new FormData();
  formData.append("name", name);
  formData.append("synopsis", "");
  const response = await fetch("/api/projects", { method: "POST", body: formData });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "创建项目失败");
  }

  state.currentProjectId = data.id;
  await loadProjects();
  return currentProject();
}

async function createCard(type) {
  let project;
  try {
    project = await ensureActiveProject();
  } catch (e) {
    setStatus(`失败：${e.message}（请先在下拉框选择项目，或填写"新项目名称"后重试）`);
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
  const response = await fetch(`/api/projects/${project.id}/cards`, { method: "POST", body: formData });
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
  const cardLabel = type === "character" ? "角色卡" : "场景卡";
  setStatus(`${cardLabel}「${name}」已创建 ✓`);
}

async function submitForm(event) {
  event.preventDefault();
  setStatus("正在处理请求...");
  els.submitBtn.disabled = true;

  try {
    const formData = new FormData();
    formData.append("api_key", els.apiKey.value.trim());
    formData.append("base_url", els.baseUrl.value.trim());
    formData.append("project_id", state.currentProjectId);
    formData.append("project_name", els.projectName.value.trim());
    formData.append("synopsis", "");
    formData.append("prompt", els.prompt.value.trim());
    formData.append("model", els.model.value.trim());
    formData.append("size", els.size.value);
    formData.append("character_card_ids", selectedCardIds("character").join(","));
    formData.append("scene_card_ids", selectedCardIds("scene").join(","));
    formData.append("continue_from_last", "false");

    const refFiles = Array.from(els.referenceImages.files || []);
    const cardCheckCount = selectedCardIds("character").length + selectedCardIds("scene").length;
    console.log(`[submitForm] 卡片选中: ${cardCheckCount}, 参考图文件: ${refFiles.length}`);
    refFiles.forEach((file) => {
      formData.append("reference_images", file);
    });

    if (!cardCheckCount && !refFiles.length) {
      console.warn("[submitForm] 警告: 未选中任何卡片，也未上传参考图，将使用纯文生图模式");
    }

    const response = await fetch("/api/generate", { method: "POST", body: formData });
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
  state.currentProjectId = event.target.value;
  const project = currentProject();
  if (project) {
    els.projectName.value = project.name;
    renderProjectDetails();
    renderHistory(project);
    if (project.shots.length) {
      renderResult(project.shots[0]);
    }
  } else {
    els.projectName.value = "";
    renderCardList(els.characterCardList, [], "character");
    renderCardList(els.sceneCardList, [], "scene");
    renderHistory(null);
  }
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

document.addEventListener("change", (event) => {
  if (event.target.matches('[data-card-type="character"], [data-card-type="scene"]')) {
    updateReferencePreview();
  }
});

updateSingleFileText(els.characterImage, els.characterImageText, "选择参考图");
updateSingleFileText(els.sceneImage, els.sceneImageText, "选择参考图");
updateReferencePreview();

loadConfig().catch((error) => {
  els.configStatus.textContent = `配置读取失败：${error.message}`;
});
loadProjects().catch((error) => {
  setStatus(`项目读取失败：${error.message}`);
});
