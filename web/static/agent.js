"use strict";

window.PhotoDreamAgent = (() => {
  let mounted = false;
  const characterAsset = "/photodream/static/assets/xiaomeng-character.png";
  const characterSheet = "/photodream/static/assets/xiaomeng.png";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  function operationPreview(session) {
    if (!session?.job?.pending_confirmation || session.decision?.action !== "tool") return "";
    const decision = session.decision;
    const names = {create_project:"创建项目", update_project:"修改项目", save_script:"保存剧本", upsert_card:"保存参考卡", delete_card:"删除参考卡", create_frame:"创建镜头", create_frames:"批量创建镜头", update_frame:"修改镜头", delete_frame:"删除镜头", apply_cards:"应用参考卡", generate_frames:"生成镜头", set_card_image:"设置参考图", remove_frame_image:"移除镜头图片", remove_card_image:"移除参考图", import_resources:"导入参考卡", restore_frame_image:"恢复历史图片", purge_image:"彻底删除图片"};
    const labels = {name:"名称", title:"标题", synopsis:"简介", screenplay_text:"剧本", content:"设定", prompt:"画面提示词", system_prompt:"系统提示词", model:"模型", size:"尺寸", frame_id:"镜头", frame_ids:"镜头", card_id:"参考卡", character_card_ids:"角色卡", scene_card_ids:"场景卡", image_url:"图片", frames:"分镜", archived:"归档", card_type:"参考卡类型", source_project_id:"来源项目", card_ids:"参考卡", record_id:"历史记录", marked:"标记"};
    const fields = Object.entries(decision.arguments || {}).map(([key,value]) => `<p><b>${esc(labels[key] || key)}</b> ${esc(typeof value === "object" ? JSON.stringify(value) : value)}</p>`).join("");
    return `<section class="agent-operation"><strong>待确认：${esc(names[decision.tool_name] || "项目操作")}</strong>${fields}</section>`;
  }
  function mountCharacterScene(container) {
    if (!container || container.__xiaomengIllustration) return;
    container.classList.add("xiaomeng-illustration");
    const image = document.createElement("img");
    image.src = characterAsset;
    image.alt = "小梦 Agent 角色立绘";
    image.loading = "eager";
    container.replaceChildren(image);
    container.__xiaomengIllustration = {image};
  }

  function mountCharacterSlots(root = document) {
    root.querySelectorAll("[data-xiaomeng-illustration]").forEach(mountCharacterScene);
  }

  function sessionControls(container, prefix, Studio, options) {
    const controls = document.createElement("div");
    controls.className = "agent-session-controls";
    controls.innerHTML = `<select id="${prefix}Session" aria-label="选择会话"></select><button id="${prefix}New" type="button">新建会话</button><button id="${prefix}Delete" type="button">删除会话</button>`;
    container.prepend(controls);
    const select = controls.querySelector("select");
    const buttons = [...controls.querySelectorAll("button")];
    let active, pending = false;
    async function load(preferred) {
      const rows = await Studio.api(`/photodream/api/agent/sessions${options.query()}`);
      let row = rows.find(item => item.id === preferred) || rows[0];
      if (!row) row = await Studio.post("/photodream/api/agent/sessions", options.body());
      if (!rows.some(item => item.id === row.id)) rows.unshift(row);
      select.innerHTML = rows.map(item => `<option value="${esc(item.id)}">${esc(item.title || "新会话")}</option>`).join("");
      const full = await Studio.api(`/photodream/api/agent/sessions/${row.id}`);
      active = full; select.value = full.id;
      options.activate(full);
    }
    async function action(callback) {
      if (pending || options.loading()) return;
      pending = true; select.disabled = true; buttons.forEach(button => button.disabled = true);
      options.lock(true);
      try { await options.before?.(); await callback(); }
      catch (error) { if (active) select.value = active.id; Studio.showError(error); }
      finally { pending = false; options.lock(false); update(options.current()); }
    }
    select.onchange = () => { const id = select.value; action(() => load(id)); };
    buttons[0].onclick = () => action(async () => {
      const row = await Studio.post("/photodream/api/agent/sessions", options.body());
      await load(row.id);
    });
    buttons[1].onclick = () => {
      if (!active || !confirm("删除这个会话及聊天记录？项目、资源和已生成图片会保留。")) return;
      action(async () => { await Studio.api(`/photodream/api/agent/sessions/${active.id}`, {method:"DELETE"}); await load(); });
    };
    function update(row) {
      active = row;
      select.disabled = pending || options.loading();
      buttons[0].disabled = pending || options.loading();
      buttons[1].disabled = pending || options.loading() || !row || ["queued", "running"].includes(row.job?.status);
      const option = [...select.options].find(item => item.value === row?.id);
      if (option) option.textContent = row.title || "新会话";
    }
    return {load: preferred => action(() => load(preferred)), update};
  }

  function mount(Studio) {
    if (mounted) return;
    mounted = true;
    const launcher = document.createElement("button");
    launcher.id = "xiaomengLauncher";
    launcher.className = "xiaomeng-launcher xiaomeng-model-launcher";
    launcher.type = "button";
    launcher.innerHTML = `<span class="xiaomeng-illustration-slot" data-xiaomeng-illustration="launcher" aria-label="小梦 Agent 角色立绘"></span><span class="xiaomeng-model-label">小梦 Agent</span>`;
    launcher.title = "打开小梦 Agent";
    document.body.append(launcher);
    mountCharacterSlots(launcher);

    const dialog = document.createElement("dialog");
    dialog.id = "xiaomengDialog";
    dialog.className = "xiaomeng-dialog";
    dialog.innerHTML = `<div class="xiaomeng-head"><div class="xiaomeng-identity"><div class="xiaomeng-portrait xiaomeng-illustration-slot" data-xiaomeng-illustration="portrait" role="img" aria-label="小梦角色立绘"></div><div><span class="eyebrow">PHOTO DREAM AGENT</span><h2>小梦</h2><p>把想法整理成可以直接制作的项目。</p><a class="xiaomeng-sheet-link" href="${characterSheet}" target="_blank" rel="noopener">查看完整角色设定图</a></div></div><button type="button" class="xiaomeng-close" aria-label="关闭">×</button></div><div class="xiaomeng-toolbar"><select id="xiaomengMode" aria-label="工作模式"><option value="guided">协作模式 · 每阶段确认</option><option value="autonomous">完全托管 · 自动推进规划</option></select><span id="xiaomengStage" class="tag">准备中</span></div><div id="xiaomengStages" class="xiaomeng-stages"></div><div id="xiaomengMessages" class="xiaomeng-messages"></div><div id="xiaomengPlan" class="xiaomeng-plan"></div><form id="xiaomengForm" class="xiaomeng-form"><textarea id="xiaomengInput" rows="3" maxlength="20000" placeholder="告诉小梦你的想法，例如：我想做一个雨夜里寻找旧相机的短片…" required></textarea><div class="xiaomeng-actions"><button id="xiaomengConfirm" type="button">确认当前阶段</button><button id="xiaomengRun" type="button" class="primary">开始托管</button><button type="submit" class="primary">发送</button></div></form>`;
    document.body.append(dialog);
    mountCharacterSlots(dialog);
    let session = null, loading = false, pollTimer, loadedProject;
    const $ = id => dialog.querySelector(`#${id}`);
    const projectId = () => new URL(location.href).searchParams.get("project") || Studio.projectId || "";
    const conversations = sessionControls(dialog.querySelector(".xiaomeng-toolbar"), "xiaomeng", Studio, {
      query: () => projectId() ? `?project_id=${encodeURIComponent(projectId())}` : "",
      body: () => ({project_id:projectId(), mode:$("xiaomengMode").value}),
      loading: () => loading, current: () => session,
      lock: value => { loading = value; render(); },
      activate: row => { clearTimeout(pollTimer); session = row; $("xiaomengInput").value = ""; render(); if (["queued", "running"].includes(row.job?.status)) refresh(); },
    });
    function stageMarkup() {
      return (session?.stages || []).map(stage => `<span class="xiaomeng-stage ${stage.active ? "active" : ""}">${esc(stage.label)}</span>`).join("");
    }
    function render() {
      if (!session) return;
      conversations.update(session);
      $("xiaomengStage").textContent = session.stage_label;
      $("xiaomengStages").innerHTML = stageMarkup();
      $("xiaomengMode").value = session.mode;
      $("xiaomengMessages").innerHTML = session.messages.map(item => `<div class="xiaomeng-message ${item.role === "user" ? "user" : "assistant"}"><span>${item.role === "user" ? "你" : "小梦"}</span><p>${esc(item.content).replace(/\n/g, "<br>")}</p></div>`).join("") || '<div class="xiaomeng-empty">先告诉我你想拍什么。</div>';
      const plan = session.plan || {};
      $("xiaomengPlan").innerHTML = `<div class="xiaomeng-plan-head"><strong>当前规划</strong><span>${esc(plan.title || "等待想法")}</span></div><p>${esc(plan.brief || "对话后会生成创作目标")}</p><div class="xiaomeng-plan-grid"><span>人物 ${plan.characters?.length || 0}</span><span>场景 ${plan.scenes?.length || 0}</span><span>分镜 ${plan.shots?.length || 0}</span></div>`;
      $("xiaomengPlan").insertAdjacentHTML("afterbegin", operationPreview(session));
      if (session.job?.status === "failed") $("xiaomengPlan").insertAdjacentHTML("afterbegin", `<p role="status">${esc(session.job.message)}<small> ${esc(session.job.error_code || "")} · ${esc(session.job.request_id || "")}</small></p>`);
      const busy = loading || session.job?.status === "queued" || session.job?.status === "running";
      $("xiaomengRun").disabled = busy;
      $("xiaomengRun").textContent = busy ? `${session.job.message || "托管中"} ${session.job.progress || 0}%` : "开始托管";
      $("xiaomengConfirm").disabled = busy || !session.job?.pending_confirmation;
      $("xiaomengConfirm").textContent = session.decision?.action === "generate" ? "确认生成所选镜头" : "确认本次操作";
      $("xiaomengForm button[type=submit]").disabled = busy;
      $("xiaomengForm button[type=submit]").textContent = busy ? "处理中…" : "发送";
      $("xiaomengMode").disabled = busy;
      const box = $("xiaomengMessages"); box.scrollTop = box.scrollHeight;
    }
    async function loadSession() {
      await conversations.load();
      loadedProject = projectId();
    }
    async function open() {
      dialog.showModal();
      if (!session || loadedProject !== projectId()) {
        try { await loadSession(); } catch (error) { Studio.showError(error); }
      }
      $("xiaomengInput").focus();
    }
    async function refresh() {
      if (!session) return;
      clearTimeout(pollTimer);
      const id = session.id;
      try {
        const row = await Studio.api(`/photodream/api/agent/sessions/${id}`);
        if (session?.id !== id) return;
        session = row; render();
        if (session.job?.status === "queued" || session.job?.status === "running") pollTimer = setTimeout(refresh, 1200);
      } catch (error) { Studio.showError(error); }
    }
    launcher.onclick = open;
    $("xiaomengMode").options[0].textContent = "协作模式 · 操作前确认";
    $("xiaomengMode").options[1].textContent = "托管模式 · 点击开始后执行";
    dialog.querySelector(".xiaomeng-close").onclick = () => dialog.close();
    $("xiaomengMode").onchange = async () => {
      if (!session || loading) return;
      const mode = $("xiaomengMode").value;
      loading = true; render();
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/mode`, {mode}); render(); }
      catch (error) { Studio.showError(error); }
      finally { loading = false; render(); }
    };
    $("xiaomengForm").onsubmit = async event => {
      event.preventDefault(); if (loading || !session) return;
      const content = $("xiaomengInput").value.trim(); if (!content) return;
      loading = true; $("xiaomengInput").disabled = true; render();
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/messages`, {content, background:true}); $("xiaomengInput").value = ""; }
      catch (error) { Studio.showError(error); }
      finally { loading = false; $("xiaomengInput").disabled = false; render(); $("xiaomengInput").focus(); if (["queued", "running"].includes(session?.job?.status)) refresh(); }
    };
    $("xiaomengConfirm").onclick = async () => {
      if (!session || loading) return;
      loading = true; render();
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/confirm?background=true`, {}); render(); refresh(); }
      catch (error) { Studio.showError(error); }
      finally { loading = false; render(); }
    };
    $("xiaomengRun").onclick = async () => {
      if (!session || loading) return;
      loading = true; render();
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/run`, {}); render(); refresh(); }
      catch (error) { Studio.showError(error); }
      finally { loading = false; render(); }
    };
  }
  async function mountPage(root, Studio) {
    let session, projects = [], loading = false, selectedProject = new URL(location.href).searchParams.get("project") || "";
    root.innerHTML = `<div class="planning-page"><header class="page-head"><div><div class="eyebrow">PHOTO DREAM / CREATIVE PLANNING</div><div class="planning-title-row"><div class="planning-agent-avatar xiaomeng-illustration-slot" data-xiaomeng-illustration="planning" aria-label="小梦角色立绘"></div><div><h1>创作规划</h1><p>和小梦一起完成文案、剧本、人物、场景和分镜准备，再交给分镜工作台生图。</p></div></div></div><div class="planning-head-actions"><select id="planningProject" aria-label="当前项目"></select><button id="planningNew" type="button">新建会话</button></div></header><div class="planning-layout"><aside class="planning-rail"><h3>创作阶段</h3><div id="planningStages"></div><div class="planning-mode"><label>工作方式<select id="planningMode"><option value="guided">协作模式</option><option value="autonomous">完全托管</option></select></label><button id="planningRun" class="primary" type="button">开始托管</button></div></aside><section class="planning-chat c-panel"><div id="planningMessages" class="planning-messages"></div><form id="planningForm"><textarea id="planningInput" rows="4" maxlength="20000" required placeholder="描述你的故事、用途、人物或画面想法…"></textarea><div class="planning-chat-actions"><span class="muted">每个阶段确认后才会写入项目。</span><button class="primary" type="submit">发送给小梦</button></div></form></section><section class="planning-editor c-panel"><div class="page-head"><div><h2>规划结果</h2><p>可以直接编辑，保存后再确认。</p></div><button id="planningSave" type="button">保存规划</button></div><div id="planningFields"></div><div class="planning-editor-actions"><button id="planningConfirm" class="primary" type="button">确认当前阶段并写入项目</button></div></section></div></div>`;
    mountCharacterSlots(root);
    const $ = id => root.querySelector(`#${id}`);
    let pollTimer, editorDirty = false;
    $("planningNew").remove();
    const conversations = sessionControls(root.querySelector(".planning-head-actions"), "planning", Studio, {
      query: () => selectedProject ? `?project_id=${encodeURIComponent(selectedProject)}` : "",
      body: () => ({project_id:selectedProject, mode:$("planningMode").value}),
      loading: () => loading, current: () => session,
      lock: value => { loading = value; render(); },
      before: async () => { if (editorDirty) await savePlan(); },
      activate: row => { clearTimeout(pollTimer); session = row; editorDirty = false; $("planningInput").value = ""; render(); if (["queued", "running"].includes(row.job?.status)) poll(); },
    });
    $("planningFields").addEventListener("input", () => { editorDirty = true; });
    const getProjectId = () => selectedProject;
    function renderStages() {
      $("planningStages").innerHTML = (session?.stages || []).map(item => `<div class="planning-stage ${item.active ? "active" : ""}"><span>${esc(item.label)}</span>${item.active ? "<small>当前</small>" : ""}</div>`).join("");
    }
    function field(label, name, value, rows = 3) { return `<label>${label}<textarea data-plan-field="${name}" rows="${rows}">${esc(value)}</textarea></label>`; }
    function renderEditor() {
      if (editorDirty) return;
      const plan = session?.plan || {};
      const cardFields = (items, kind, labels) => `<div class="planning-list"><div class="planning-list-head"><h3>${labels}</h3><button type="button" data-plan-add="${kind}">＋ 添加</button></div>${(items || []).map((item, index) => `<div class="planning-item" data-plan-item="${kind}" data-index="${index}"><input data-plan-name value="${esc(item.name || item.title || "")}" placeholder="名称"><textarea data-plan-content rows="2" placeholder="设定或画面描述">${esc(item.content || item.prompt || "")}</textarea><button type="button" data-plan-remove>删除</button></div>`).join("")}</div>`;
      $("planningFields").innerHTML = field("项目名称", "title", plan.title, 1) + field("创作目标 / 文案", "brief", plan.brief, 4) + field("视觉基调", "tone", plan.tone, 2) + field("剧本", "screenplay", plan.screenplay, 9) + cardFields(plan.characters, "characters", "人物设定") + cardFields(plan.scenes, "scenes", "场景设定") + cardFields(plan.shots, "shots", "分镜草案");
    }
    function collectPlan() {
      const plan = {...session.plan};
      for (const node of root.querySelectorAll("[data-plan-field]")) plan[node.dataset.planField] = node.value;
      for (const [kind, target] of [["characters", "characters"], ["scenes", "scenes"], ["shots", "shots"]]) {
        plan[target] = [...root.querySelectorAll(`[data-plan-item="${kind}"]`)].map((item, index) => ({
          ...(kind === "shots" ? {order:index + 1} : {}),
          [kind === "shots" ? "title" : "name"]: item.querySelector("[data-plan-name]").value.trim(),
          [kind === "shots" ? "prompt" : "content"]: item.querySelector("[data-plan-content]").value.trim(),
        })).filter(item => Object.values(item).some(value => value));
      }
      return plan;
    }
    function render() {
      if (!session) return;
      conversations.update(session);
      $("planningMode").value = session.mode; renderStages(); renderEditor();
      $("planningFields").querySelector(".agent-operation")?.remove();
      $("planningFields").insertAdjacentHTML("afterbegin", operationPreview(session));
      $("planningMessages").innerHTML = session.messages.map(item => `<article class="planning-message ${item.role}"><small>${item.role === "user" ? "你" : "小梦"}</small><div>${esc(item.content).replace(/\n/g, "<br>")}</div></article>`).join("") || '<div class="planning-empty">在这里告诉小梦你的创作想法。</div>';
      const box = $("planningMessages"); box.scrollTop = box.scrollHeight;
      const busy = loading || ["queued", "running"].includes(session.job?.status);
      $("planningRun").disabled = busy; $("planningConfirm").disabled = busy || !session.job?.pending_confirmation;
      $("planningForm button[type=submit]").disabled = busy;
      $("planningForm button[type=submit]").textContent = busy ? "小梦正在处理…" : "发送给小梦";
      for (const id of ["planningSave", "planningMode", "planningProject"]) $(id).disabled = busy;
      $("planningFields").querySelectorAll("input,textarea,button").forEach(node => { node.disabled = busy; });
      $("planningRun").textContent = busy ? `${session.job.message || "托管中"} ${session.job.progress || 0}%` : "开始托管";
    }
    async function load() {
      projects = await Studio.api("/photodream/api/projects");
      const select = $("planningProject");
      select.innerHTML = '<option value="">全部会话 / 新项目</option>' + projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
      select.value = getProjectId(); selectedProject = select.value;
      await conversations.load();
    }
    async function savePlan() { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/plan`, collectPlan()); editorDirty = false; render(); }
    $("planningProject").onchange = async event => {
      try {
        if (editorDirty) await savePlan();
        selectedProject = event.target.value;
        const url = new URL(location.href); if (selectedProject) url.searchParams.set("project", selectedProject); else url.searchParams.delete("project");
        history.replaceState(null, "", url); await load();
      } catch (error) { Studio.showError(error); }
    };
    async function poll() {
      clearTimeout(pollTimer);
      const id = session.id;
      try {
        const row = await Studio.api(`/photodream/api/agent/sessions/${id}`);
        if (!root.isConnected || session?.id !== id) return;
        session = row; render();
        if (["queued", "running"].includes(session.job?.status)) pollTimer = setTimeout(poll, 1200);
      } catch (error) { Studio.showError(error); }
    }
    $("planningForm").onsubmit = async event => {
      event.preventDefault(); if (loading || !session) return;
      const content = $("planningInput").value.trim(); if (!content) return;
      loading = true; render();
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/messages`, {content, background:true}); $("planningInput").value = ""; }
      catch (error) { Studio.showError(error); }
      finally { loading = false; render(); if (["queued", "running"].includes(session.job?.status)) poll(); }
    };
    async function perform(callback) {
      if (!session || loading) return;
      loading = true; render();
      try { await callback(); }
      catch (error) { Studio.showError(error); }
      finally { loading = false; render(); }
    }
    $("planningSave").onclick = () => perform(savePlan);
    $("planningConfirm").onclick = () => perform(async () => { await savePlan(); session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/confirm?background=true`, {}); poll(); });
    $("planningRun").onclick = () => perform(async () => { await savePlan(); session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/run`, {}); poll(); });
    $("planningMode").onchange = () => {
      const mode = $("planningMode").value;
      return perform(async () => { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/mode`, {mode}); });
    };
    root.addEventListener("click", event => { const add = event.target.closest("[data-plan-add]"); if (add) { session.plan = collectPlan(); const kind = add.dataset.planAdd, list = session.plan[kind] || []; list.push(kind === "shots" ? {title:"", prompt:""} : {name:"", content:""}); session.plan[kind] = list; editorDirty = false; renderEditor(); editorDirty = true; return; } const remove = event.target.closest("[data-plan-remove]"); if (remove) { remove.closest("[data-plan-item]").remove(); editorDirty = true; } });
    await load();
    if (["queued", "running"].includes(session.job?.status)) poll();
    $("planningConfirm").textContent = "确认本次操作";
    root.querySelector(".planning-chat-actions .muted").textContent = "先讨论剧本与想法，操作项目之前可检查具体内容。";
    $("planningStages").previousElementSibling.textContent = "创作主题 · 自由切换";
  }
  return {mount, mountPage};
})();
