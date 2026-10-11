"use strict";
(() => {
  const $ = id => document.getElementById(id), e = Studio.escape;
  const workbenches = import("/photodream/static/workbenches.js");
  let renderVersion = 0, previousView = "", initialized = false;
  const scrollPositions = new Map();
  let projects = [], teams = [], resources = [], selectedTeam = "", search = "", scope = "all", archive = "active", modalAction;
  const roles = {owner:"所有者", admin:"管理员", member:"成员"};
  const teamName = id => teams.find(t => t.id === id)?.name || "团队项目";
  const date = value => value ? new Date(value).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";
  const view = () => Studio.route();
  function notify(message, error = false) { $("notice").hidden = false; $("notice").className = "notice" + (error ? " error" : ""); $("notice").textContent = message; }
  async function refresh() { [projects, teams, resources] = await Promise.all([Studio.cached("/photodream/api/projects"), Studio.api("/photodream/api/teams"), Studio.cached("/photodream/api/resources")]); initialized = true; }
  function heading(title, description, action = "") { return `<header class="page-head"><div><div class="eyebrow">Photo Dream / ${e(view().toUpperCase())}</div><h1>${title}</h1><p>${description}</p></div>${action}</header>`; }
  function teamOptions(includePrivate = true) { return (includePrivate ? '<option value="">个人空间 · 仅自己可见</option>' : "") + teams.map(t => `<option value="${e(t.id)}">${e(t.name)}</option>`).join(""); }
  function modal(title, fields, action, submit = "保存") {
    $("dialogTitle").textContent = title; $("dialogFields").innerHTML = fields; $("dialogError").textContent = ""; $("dialogSubmit").textContent = submit; modalAction = action; $("consoleDialog").showModal();
  }
  $("dialogCancel").onclick = () => $("consoleDialog").close();
  $("dialogForm").onsubmit = async event => {
    event.preventDefault(); $("dialogSubmit").disabled = true;
    try { await modalAction(Object.fromEntries(new FormData(event.currentTarget))); $("consoleDialog").close(); await refresh(); await render(); }
    catch (error) { $("dialogError").textContent = error.message; }
    finally { $("dialogSubmit").disabled = false; }
  };
  function projectCard(p) {
    const manage = p.team_id ? ["owner","admin"].includes(teams.find(t => t.id === p.team_id)?.role) : p.owner_id === Studio.user.id;
    return `<article class="project-card"><span class="tag">${p.team_id ? e(teamName(p.team_id)) : "个人项目"}${p.archived ? " · 已归档" : ""}</span><h2>${e(p.name)}</h2><p>${e(p.synopsis || "还没有项目简介，记录故事的起点。")}</p><div class="card-meta"><span>${p.frames.length} 个分镜</span><span>${p.character_cards.length + p.scene_cards.length} 张卡片</span><span>${date(p.updated_at)}</span></div><div class="card-actions"><a class="c-button primary" href="/photodream/storyboard?project=${e(p.id)}">打开分镜 ↗</a><a class="c-button" href="/photodream/single?project=${e(p.id)}">单图</a><button data-action="edit" data-id="${e(p.id)}">编辑</button>${manage ? `<button data-action="share" data-id="${e(p.id)}">共享设置</button><button data-action="archive" data-id="${e(p.id)}">${p.archived ? "取消归档" : "归档"}</button>` : ""}</div></article>`;
  }
  function overview() {
    const active = projects.filter(p => !p.archived), frames = active.flatMap(p => p.frames);
    $("consoleView").innerHTML = heading(`你好，${e(Studio.user.display_name)}`, "管理你的创作，和团队一起推进故事。", '<button class="primary" data-action="new-project">＋ 新建项目</button>') +
      `<section class="stats"><div class="stat"><span>进行中的项目</span><strong>${active.length}</strong></div><div class="stat"><span>分镜 / 已生成</span><strong>${frames.length}<small class="muted"> / ${frames.filter(f => f.image_url).length}</small></strong></div><div class="stat"><span>角色与场景资源</span><strong>${resources.length}</strong></div><div class="stat"><span>我的团队</span><strong>${teams.length}</strong></div></section><section class="quick-grid"><a class="c-panel" href="/photodream/storyboard"><div class="eyebrow">STORYBOARD</div><h3>分镜工作台 <span class="arrow">↗</span></h3><p>铺开镜头、标记批量生成，逐帧打磨故事。</p></a><a class="c-panel" href="/photodream/single"><div class="eyebrow">IMAGE STUDIO</div><h3>单图工作台 <span class="arrow">↗</span></h3><p>文字生成、参考图与角色卡，探索画面。</p></a><a class="c-panel" href="#teams"><div class="eyebrow">COLLABORATION</div><h3>团队空间 <span class="arrow">↗</span></h3><p>共享项目与资源，让创作在成员间接续。</p></a></section><div class="page-head" style="margin-top:32px"><h2>最近更新的项目</h2><a class="c-button" href="#projects">查看全部 →</a></div><div class="project-grid">${active.slice(0,6).map(projectCard).join("") || '<div class="empty">暂无项目，点击右上角创建你的第一个项目。</div>'}</div>`;
  }
  function projectsPage() {
    $("consoleView").innerHTML = heading("项目管理", "个人项目独立保存；团队项目由成员共同编辑。", '<button class="primary" data-action="new-project">＋ 新建项目</button>') +
      `<div class="filters"><input id="projectSearch" placeholder="搜索项目名称或简介" value="${e(search)}"><select id="projectScope"><option value="all">全部空间</option><option value="private">个人空间</option>${teamOptions(false)}</select><select id="archiveFilter"><option value="active">进行中</option><option value="archived">已归档</option><option value="all">全部状态</option></select></div><div id="projectGrid" class="project-grid"></div>`;
    $("projectScope").value = scope; $("archiveFilter").value = archive;
    function list() {
      const visible = projects.filter(p => (archive === "all" || Boolean(p.archived) === (archive === "archived")) && (scope === "all" || (scope === "private" ? !p.team_id : p.team_id === scope)) && `${p.name} ${p.synopsis}`.toLowerCase().includes(search.toLowerCase()));
      $("projectGrid").innerHTML = visible.map(projectCard).join("") || '<div class="empty">没有符合条件的项目。</div>';
    }
    $("projectSearch").oninput = event => { search = event.target.value; list(); };
    $("projectScope").onchange = event => { scope = event.target.value; list(); };
    $("archiveFilter").onchange = event => { archive = event.target.value; list(); }; list();
  }
  function resourcesPage() {
    $("consoleView").innerHTML = heading("资源中心", "汇总可访问项目中的角色卡与场景卡。导入副本后，可直接在目标项目的镜头中使用。") +
      `<div class="filters"><input id="resourceSearch" placeholder="搜索资源名称、设定或来源项目"><select id="resourceScope"><option value="all">全部空间</option><option value="private">个人空间</option>${teamOptions(false)}</select><select id="resourceType"><option value="all">角色卡与场景卡</option><option value="character">角色卡</option><option value="scene">场景卡</option></select></div><div id="resourceGrid" class="resource-grid"></div>`;
    function list() {
      const term = $("resourceSearch").value.toLowerCase(), type = $("resourceType").value, filter = $("resourceScope").value;
      $("resourceGrid").innerHTML = resources.filter(c => (type === "all" || c.card_type === type) && (filter === "all" || (filter === "private" ? !c.team_id : c.team_id === filter)) && `${c.name} ${c.content} ${c.project_name}`.toLowerCase().includes(term)).map(c =>
        `<article class="c-panel resource-card">${c.image_url ? `<img class="resource-image" src="${e(Studio.asset(c.image_url))}" alt="${e(c.name)}" loading="lazy">` : '<div class="resource-image" style="display:grid;place-items:center;color:#8fb39b">▧ 文字设定</div>'}<h3>${e(c.name)}</h3><span class="tag">${c.card_type === "character" ? "角色卡" : "场景卡"}</span><p>${e(c.content)}</p><div class="card-meta">来自 ${e(c.project_name)} · ${c.team_id ? e(teamName(c.team_id)) : "个人"}</div><button data-action="import" data-id="${e(c.id)}" data-project="${e(c.project_id)}">导入到项目…</button></article>`).join("") || '<div class="empty">暂无资源。在单图或分镜工作台中创建角色卡与场景卡。</div>';
    }
    for (const id of ["resourceSearch","resourceScope","resourceType"]) $(id).addEventListener(id === "resourceSearch" ? "input" : "change", list); list();
  }
  async function teamsPage() {
    if (!teams.some(t => t.id === selectedTeam)) selectedTeam = teams[0]?.id || "";
    const team = teams.find(t => t.id === selectedTeam), manage = team && team.role !== "member";
    $("consoleView").innerHTML = heading("团队空间", "团队项目中的镜头、卡片与图像对成员共享。", '<div class="section-actions"><button data-action="join-team">加入团队</button><button class="primary" data-action="new-team">＋ 创建团队</button></div>') +
      `<div class="team-layout"><div class="team-list">${teams.map(t => `<button class="${t.id === selectedTeam ? "selected" : ""}" data-action="select-team" data-id="${e(t.id)}">${e(t.name)}<small>${roles[t.role]}</small></button>`).join("") || '<div class="empty">还没有加入团队。</div>'}</div><div>${team ? `<section class="c-panel"><h2>${e(team.name)}</h2><p class="muted">你的角色：${roles[team.role]}。成员可编辑团队项目，所有者和管理员可管理团队。</p><div class="section-actions"><button data-action="new-team-project" data-id="${e(team.id)}">＋ 团队项目</button>${manage ? `<button data-action="invite" data-id="${e(team.id)}">邀请成员</button><button data-action="revoke-invites" data-id="${e(team.id)}">撤销未使用邀请</button><button data-action="rename-team" data-id="${e(team.id)}">修改名称</button>` : ""}${team.role !== "owner" ? `<button class="danger" data-action="remove-member" data-id="${e(Studio.user.id)}" data-team="${e(team.id)}">退出团队</button>` : ""}</div><div id="inviteResult"></div><div id="memberTable" class="table-wrap">正在加载成员…</div></section><h2>共享项目</h2><div class="project-grid">${projects.filter(p => p.team_id === team.id && !p.archived).map(projectCard).join("") || '<div class="empty">暂无共享项目。创建团队项目或在项目管理中设置共享。</div>'}</div>` : '<div class="empty">创建团队，或使用其他成员发来的邀请码加入。</div>'}</div></div>`;
    if (team) {
      const members = await Studio.api(`/photodream/api/teams/${team.id}/members`);
      if (view() !== "teams" || selectedTeam !== team.id) return;
      $("memberTable").innerHTML = `<table class="c-table"><thead><tr><th>成员</th><th>用户名</th><th>角色</th><th>操作</th></tr></thead><tbody>${members.map(m => `<tr><td>${e(m.display_name)}</td><td>${e(m.username)}</td><td>${roles[m.role]}</td><td>${m.role !== "owner" && team.role === "owner" ? `<button data-action="role" data-id="${e(m.id)}" data-team="${e(team.id)}" data-role="${m.role === "admin" ? "member" : "admin"}">${m.role === "admin" ? "设为成员" : "设为管理员"}</button> ` : ""}${manage && m.role !== "owner" && (team.role === "owner" || m.role === "member") ? `<button class="danger" data-action="remove-member" data-id="${e(m.id)}" data-team="${e(team.id)}">移除</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
    }
  }
  function accountPage() {
    $("consoleView").innerHTML = heading("账号设置", "管理显示名称与登录密码。修改密码后，所有已登录会话会退出。") + `<div class="account-grid"><section class="c-panel"><h2>个人资料</h2><form id="profileForm" class="c-form"><label>用户名<input value="${e(Studio.user.username)}" disabled></label><label>显示名称<input name="display_name" required maxlength="100" value="${e(Studio.user.display_name)}"></label><button class="primary">保存资料</button></form></section><section class="c-panel"><h2>修改密码</h2><form id="passwordForm" class="c-form"><label>当前密码<input type="password" name="current_password" autocomplete="current-password" required maxlength="128"></label><label>新密码<input type="password" name="new_password" autocomplete="new-password" required minlength="10" maxlength="128"></label><label>确认新密码<input type="password" name="confirm_password" autocomplete="new-password" required minlength="10" maxlength="128"></label><button class="primary">更新密码并退出</button></form></section></div>`;
    $("profileForm").onsubmit = async event => { event.preventDefault(); try { Object.assign(Studio.user, await Studio.post("/photodream/api/auth/profile", Object.fromEntries(new FormData(event.currentTarget)))); document.querySelector(".studio-account a").textContent = Studio.user.display_name; notify("资料已保存。"); } catch (error) { notify(error.message,true); } };
    $("passwordForm").onsubmit = async event => { event.preventDefault(); try { const data = Object.fromEntries(new FormData(event.currentTarget)); if (data.new_password !== data.confirm_password) throw new Error("两次新密码不一致。"); delete data.confirm_password; await Studio.post("/photodream/api/auth/password",data); location.assign("/photodream/login"); } catch (error) { notify(error.message,true); } };
  }
  async function usersPage() {
    if (!Studio.user.is_admin) { location.hash = "overview"; return; }
    const users = await Studio.api("/photodream/api/admin/users");
    $("consoleView").innerHTML = heading("用户管理", "维护账号与登录权限。系统管理员不会自动获得他人的私人项目权限。", '<button class="primary" data-action="new-user">＋ 创建用户</button>') + `<section class="c-panel table-wrap"><table class="c-table"><thead><tr><th>用户名</th><th>显示名称</th><th>权限</th><th>状态</th><th>注册时间</th><th>操作</th></tr></thead><tbody>${users.map(u => `<tr><td>${e(u.username)}</td><td>${e(u.display_name)}</td><td>${u.is_admin ? "系统管理员" : "普通用户"}</td><td>${u.is_active ? "正常" : "已停用"}</td><td>${date(u.created_at)}</td><td>${u.id !== Studio.user.id ? `<button data-action="user-status" data-id="${e(u.id)}" data-active="${!u.is_active}">${u.is_active ? "停用" : "启用"}</button> <button data-action="reset-password" data-id="${e(u.id)}">重置密码</button>` : "当前账号"}</td></tr>`).join("")}</tbody></table></section>`;
  }
  async function render() {
    const version = ++renderVersion, name = view(), isWorkbench = ["single","storyboard"].includes(name);
    Studio.highlight(name);
    const module = await workbenches;
    if (version !== renderVersion) return;
    if (previousView && !["single","storyboard"].includes(previousView)) scrollPositions.set(previousView,$("workspaceMain").scrollTop);
    const wasWorkbench = ["single","storyboard"].includes(previousView); previousView = name;
    module.hideAll();
    $("consoleDialog").close(); $("notice").hidden = true;
    $("consoleView").hidden = isWorkbench; $("workbenchViews").hidden = !isWorkbench;
    $("workspaceMain").classList.toggle("workbench-main",isWorkbench);
    $("workbenchLoading").hidden = !isWorkbench; $("workbenchRetry").hidden = true;
    $("workbenchLoading").querySelector("span:last-of-type").textContent = "正在准备工作台…";
    document.title = `${({single:"单图工作台",storyboard:"分镜工作台",projects:"项目管理",resources:"资源中心",teams:"团队空间",account:"账号设置",users:"用户管理"})[name] || "创作中台"} · Photo Dream`;
    if (isWorkbench) {
      const projectId = new URL(location.href).searchParams.get("project") || Studio.projectId;
      try {
        const record = await module.open(name,projectId,Studio);
        if (version !== renderVersion) return;
        record.host.hidden = false;
        await record.controller.activate(projectId);
        if (version !== renderVersion) return;
        $("workbenchLoading").hidden = true;
        $("workspaceMain").scrollTop = record.scroll;
      } catch (error) {
        if (version !== renderVersion) return;
        $("workbenchLoading").querySelector("span:last-of-type").textContent = error.message;
        $("workbenchRetry").hidden = false;
      }
      return;
    }
    if (!initialized || wasWorkbench) await refresh();
    if (version !== renderVersion) return;
    await ({overview,projects:projectsPage,resources:resourcesPage,teams:teamsPage,account:accountPage,users:usersPage}[name] || overview)();
    if (version === renderVersion) $("workspaceMain").scrollTop = scrollPositions.get(name) || 0;
  }
  function newProject(team = "") {
    modal("创建项目", `<label>项目名称<input name="name" required maxlength="200" placeholder="例如：雨夜故事"></label><label>项目简介<textarea name="synopsis" rows="3" maxlength="20000" placeholder="故事背景、视觉风格或创作目标"></textarea></label><label>所属空间<select name="team_id">${teamOptions()}</select></label>`, async data => {
      const form = new FormData(); Object.entries(data).forEach(([key,value]) => form.append(key,value));
      await Studio.api("/photodream/api/projects",{method:"POST",body:form}); notify("项目已创建，可打开工作台开始创作。");
    },"创建项目"); $("dialogForm").elements.team_id.value = team;
  }
  $("consoleView").addEventListener("click", async event => {
    const button = event.target.closest("[data-action]"); if (!button) return;
    const {action,id,team,role,project: sourceId} = button.dataset, p = projects.find(p => p.id === id);
    try {
      if (action === "new-project") newProject();
      else if (action === "new-user") modal("创建用户", '<label>用户名<input name="username" required minlength="3" maxlength="64" autocomplete="off" pattern="[a-zA-Z0-9_\\-.]+"></label><label>显示名称<input name="display_name" maxlength="100"></label><label>初始密码<input name="password" type="password" required minlength="10" maxlength="128" autocomplete="new-password"></label>', data => Studio.post("/photodream/api/admin/users",data),"创建用户");
      else if (action === "user-status") { const is_active = button.dataset.active === "true"; if (!confirm(is_active ? "启用此用户账号？" : "停用此用户账号？所有已登录会话会立即失效，数据仍保留。")) return; await Studio.post(`/photodream/api/admin/users/${id}/status`,{is_active}); await render(); }
      else if (action === "reset-password") modal("重置用户密码", '<p class="muted">重置后此用户的所有已登录会话会退出。请将新密码安全地交给账号本人。</p><label>新密码<input name="password" type="password" required minlength="10" maxlength="128" autocomplete="new-password"></label>',data => Studio.post(`/photodream/api/admin/users/${id}/password`,data),"重置密码");
      else if (action === "new-team-project") newProject(id);
      else if (action === "edit") modal("编辑项目", `<label>项目名称<input name="name" required maxlength="200" value="${e(p.name)}"></label><label>项目简介<textarea name="synopsis" rows="5" maxlength="20000">${e(p.synopsis)}</textarea></label>`, data => Studio.post(`/photodream/api/projects/${id}/metadata`,data));
      else if (action === "share") { modal("项目共享设置", `<p class="muted">共享后团队成员可以访问并编辑项目内所有资源。移出团队后原成员将失去访问权限。</p><label>所属空间<select name="team_id">${teamOptions()}</select></label>`, data => Studio.post(`/photodream/api/projects/${id}/sharing`,{team_id:data.team_id || null})); $("dialogForm").elements.team_id.value = p.team_id || ""; }
      else if (action === "archive") { if (!confirm(p.archived ? "取消归档此项目？" : "归档此项目？所有镜头和资源仍保留，可随时取消归档。")) return; await Studio.post(`/photodream/api/projects/${id}/metadata`,{archived:!p.archived}); await refresh(); await render(); notify(p.archived ? "项目已恢复。" : "项目已归档。"); }
      else if (action === "new-team") modal("创建团队", '<label>团队名称<input name="name" required maxlength="100" placeholder="例如：视觉创作组"></label>',async data => { const created = await Studio.post("/photodream/api/teams",data); selectedTeam = created.id; });
      else if (action === "join-team") modal("加入团队", '<label>邀请码<input name="token" required minlength="20" maxlength="100" placeholder="粘贴团队管理员发来的邀请码"></label><p class="muted">邀请码仅可使用一次，有效期 48 小时。</p>', async data => { const joined = await Studio.post("/photodream/api/teams/join",data); selectedTeam = joined.id; },"加入团队");
      else if (action === "select-team") { selectedTeam = id; await render(); }
      else if (action === "rename-team") modal("修改团队名称", `<label>团队名称<input name="name" required maxlength="100" value="${e(teamName(id))}"></label>`,data => Studio.post(`/photodream/api/teams/${id}/name`,data));
      else if (action === "invite") { const invite = await Studio.post(`/photodream/api/teams/${id}/invites`,{}); $("inviteResult").innerHTML = `<p class="muted">将邀请码发送给要邀请的人，48 小时内有效，仅可使用一次。</p><code class="invite-code">${e(invite.token)}</code><p class="muted">有效期至 ${date(invite.expires_at)}</p>`; }
      else if (action === "revoke-invites") { if (!confirm("撤销这个团队的所有未使用邀请？")) return; await Studio.api(`/photodream/api/teams/${id}/invites`,{method:"DELETE"}); $("inviteResult").innerHTML = ""; notify("未使用的邀请已撤销。"); }
      else if (action === "remove-member") { if (!confirm(id === Studio.user.id ? "退出团队后将无法访问团队项目，确认退出？" : "移除此成员？他将失去团队项目和资源的访问权限。")) return; await Studio.api(`/photodream/api/teams/${team}/members/${id}`,{method:"DELETE"}); await refresh(); await render(); }
      else if (action === "role") { await Studio.post(`/photodream/api/teams/${team}/members/${id}/role`,{role}); await render(); }
      else if (action === "import") { const card = resources.find(c => c.id === id && c.project_id === sourceId), targets = projects.filter(p => !p.archived && p.id !== sourceId); if (!targets.length) throw new Error("请先创建一个目标项目。"); modal("导入资源副本", `<p class="muted">将「${e(card.name)}」复制到目标项目。之后修改副本不会影响来源卡片。</p><label>目标项目<select name="target">${targets.map(p => `<option value="${e(p.id)}">${e(p.name)} · ${p.team_id ? e(teamName(p.team_id)) : "个人"}</option>`).join("")}</select></label>`,data => Studio.post(`/photodream/api/projects/${data.target}/resources/import`,{source_project_id:sourceId, card_type:card.card_type,card_ids:[id]}),"导入资源"); }
    } catch (error) { notify(error.message,true); }
  });
  $("workbenchRetry").onclick = () => render();
  Studio.onRoute(() => Studio.ready.then(render).catch(error => notify(error.message,true)));
  Studio.ready.then(async () => {
    $("adminMenu").hidden = !Studio.user.is_admin; await render();
    const preload = () => workbenches.then(module => module.preload());
    if ("requestIdleCallback" in window) requestIdleCallback(preload,{timeout:2000}); else setTimeout(preload,500);
  }).catch(error => notify(error.message,true));
})();
