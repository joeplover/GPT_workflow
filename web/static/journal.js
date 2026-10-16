export async function mount(root, Studio, projects) {
  const e = Studio.escape, $ = id => root.querySelector(`#${id}`);
  const initial = new URL(location.href).searchParams;
  const statusNames = {generating:"生成中", succeeded:"已完成", failed:"失败", interrupted:"已中断"};
  let cursor = null, version = 0, detailVersion = 0, detailId = null, records = new Map();
  const openProjects = new Set();
  root.innerHTML = `<header class="page-head"><div><div class="eyebrow">PHOTO DREAM / HISTORY</div><h1>生成日志</h1><p>按项目保存生成记录。打开详情才加载图片；彻底删除图片后仍可追溯提示词。</p></div><button id="journalRefresh">刷新记录</button></header>
    <div class="filters"><select id="journalProject"><option value="">全部可访问项目</option>${projects.map(p => `<option value="${e(p.id)}">${e(p.name)}</option>`).join("")}</select><select id="journalStatus"><option value="">全部状态</option>${Object.entries(statusNames).map(([key, value]) => `<option value="${key}">${value}</option>`).join("")}</select><input id="journalFrame" placeholder="镜头 ID（可选）" aria-label="镜头 ID"></div>
    <p id="journalError" class="form-error" role="alert"></p><div id="journalGrid" class="project-groups"></div><button id="journalMore" hidden>加载更早记录</button>
    <dialog id="journalDetails"><div class="page-head"><h2>生成详情</h2><button id="journalClose" type="button">关闭</button></div><p id="journalDetailError" class="form-error" role="alert"></p><div id="journalDetailBody"></div></dialog>`;
  if (projects.some(p => p.id === initial.get("project"))) $("journalProject").value = initial.get("project");
  $("journalFrame").value = initial.get("frame") || "";
  const formatTime = time => time ? new Date(time).toLocaleString("zh-CN") : "—";
  function card(r) {
    const q = r.request;
    return `<article class="c-panel generation-card"><span class="tag">${statusNames[r.status] || e(r.status)}${r.historical ? " · 历史补录" : ""}${r.image_deleted ? " · 图片已删除" : ""}</span><h3>${e(q.frame_title || "单图生成")}</h3><div class="card-meta">#${r.id} · ${formatTime(r.created_at)}<br>${e(q.model || "未知模型")} · ${e(q.size || "未知尺寸")} · ${e(q.actor_name || "历史操作人未知")}</div><p class="journal-prompt">${e(q.prompt || "历史提示词未记录")}</p>${r.error ? `<p class="form-error">${e(Studio.errorSummary(r.error))}</p>` : ""}<div class="card-actions"><button data-generation="${r.id}">查看完整记录</button></div></article>`;
  }
  function renderGroups() {
    const groups = new Map();
    for (const r of records.values()) {
      if (!groups.has(r.project_id)) groups.set(r.project_id, []);
      groups.get(r.project_id).push(r);
    }
    $("journalGrid").innerHTML = [...groups].map(([id, rows]) => `<details class="project-resource-group" data-group-project="${e(id)}" ${$("journalProject").value || openProjects.has(id) ? "open" : ""}><summary><span>${e(rows[0].project_name)}</span><span class="muted">已加载 ${rows.length} 条记录</span></summary><div class="resource-grid">${rows.map(card).join("")}</div></details>`).join("") || '<div class="empty">暂无生成记录。</div>';
    for (const group of $("journalGrid").querySelectorAll("details")) group.ontoggle = () => {
      if (!group.isConnected) return;
      if (group.open) openProjects.add(group.dataset.groupProject); else openProjects.delete(group.dataset.groupProject);
    };
  }
  async function load(reset = false) {
    const current = ++version;
    if (reset) { cursor = null; records = new Map(); $("journalGrid").innerHTML = ""; }
    $("journalMore").disabled = true; $("journalError").textContent = "";
    const params = new URLSearchParams({limit: "30"});
    for (const [key, value] of [["project_id", $("journalProject").value], ["status", $("journalStatus").value], ["frame_id", $("journalFrame").value.trim()], ["before", cursor]]) if (value) params.set(key, value);
    try {
      const page = await Studio.api(`/photodream/api/generations?${params}`);
      if (current !== version || !root.isConnected || Studio.route() !== "generations") return;
      for (const item of page.items) records.set(item.id, item);
      renderGroups(); cursor = page.next_before; $("journalMore").hidden = !cursor;
    } catch (error) { if (current === version && root.isConnected && $("journalError")) { $("journalError").textContent = "记录加载失败，请重试。"; Studio.showError(error); } }
    finally { if (current === version && $("journalMore")) $("journalMore").disabled = false; }
  }
  function preview(url, r, output = false) {
    const deleted = r.deleted_images[url];
    return `<div class="journal-image${output ? " journal-output" : ""}">${deleted ? `<p class="muted">图片已彻底删除 · ${formatTime(deleted.deleted_at)}${deleted.file_removed ? "" : "（文件清理待重试）"}</p>` : `<a href="${e(Studio.asset(url))}" target="_blank" rel="noopener"><img src="${e(Studio.asset(url))}" alt="${output ? "本次生成图片" : "当时参考图片"}"></a>`}<small>${e(url)}</small><div class="card-actions">${!deleted && output ? `<a class="c-button primary" href="${e(Studio.downloadUrl(url))}" download>下载这次生成的图片</a>` : ""}${!deleted || !deleted.file_removed ? `<button class="c-button danger" data-purge-image="${e(url)}">${deleted ? "重试文件清理" : "彻底删除图片"}</button>` : ""}</div></div>`;
  }
  async function showDetail(id) {
    const current = ++detailVersion;
    detailId = id;
    $("journalDetailError").textContent = "";
    $("journalDetailBody").innerHTML = '<p class="muted">正在加载记录…</p>';
    if (!$("journalDetails").open) $("journalDetails").showModal();
    try {
      const r = await Studio.api(`/photodream/api/generations/${id}`);
      if (current !== detailVersion || !root.isConnected || !$("journalDetails").open) return;
      records.set(r.id, r); renderGroups();
      const q = r.request;
      const field = (label, text) => `<label>${label}<textarea readonly rows="4">${e(text || "（未记录）")}</textarea></label>`;
      const refs = [...new Set([q.source_image, q.original_source_image, q.mask_image, ...(q.reference_images || []), ...[...(q.character_cards || []), ...(q.scene_cards || [])].map(c => c.image_url)].filter(Boolean))];
      $("journalDetailBody").innerHTML = `<div class="c-form">
        <p>记录 #${r.id} · ${statusNames[r.status]} · ${formatTime(r.created_at)}<br>项目：${e(r.project_name)}<br>镜头 ID：${e(r.frame_id || "单图工作台")}<br>模型：${e(q.model || "未知")} / 分辨率：${e(q.size || "未知")}<br>完成时间：${formatTime(r.finished_at)}</p>
        ${r.historical ? '<p class="muted">由现有历史数据补录，部分字段可能缺失。</p>' : ""}
        ${r.image_url ? `<h3>本次生成图片</h3>${preview(r.image_url, r, true)}` : '<p class="muted">本次没有生成图片。</p>'}
        ${field("系统提示词", q.system_prompt)}${field("用户提示词", q.prompt)}${field("实际发送的完整提示词", q.composed_prompt)}
        ${field("当时的角色卡与场景卡快照", JSON.stringify({character_cards:q.character_cards || [], scene_cards:q.scene_cards || []}, null, 2))}
        ${field("完整记录（可复制保存）", JSON.stringify(r, null, 2))}
        ${refs.length ? `<h3>当时参考图片</h3><div class="journal-references">${refs.map(url => preview(url, r)).join("")}</div>` : ""}
        ${r.error ? `<p class="form-error">${e(Studio.errorSummary(r.error))}</p>` : ""}</div>`;
    } catch (error) { if (current === detailVersion && root.isConnected && $("journalDetailError")) { $("journalDetailError").textContent = "详情加载失败，请重试。"; Studio.showError(error); } }
  }
  $("journalRefresh").onclick = () => load(true); $("journalMore").onclick = () => load();
  for (const id of ["journalProject", "journalStatus", "journalFrame"]) $(id).onchange = () => load(true);
  $("journalClose").onclick = () => $("journalDetails").close();
  $("journalDetails").onclose = () => { detailVersion++; $("journalDetailBody").innerHTML = ""; };
  $("journalGrid").onclick = event => {
    const button = event.target.closest("[data-generation]");
    if (button) showDetail(Number(button.dataset.generation));
  };
  $("journalDetailBody").onclick = async event => {
    const button = event.target.closest("[data-purge-image]"); if (!button) return;
    button.disabled = true;
    try {
      const r = records.get(detailId);
      if (await Studio.purgeImage(r.project_id, button.dataset.purgeImage)) await showDetail(r.id);
    } catch (error) { Studio.showError(error); }
    finally { button.disabled = false; }
  };
  await load(true);
}
