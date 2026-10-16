const endpoint = "/photodream/api/settings/api-profiles";
const changedEvent = "photodream:api-profiles";
function changed() { window.dispatchEvent(new Event(changedEvent)); }

export function mountSelector(root, Studio, signal) {
  const select = root.getElementById("apiProfileSelect"), info = root.getElementById("apiProfileInfo");
  let config, loaded = false, saving = false, version = 0;
  async function refresh(settings) {
    if (settings) config = settings;
    const requestVersion = ++version;
    try {
      const data = await Studio.api(endpoint);
      if (signal.aborted || requestVersion !== version) return;
      select.innerHTML = '<option value="">服务端默认配置</option>' + data.profiles.map(p => `<option value="${Studio.escape(p.id)}">${Studio.escape(p.name)}</option>`).join("");
      select.value = data.selected_id || "";
      const selected = data.profiles.find(p => p.id === select.value);
      info.textContent = selected ? selected.base_url : config?.has_api_key ? `服务端默认 · ${config.base_url}` : "请先在设置中添加 URL + Key 配置";
      loaded = true;
    } catch (error) {
      loaded = false; info.textContent = "接口配置加载失败，请重试。";
      throw error;
    }
  }
  select.addEventListener("change", async () => {
    saving = true; select.disabled = true;
    try {
      await Studio.post(endpoint + "/selection", {profile_id:select.value});
      await refresh(); changed();
    } catch (error) { Studio.showError(error); info.textContent = "接口选择未保存，请重试。"; loaded = false; }
    finally { saving = false; select.disabled = false; }
  }, {signal});
  root.getElementById("manageApiProfiles").addEventListener("click", event => {
    event.preventDefault(); Studio.navigate("/photodream/#account");
  }, {signal});
  window.addEventListener(changedEvent, () => refresh().catch(error => Studio.showError(error)), {signal});
  return {
    refresh,
    selection() {
      if (saving) throw new Error("正在保存接口选择，请稍后重试。");
      if (!loaded) throw new Error("接口配置尚未加载，请重新打开工作台。");
      if (!select.value && !config?.has_api_key) throw new Error("请先在设置中添加并选择接口配置。");
      return select.value;
    },
  };
}

export async function mountSettings(root, Studio) {
  const e = Studio.escape;
  let profiles = [], selectedId = "", editing = "";
  root.innerHTML = `<div class="page-head"><div><h2>接口配置</h2><p>URL + API Key 成组保存，可命名并在两个工作台中选择。配置仅自己可用，团队成员无法查看。</p></div><button type="button" id="newApiProfile">＋ 新增配置</button></div>
    <p id="apiProfileNotice" class="muted" role="status"></p>
    <form id="apiProfileForm" class="c-form" hidden>
      <h3 id="apiProfileFormTitle">新增接口配置</h3>
      <label>配置名称<input name="name" required maxlength="100" placeholder="例如：主力生图接口" autocomplete="off"></label>
      <label>接口 URL<input name="base_url" type="url" required maxlength="2048" placeholder="https://api.example.com/v1" autocomplete="off"></label>
      <label>API Key<input name="api_key" type="password" required maxlength="16384" placeholder="填写此接口对应的 Key" autocomplete="new-password"></label>
      <p class="muted">已保存的 Key 不回显。编辑时留空保留原 Key；修改 URL 时需同时填写 Key。</p>
      <div class="card-actions"><button type="submit" class="primary">保存配置</button><button type="button" id="cancelApiProfile">取消</button></div>
    </form><div id="apiProfileList" class="table-wrap">加载中…</div>`;
  const $ = id => root.querySelector(`#${id}`), form = $("apiProfileForm");
  function notice(text) { $("apiProfileNotice").textContent = text; }
  function closeEditor() { form.reset(); form.hidden = true; editing = ""; }
  function editor(profile) {
    editing = profile?.id || ""; form.reset(); form.hidden = false;
    $("apiProfileFormTitle").textContent = editing ? "编辑接口配置" : "新增接口配置";
    form.elements.name.value = profile?.name || "";
    form.elements.base_url.value = profile?.base_url || "";
    form.elements.api_key.required = !editing;
    form.elements.api_key.placeholder = editing ? "留空保留原 Key" : "填写此接口对应的 Key";
    form.elements.name.focus();
  }
  async function load() {
    const data = await Studio.api(endpoint);
    if (!root.isConnected) return;
    profiles = data.profiles; selectedId = data.selected_id || "";
    $("apiProfileList").innerHTML = `<table class="c-table"><thead><tr><th>名称</th><th>接口 URL</th><th>Key</th><th>操作</th></tr></thead><tbody>${profiles.map(p => `<tr><td>${e(p.name)}${p.id === selectedId ? ' <span class="tag">当前使用</span>' : ""}</td><td class="api-profile-url">${e(p.base_url)}</td><td>已保存</td><td><div class="card-actions">${p.id !== selectedId ? `<button type="button" data-profile-action="select" data-profile-id="${e(p.id)}">使用此配置</button>` : ""}<button type="button" data-profile-action="edit" data-profile-id="${e(p.id)}">编辑</button><button type="button" class="danger" data-profile-action="delete" data-profile-id="${e(p.id)}">删除</button></div></td></tr>`).join("")}</tbody></table>${!profiles.length ? '<p class="muted">尚未添加接口配置，点击「新增配置」开始。</p>' : ""}<div class="card-actions api-profile-default"><span class="muted">${selectedId ? "也可使用服务器环境变量中的默认接口。" : "当前选择：服务端默认配置（需服务器已配置 Key）。"}</span>${selectedId ? '<button type="button" data-profile-action="select" data-profile-id="">使用服务端默认</button>' : ""}</div>`;
  }
  $("newApiProfile").onclick = () => editor();
  $("cancelApiProfile").onclick = closeEditor;
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]'); button.disabled = true;
    try {
      await Studio.post(endpoint + (editing ? `/${editing}` : ""), Object.fromEntries(new FormData(form)));
      closeEditor(); await load(); changed(); notice("接口配置已保存。");
    } catch (error) { Studio.showError(error); }
    finally { button.disabled = false; }
  };
  $("apiProfileList").onclick = async event => {
    const button = event.target.closest("[data-profile-action]"); if (!button) return;
    const {profileAction: action, profileId: id} = button.dataset;
    if (action === "edit") { editor(profiles.find(p => p.id === id)); return; }
    if (action === "delete" && !confirm("删除此接口配置？已生成的图片和日志会保留；排队中使用此配置的任务将无法继续生成。")) return;
    button.disabled = true;
    try {
      if (action === "delete") { await Studio.api(`${endpoint}/${id}`, {method:"DELETE"}); if (editing === id) closeEditor(); }
      else await Studio.post(endpoint + "/selection", {profile_id:id});
      await load(); changed(); notice(action === "delete" ? "接口配置已删除。" : "接口选择已保存，两个工作台均可使用。");
    } catch (error) { Studio.showError(error); }
    finally { button.disabled = false; }
  };
  try { await load(); } catch (error) { Studio.showError(error); $("apiProfileList").textContent = "接口配置加载失败，请重新打开设置。"; }
}
