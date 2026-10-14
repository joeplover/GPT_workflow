export async function mount(root, Studio, projects) {
  const e = Studio.escape, $ = id => root.querySelector(`#${id}`);
  const initial = new URL(location.href).searchParams;
  const statusNames = {generating:"生成中", succeeded:"已完成", failed:"失败", interrupted:"已中断"};
  let cursor = null, version = 0, records = new Map();
  root.innerHTML = `<header class="page-head"><div><div class="eyebrow">PHOTO DREAM / HISTORY</div><h1>生成日志</h1><p>保存每次生成的图片、完整提示词和参考设定。重新生成或删除镜头后仍可追溯。</p></div><button id="journalRefresh">刷新记录</button></header>
    <div class="filters"><select id="journalProject"><option value="">全部可访问项目</option>${projects.map(p => `<option value="${e(p.id)}">${e(p.name)}</option>`).join("")}</select><select id="journalStatus"><option value="">全部状态</option>${Object.entries(statusNames).map(([key, value]) => `<option value="${key}">${value}</option>`).join("")}</select><input id="journalFrame" placeholder="镜头 ID（可选）" aria-label="镜头 ID"></div>
    <p id="journalError" class="form-error" role="alert"></p><div id="journalGrid" class="resource-grid"></div><button id="journalMore" hidden>加载更早记录</button>
    <dialog id="journalDetails"><div class="page-head"><h2>生成详情</h2><button id="journalClose" type="button">关闭</button></div><div id="journalDetailBody"></div></dialog>`;
  if (projects.some(p => p.id === initial.get("project"))) $("journalProject").value = initial.get("project");
  $("journalFrame").value = initial.get("frame") || "";
  const formatTime = time => time ? new Date(time).toLocaleString("zh-CN") : "—";
  function card(r) {
    const q = r.request;
    return `<article class="c-panel generation-card">${r.image_url ? `<a href="${e(Studio.asset(r.image_url))}" target="_blank" rel="noopener"><img class="resource-image" src="${e(Studio.asset(r.image_url))}" alt="生成图片 ${r.id}" loading="lazy"></a>` : '<div class="resource-image generation-placeholder">暂无图片</div>'}<span class="tag">${statusNames[r.status] || e(r.status)}${r.historical ? " · 历史补录" : ""}</span><h3>${e(r.project_name)}${q.frame_title ? ` / ${e(q.frame_title)}` : ""}</h3><div class="card-meta">#${r.id} · ${formatTime(r.created_at)}<br>${e(q.model || "未知模型")} · ${e(q.size || "未知尺寸")} · ${e(q.actor_name || "历史操作人未知")}</div><p class="journal-prompt">${e(q.prompt || "历史提示词未记录")}</p>${r.error ? `<p class="form-error">${e(r.error)}</p>` : ""}<div class="card-actions"><button data-generation="${r.id}">查看完整记录</button>${r.image_url ? `<a class="c-button" href="${e(Studio.downloadUrl(r.image_url))}" download>下载图片</a>` : ""}</div></article>`;
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
      $("journalGrid").innerHTML = [...records.values()].map(card).join("") || '<div class="empty">暂无生成记录。</div>';
      cursor = page.next_before; $("journalMore").hidden = !cursor;
    } catch (error) { if (current === version && $("journalError")) $("journalError").textContent = error.message; }
    finally { if (current === version && $("journalMore")) $("journalMore").disabled = false; }
  }
  $("journalRefresh").onclick = () => load(true); $("journalMore").onclick = () => load();
  for (const id of ["journalProject", "journalStatus", "journalFrame"]) $(id).onchange = () => load(true);
  $("journalClose").onclick = () => $("journalDetails").close();
  $("journalGrid").onclick = event => {
    const button = event.target.closest("[data-generation]"); if (!button) return;
    const r = records.get(Number(button.dataset.generation)), q = r.request;
    const field = (label, text) => `<label>${label}<textarea readonly rows="4">${e(text || "（未记录）")}</textarea></label>`;
    const refs = [...new Set([q.source_image, q.original_source_image, q.mask_image, ...(q.reference_images || []), ...[...(q.character_cards || []), ...(q.scene_cards || [])].map(c => c.image_url)].filter(Boolean))];
    $("journalDetailBody").innerHTML = `<div class="c-form"><p>记录 #${r.id} · ${statusNames[r.status]} · ${formatTime(r.created_at)}<br>项目：${e(r.project_name)}<br>镜头 ID：${e(r.frame_id || "单图工作台")}<br>模型：${e(q.model || "未知")} / 分辨率：${e(q.size || "未知")}<br>完成时间：${formatTime(r.finished_at)}</p>${r.historical ? '<p class="muted">由现有历史数据补录，部分字段可能缺失。参考卡未记录当时快照时不会使用当前设定替代。</p>' : ""}${field("系统提示词", q.system_prompt)}${field("用户提示词", q.prompt)}${field("实际发送的完整提示词", q.composed_prompt)}${field("当时的角色卡与场景卡快照", JSON.stringify({character_cards:q.character_cards || [], scene_cards:q.scene_cards || []}, null, 2))}${field("完整记录（可复制保存）", JSON.stringify(r, null, 2))}<div class="journal-references">${refs.map(url => `<a href="${e(Studio.asset(url))}" target="_blank" rel="noopener"><img src="${e(Studio.asset(url))}" alt="当时参考图片"><small>${e(url)}</small></a>`).join("")}</div>${r.image_url ? `<a class="c-button primary" href="${e(Studio.downloadUrl(r.image_url))}" download>下载这次生成的图片</a>` : ""}${r.error ? `<p class="form-error">${e(r.error)}</p>` : ""}</div>`;
    $("journalDetails").showModal();
  };
  await load(true);
}
