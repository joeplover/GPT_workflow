"use strict";
window.StudioErrors = (() => {
  const reported = new WeakSet();
  let dialog, list, incidents = [];
  function summary(error) {
    const value = typeof error === "string" ? error : String(error?.message || "");
    if (error?.name === "TimeoutError" || error?.name === "AbortError") return "请求超时，请稍后重试。";
    if (!value || /Failed to fetch|NetworkError|Load failed|fetch failed/i.test(value)) return "网络连接异常，请检查网络后重试。";
    if (/timeout|timed out/i.test(value)) return "请求超时，请稍后重试。";
    if (/upstream_status=(401|403)/.test(value)) return "生图接口认证失败，请检查 API Key 和访问权限。";
    if (/quota|insufficient|balance|credit/i.test(value)) return "生图接口额度不足，请检查账户余额。";
    if (value.length > 160 || /https?:\/\/|<[^>]+>|Traceback|\n|pdenc:v1:|Bearer\s|(?:api[_-]?key|password|token|secret)["']?\s*[:=]/i.test(value) || !/[\u4e00-\u9fff]/.test(value)) return "操作失败，请稍后重试；持续失败时请联系管理员。";
    return value;
  }
  class RequestError extends Error {
    constructor(message, {code = "REQUEST_ERROR", request_id = "", status = 0} = {}) {
      super(summary(message)); this.name = "StudioRequestError";
      this.code = code; this.requestId = request_id; this.status = status;
    }
  }
  async function readResponse(response) {
    let body;
    try { body = await response.json(); }
    catch {
      throw new RequestError(response.status === 504 ? "请求超时，请稍后重试。" : "服务返回异常，请稍后重试。", {code:"INVALID_RESPONSE", status:response.status, request_id:response.headers.get("X-Request-ID") || ""});
    }
    if (!response.ok) {
      const info = body?.error || {};
      throw new RequestError(info.message || (typeof body?.detail === "string" ? body.detail : "请检查填写内容后重试。"), {...info, status:response.status, request_id:info.request_id || response.headers.get("X-Request-ID") || ""});
    }
    return body;
  }
  async function request(url, options) {
    let response;
    try { response = await fetch(url, options); }
    catch (error) { throw new RequestError(summary(error), {code:error.name === "TimeoutError" || error.name === "AbortError" ? "CLIENT_TIMEOUT" : "NETWORK_ERROR"}); }
    return readResponse(response);
  }
  function setup() {
    if (dialog) return;
    dialog = document.createElement("dialog"); dialog.id = "systemErrorDialog"; dialog.className = "system-error-dialog";
    dialog.setAttribute("aria-labelledby", "systemErrorTitle"); dialog.setAttribute("aria-describedby", "systemErrorHint");
    dialog.innerHTML = '<div class="error-heading"><span class="error-symbol" aria-hidden="true">!</span><h2 id="systemErrorTitle">操作未完成</h2></div><p id="systemErrorHint" class="muted">请根据提示处理后重试。</p><ul id="systemErrorList" class="system-error-list" aria-live="polite"></ul><details class="error-diagnostics"><summary>查看诊断信息</summary><pre id="systemErrorDiagnostics"></pre><button type="button" id="copyErrorDiagnostics">复制诊断信息</button><span id="copyErrorStatus" class="muted" role="status"></span></details><div class="error-actions"><button class="primary" type="button" id="dismissSystemError" autofocus>知道了</button></div>';
    document.body.append(dialog); list = dialog.querySelector("#systemErrorList");
    dialog.querySelector("#dismissSystemError").onclick = () => dialog.close();
    dialog.querySelector("#copyErrorDiagnostics").onclick = async () => {
      const status = dialog.querySelector("#copyErrorStatus");
      try { await navigator.clipboard.writeText(dialog.querySelector("pre").textContent); status.textContent = "已复制"; }
      catch { status.textContent = "无法自动复制，请选中文字复制。"; }
    };
  }
  function show(error, {context = ""} = {}) {
    if (error && typeof error === "object") { if (reported.has(error)) return summary(error); reported.add(error); }
    const message = summary(error), code = error?.code || "CLIENT_ERROR", key = `${code}:${message}`;
    setup();
    if (!dialog.open) { incidents = []; dialog.querySelector("details").open = false; dialog.querySelector("#copyErrorStatus").textContent = ""; }
    let incident = incidents.find(item => item.key === key);
    if (!incident) { incident = {key, message, code, count:0, contexts:new Set(), ids:new Set()}; incidents.push(incident); }
    incident.count++;
    if (context) incident.contexts.add(String(context).slice(0, 60));
    if (error?.requestId) incident.ids.add(error.requestId);
    list.replaceChildren();
    for (const item of incidents.slice(0, 5)) {
      const li = document.createElement("li"), p = document.createElement("p"), note = document.createElement("small");
      p.textContent = item.message; note.textContent = [item.count > 1 ? `${item.count} 次操作遇到同类问题` : "", ...[...item.contexts].slice(0, 2)].filter(Boolean).join(" · ");
      li.append(p); if (note.textContent) li.append(note); list.append(li);
    }
    if (incidents.length > 5) { const li = document.createElement("li"); li.textContent = `另有 ${incidents.length - 5} 类异常，诊断信息已合并。`; list.append(li); }
    dialog.querySelector("pre").textContent = incidents.map(item => `${item.code} · ${item.message}\n${[...item.ids].map(id => `诊断编号：${id}`).join("\n") || "未获得服务端诊断编号"}`).join("\n\n");
    if (!dialog.open) dialog.showModal();
    return message;
  }
  window.addEventListener("unhandledrejection", event => { event.preventDefault(); show(event.reason); });
  window.addEventListener("error", event => { if (event.error) { event.preventDefault(); show("页面运行异常，请刷新页面后重试。"); } });
  return {show, summary, request, readResponse, RequestError};
})();
