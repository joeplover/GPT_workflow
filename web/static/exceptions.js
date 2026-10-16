export async function mount(root, Studio) {
  const $ = id => root.querySelector(`#${id}`), e = Studio.escape;
  let version = 0, detailVersion = 0, rows = [], cursor = "";
  const initialId = new URL(location.href).searchParams.get("request_id") || "";
  root.innerHTML = `<div class="filters"><select id="exceptionState" aria-label="处理状态"><option value="">全部状态</option><option value="false">未处理</option><option value="true">已处理</option></select><input id="exceptionCode" placeholder="异常类型，例如 UPSTREAM_TIMEOUT" aria-label="异常类型"><input id="exceptionId" placeholder="粘贴诊断编号" aria-label="诊断编号"><button id="exceptionSearch">查询</button><button id="exceptionRefresh">刷新</button></div><p id="exceptionCount" class="muted"></p><section id="exceptionTable" class="c-panel table-wrap">正在加载…</section><div class="section-actions"><button id="exceptionMore" hidden>加载更早记录</button></div><dialog id="exceptionDetails"><div class="page-head"><h2>异常详情</h2><button id="exceptionClose" type="button">关闭</button></div><div id="exceptionBody"></div></dialog>`;
  $("exceptionId").value = initialId;
  const date = value => new Date(value).toLocaleString("zh-CN");
  function render() {
    $("exceptionTable").innerHTML = rows.length ? `<table class="c-table"><thead><tr><th>时间 / 诊断编号</th><th>类型 / 提示</th><th>请求</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(row => `<tr><td>${e(date(row.created_at))}<br><span class="exception-reference">${e(row.id)}</span></td><td class="exception-message"><span class="tag">${e(row.code)}</span><p>${e(row.message)}</p></td><td>${e(row.method)} · ${row.status_code}<br>${e(row.path)}</td><td>${row.resolved ? "已处理" : "未处理"}</td><td><div class="card-actions"><button data-exception-detail="${e(row.id)}">查看详情</button><button data-exception-resolve="${e(row.id)}">${row.resolved ? "重新打开" : "标记已处理"}</button></div></td></tr>`).join("")}</tbody></table>` : '<div class="empty">暂无符合条件的异常。</div>';
  }
  async function load(reset = true) {
    const current = ++version;
    if (reset) { cursor = ""; rows = []; }
    const params = new URLSearchParams({limit:"30"});
    for (const [key, value] of [["resolved", $("exceptionState").value], ["code", $("exceptionCode").value.trim()], ["request_id", $("exceptionId").value.trim()], ["before", cursor]]) if (value) params.set(key, value);
    $("exceptionMore").disabled = true;
    try {
      const page = await Studio.api(`/photodream/api/admin/exceptions?${params}`);
      if (current !== version || !root.isConnected) return;
      rows.push(...page.items); render(); cursor = page.next_before || "";
      $("exceptionMore").hidden = !cursor;
      $("exceptionCount").textContent = `未处理 ${page.unresolved} 条 · 当前已加载 ${rows.length} 条`;
    } catch (error) { if (current === version && root.isConnected) { Studio.showError(error); $("exceptionTable").textContent = "异常记录加载失败，请重试。"; } }
    finally { if (current === version && root.isConnected) $("exceptionMore").disabled = false; }
  }
  async function detail(id) {
    const current = ++detailVersion;
    try {
      const row = await Studio.api(`/photodream/api/admin/exceptions/${encodeURIComponent(id)}`);
      if (!root.isConnected || current !== detailVersion) return;
      $("exceptionBody").innerHTML = `<p>${e(row.message)}</p><p class="muted">${e(row.code)} · ${e(date(row.created_at))} · ${row.resolved ? "已处理" : "未处理"}</p><p>诊断编号：<span class="exception-reference">${e(row.id)}</span></p><p>请求：${e(row.method)} ${e(row.path)} · HTTP ${row.status_code}</p><p class="muted">操作用户 ID：${e(row.actor_id || "未登录")}<br>处理时间：${row.resolved_at ? e(date(row.resolved_at)) : "—"}</p><h3>脱敏诊断详情</h3><pre>${e(row.diagnostic)}</pre>`;
      if (!$("exceptionDetails").open) $("exceptionDetails").showModal();
    } catch (error) { Studio.showError(error); }
  }
  $("exceptionTable").onclick = async event => {
    const button = event.target.closest("button"); if (!button) return;
    if (button.dataset.exceptionDetail) { await detail(button.dataset.exceptionDetail); return; }
    const id = button.dataset.exceptionResolve, row = rows.find(r => r.id === id); if (!row) return;
    button.disabled = true;
    try { await Studio.post(`/photodream/api/admin/exceptions/${encodeURIComponent(id)}/resolution`, {resolved:!row.resolved}); await load(); }
    catch (error) { Studio.showError(error); }
    finally { button.disabled = false; }
  };
  $("exceptionClose").onclick = () => { detailVersion++; $("exceptionDetails").close(); };
  $("exceptionSearch").onclick = () => load(); $("exceptionRefresh").onclick = () => load(); $("exceptionMore").onclick = () => load(false);
  $("exceptionState").onchange = () => load();
  for (const id of ["exceptionCode", "exceptionId"]) $(id).onkeydown = event => { if (event.key === "Enter") load(); };
  await load();
}
