"use strict";

window.PhotoDreamAgent = (() => {
  let mounted = false;
  const characterAsset = "/photodream/static/assets/xiaomeng-character.png";
  const characterSheet = "/photodream/static/assets/xiaomeng.png";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
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
    let session = null, loading = false, pollTimer;
    const $ = id => dialog.querySelector(`#${id}`);
    const projectId = () => new URL(location.href).searchParams.get("project") || Studio.projectId || "";
    const currentStage = () => session?.stage || "brief";
    function stageMarkup() {
      return (session?.stages || []).map(stage => `<span class="xiaomeng-stage ${stage.active ? "active" : ""}">${esc(stage.label)}</span>`).join("");
    }
    function render() {
      if (!session) return;
      $("xiaomengStage").textContent = session.stage_label;
      $("xiaomengStages").innerHTML = stageMarkup();
      $("xiaomengMode").value = session.mode;
      $("xiaomengMessages").innerHTML = session.messages.map(item => `<div class="xiaomeng-message ${item.role === "user" ? "user" : "assistant"}"><span>${item.role === "user" ? "你" : "小梦"}</span><p>${esc(item.content).replace(/\n/g, "<br>")}</p></div>`).join("") || '<div class="xiaomeng-empty">先告诉我你想拍什么。</div>';
      const plan = session.plan || {};
      $("xiaomengPlan").innerHTML = `<div class="xiaomeng-plan-head"><strong>当前规划</strong><span>${esc(plan.title || "等待想法")}</span></div><p>${esc(plan.brief || "对话后会生成创作目标")}</p><div class="xiaomeng-plan-grid"><span>人物 ${plan.characters?.length || 0}</span><span>场景 ${plan.scenes?.length || 0}</span><span>分镜 ${plan.shots?.length || 0}</span></div>`;
      const busy = session.job?.status === "queued" || session.job?.status === "running";
      $("xiaomengRun").disabled = busy;
      $("xiaomengRun").textContent = busy ? `${session.job.message || "托管中"} ${session.job.progress || 0}%` : "开始托管";
      $("xiaomengConfirm").disabled = busy || session.status === "completed";
      const box = $("xiaomengMessages"); box.scrollTop = box.scrollHeight;
    }
    async function loadSession() {
      const id = projectId();
      session = await Studio.api(`/photodream/api/agent/sessions${id ? `?project_id=${encodeURIComponent(id)}` : ""}`);
      if (!session) session = await Studio.post("/photodream/api/agent/sessions", {project_id: id, mode: $("xiaomengMode").value});
      render();
    }
    async function open() {
      dialog.showModal();
      if (!session || (session.project_id || "") !== projectId()) {
        try { await loadSession(); } catch (error) { Studio.showError(error); }
      }
      $("xiaomengInput").focus();
    }
    async function refresh() {
      if (!session) return;
      session = await Studio.api(`/photodream/api/agent/sessions/${session.id}`); render();
      if (session.job?.status === "queued" || session.job?.status === "running") pollTimer = setTimeout(refresh, 1200);
    }
    launcher.onclick = open;
    dialog.querySelector(".xiaomeng-close").onclick = () => dialog.close();
    $("xiaomengMode").onchange = async () => {
      if (!session) return;
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/mode`, {mode:$("xiaomengMode").value}); render(); }
      catch (error) { Studio.showError(error); }
    };
    $("xiaomengForm").onsubmit = async event => {
      event.preventDefault(); if (loading || !session) return;
      const content = $("xiaomengInput").value.trim(); if (!content) return;
      loading = true; $("xiaomengInput").disabled = true;
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/messages`, {content}); $("xiaomengInput").value = ""; render(); }
      catch (error) { Studio.showError(error); }
      finally { loading = false; $("xiaomengInput").disabled = false; $("xiaomengInput").focus(); }
    };
    $("xiaomengConfirm").onclick = async () => {
      if (!session || loading) return;
      loading = true;
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/confirm`, {}); render(); }
      catch (error) { Studio.showError(error); }
      finally { loading = false; }
    };
    $("xiaomengRun").onclick = async () => {
      if (!session || loading) return;
      loading = true;
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/run`, {}); render(); refresh(); }
      catch (error) { Studio.showError(error); }
      finally { loading = false; }
    };
  }
  async function mountPage(root, Studio) {
    let session, projects = [], selectedProject = new URL(location.href).searchParams.get("project") || "";
    root.innerHTML = `<div class="planning-page"><header class="page-head"><div><div class="eyebrow">PHOTO DREAM / CREATIVE PLANNING</div><div class="planning-title-row"><div class="planning-agent-avatar xiaomeng-illustration-slot" data-xiaomeng-illustration="planning" aria-label="小梦角色立绘"></div><div><h1>创作规划</h1><p>和小梦一起完成文案、剧本、人物、场景和分镜准备，再交给分镜工作台生图。</p></div></div></div><div class="planning-head-actions"><select id="planningProject" aria-label="当前项目"></select><button id="planningNew" type="button">新建会话</button></div></header><div class="planning-layout"><aside class="planning-rail"><h3>创作阶段</h3><div id="planningStages"></div><div class="planning-mode"><label>工作方式<select id="planningMode"><option value="guided">协作模式</option><option value="autonomous">完全托管</option></select></label><button id="planningRun" class="primary" type="button">开始托管</button></div></aside><section class="planning-chat c-panel"><div id="planningMessages" class="planning-messages"></div><form id="planningForm"><textarea id="planningInput" rows="4" maxlength="20000" required placeholder="描述你的故事、用途、人物或画面想法…"></textarea><div class="planning-chat-actions"><span class="muted">每个阶段确认后才会写入项目。</span><button class="primary" type="submit">发送给小梦</button></div></form></section><section class="planning-editor c-panel"><div class="page-head"><div><h2>规划结果</h2><p>可以直接编辑，保存后再确认。</p></div><button id="planningSave" type="button">保存规划</button></div><div id="planningFields"></div><div class="planning-editor-actions"><button id="planningConfirm" class="primary" type="button">确认当前阶段并写入项目</button></div></section></div></div>`;
    mountCharacterSlots(root);
    const $ = id => root.querySelector(`#${id}`);
    const projectQuery = () => `?project_id=${encodeURIComponent(selectedProject)}`;
    const getProjectId = () => selectedProject;
    function renderStages() {
      $("planningStages").innerHTML = (session?.stages || []).map(item => `<div class="planning-stage ${item.active ? "active" : ""}"><span>${esc(item.label)}</span>${item.active ? "<small>当前</small>" : ""}</div>`).join("");
    }
    function field(label, name, value, rows = 3) { return `<label>${label}<textarea data-plan-field="${name}" rows="${rows}">${esc(value)}</textarea></label>`; }
    function renderEditor() {
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
      $("planningMode").value = session.mode; renderStages(); renderEditor();
      $("planningMessages").innerHTML = session.messages.map(item => `<article class="planning-message ${item.role}"><small>${item.role === "user" ? "你" : "小梦"}</small><div>${esc(item.content).replace(/\n/g, "<br>")}</div></article>`).join("") || '<div class="planning-empty">在这里告诉小梦你的创作想法。</div>';
      const box = $("planningMessages"); box.scrollTop = box.scrollHeight;
      const busy = ["queued", "running"].includes(session.job?.status);
      $("planningRun").disabled = busy; $("planningConfirm").disabled = busy || session.status === "completed";
      $("planningRun").textContent = busy ? `${session.job.message || "托管中"} ${session.job.progress || 0}%` : "开始托管";
    }
    async function load() {
      projects = await Studio.api("/photodream/api/projects");
      const select = $("planningProject");
      select.innerHTML = '<option value="">新建项目，规划后创建</option>' + projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
      select.value = getProjectId(); selectedProject = select.value;
      session = await Studio.api(`/photodream/api/agent/sessions${projectQuery()}`);
      if (!session) session = await Studio.post("/photodream/api/agent/sessions", {project_id:selectedProject, mode:$("planningMode").value});
      render();
    }
    async function savePlan() { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/plan`, collectPlan()); render(); }
    $("planningProject").onchange = async event => { selectedProject = event.target.value; const url = new URL(location.href); if (selectedProject) url.searchParams.set("project", selectedProject); else url.searchParams.delete("project"); history.replaceState(null, "", url); await load(); };
    $("planningNew").onclick = async () => {
      selectedProject = "";
      const url = new URL(location.href); url.searchParams.delete("project"); history.replaceState(null, "", url);
      session = await Studio.post("/photodream/api/agent/sessions", {project_id:"", mode:$("planningMode").value});
      $("planningProject").value = ""; render();
    };
    $("planningForm").onsubmit = async event => { event.preventDefault(); try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/messages`, {content:$("planningInput").value.trim()}); $("planningInput").value = ""; render(); } catch (error) { Studio.showError(error); } };
    $("planningSave").onclick = async () => { try { await savePlan(); } catch (error) { Studio.showError(error); } };
    $("planningConfirm").onclick = async () => { try { await savePlan(); session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/confirm`, {}); render(); } catch (error) { Studio.showError(error); } };
    $("planningRun").onclick = async () => { try { await savePlan(); session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/run`, {}); render(); const poll = async () => { session = await Studio.api(`/photodream/api/agent/sessions/${session.id}`); render(); if (["queued", "running"].includes(session.job?.status)) setTimeout(poll, 1200); }; poll(); } catch (error) { Studio.showError(error); } };
    $("planningMode").onchange = async () => {
      if (!session) return;
      try { session = await Studio.post(`/photodream/api/agent/sessions/${session.id}/mode`, {mode:$("planningMode").value}); render(); }
      catch (error) { Studio.showError(error); }
    };
    root.addEventListener("click", event => { const add = event.target.closest("[data-plan-add]"); if (add) { const kind = add.dataset.planAdd, list = session.plan[kind] || []; list.push(kind === "shots" ? {title:"", prompt:""} : {name:"", content:""}); session.plan[kind] = list; renderEditor(); return; } const remove = event.target.closest("[data-plan-remove]"); if (remove) { remove.closest("[data-plan-item]").remove(); } });
    await load();
  }
  return {mount, mountPage};
})();
