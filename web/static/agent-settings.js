const endpoint = "/photodream/api/settings/agent";

export async function mountSettings(root, Studio) {
  root.innerHTML = `<div class="page-head"><div><h2>小梦 Agent 模型配置</h2><p>配置小梦使用的 OpenAI 兼容接口。每个用户独立保存，已保存的 Agent Key 不会回显。</p></div></div>
    <p id="agentSettingsNotice" class="muted" role="status"></p>
    <form id="agentSettingsForm" class="c-form" hidden>
      <label>Agent URL<input name="base_url" type="url" required maxlength="2048" placeholder="https://api.example.com/v1" autocomplete="off"></label>
      <label>Agent Key<input name="api_key" type="password" maxlength="16384" placeholder="填写 Agent 接口对应的 Key" autocomplete="new-password"></label>
      <label>Agent Model<input name="model" required maxlength="200" placeholder="例如：gpt-4o-mini" autocomplete="off"></label>
      <p class="muted">首次保存必须填写 Agent Key；编辑时留空表示保留原 Key。修改 URL 时需要重新填写 Key。</p>
      <div class="card-actions"><button type="submit" class="primary">保存 Agent 配置</button><button type="button" id="deleteAgentSettings" class="danger">删除配置</button></div>
    </form>`;
  const form = root.querySelector("#agentSettingsForm"), notice = root.querySelector("#agentSettingsNotice"), deleteButton = root.querySelector("#deleteAgentSettings");
  let configured = false;
  function busy(value) {
    for (const control of form.elements) control.disabled = value;
  }
  function apply(data) {
    configured = Boolean(data.configured);
    form.elements.base_url.value = data.base_url || "";
    form.elements.model.value = data.model || "";
    form.elements.api_key.value = "";
    form.elements.api_key.required = !configured;
    form.elements.api_key.placeholder = configured ? "留空保留已保存的 Agent Key" : "填写 Agent 接口对应的 Key";
    deleteButton.hidden = !configured;
    notice.textContent = configured
      ? `当前使用自定义 Agent · ${data.model} · Key 已加密保存`
      : data.has_api_key
        ? `尚未配置个人 Agent，服务器默认模型为 ${data.model}；保存后将启用个人模型调用。`
        : "尚未配置个人 Agent，请填写 URL、Key 和模型。";
    form.hidden = false;
  }
  async function load() {
    const data = await Studio.api(endpoint);
    if (root.isConnected) apply(data);
  }
  form.onsubmit = async event => {
    event.preventDefault();
    const body = JSON.stringify(Object.fromEntries(new FormData(form)));
    busy(true);
    try {
      apply(await Studio.api(endpoint, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body,
      }));
      notice.textContent = "小梦 Agent 配置已保存。";
    } catch (error) { Studio.showError(error); }
    finally { busy(false); }
  };
  deleteButton.onclick = async () => {
    if (!configured || !confirm("删除个人 Agent 配置？删除后将回退到服务器默认配置。")) return;
    busy(true);
    try { await Studio.api(endpoint, {method: "DELETE"}); await load(); }
    catch (error) { Studio.showError(error); }
    finally { busy(false); }
  };
  try { await load(); } catch (error) { Studio.showError(error); notice.textContent = "Agent 配置加载失败，请重新打开设置。"; }
}
