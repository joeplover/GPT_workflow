"use strict";
window.Studio = (() => {
  let user, currentProject = new URL(location.href).searchParams.get("project") || "";
  const listeners = new Set(), cache = new Map();
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    const url = new URL(String(args[0]?.url || args[0]), location.href);
    if (response.status === 401 && url.origin === location.origin && url.pathname.startsWith("/photodream/api/") && !url.pathname.startsWith("/photodream/api/auth/") && location.pathname !== "/photodream/login") {
      location.replace("/photodream/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash));
    }
    const method = (args[1]?.method || args[0]?.method || "GET").toUpperCase();
    if (response.ok && method !== "GET" && url.pathname.startsWith("/photodream/api/projects")) {
      cache.delete("/photodream/api/projects"); cache.delete("/photodream/api/resources");
    }
    return response;
  };
  function escape(value) { return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])); }
  // Keep persisted asset paths stable while serving them within the application.
  function asset(url) { return /^\/(uploads|outputs)\//.test(url || "") ? "/photodream" + url : url; }
  function downloadUrl(url) { return asset(url) + "?download=true"; }
  function cardActions(card, type, disabled = false, variant = "console") {
    const classes = variant === "single" ? "btn btn--small" : variant === "storyboard" ? "button button--small secondary" : "c-button";
    const attrs = `data-card-id="${escape(card.id)}" data-card-kind="${escape(type)}" ${disabled ? "disabled" : ""}`;
    return `<div class="card-resource-actions${variant === "console" ? " card-actions" : ""}">${card.image_url ? `<a class="${classes}" href="${escape(downloadUrl(card.image_url))}" download>下载参考图</a><button class="${classes}" type="button" data-card-action="image" ${attrs}>移除图片</button>` : ""}<button class="${classes} danger" type="button" data-card-action="delete" ${attrs}>删除卡片</button></div>`;
  }
  async function removeCard(projectId, kind, id, imageOnly) {
    if (!confirm(imageOnly ? "移除这张参考图？卡片文字设定会保留，历史文件仍可追溯。" : "删除这张参考卡？关联镜头会取消选用，已生成的图片和日志会保留。")) return null;
    return api(`/photodream/api/projects/${projectId}/cards/${kind}/${id}${imageOnly ? "/image" : ""}`, {method:"DELETE"});
  }
  async function purgeImage(projectId, imageUrl) {
    if (!confirm("彻底删除这张图片？服务器文件和当前图片引用将被永久移除，无法恢复。生成日志中的提示词和原始路径会保留。")) return null;
    const result = await api(`/photodream/api/projects/${encodeURIComponent(projectId)}/assets`, {
      method: "DELETE", headers: {"Content-Type": "application/json"}, body: JSON.stringify({image_url: imageUrl})
    });
    projectSelected(result.project.id);
    return result;
  }
  async function api(url, options = {}) {
    return StudioErrors.request(url, options);
  }
  function cached(url, ttl = 3000) {
    const entry = cache.get(url);
    if (entry && entry.until > Date.now()) return entry.promise.then(value => structuredClone(value));
    const promise = api(url), next = {promise, until:Date.now() + ttl}; cache.set(url,next);
    promise.catch(() => { if (cache.get(url) === next) cache.delete(url); });
    return promise.then(value => structuredClone(value));
  }
  function post(url, body) { return api(url, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); }
  function route() { return location.pathname === "/photodream/single" ? "single" : location.pathname === "/photodream/storyboard" ? "storyboard" : location.hash.slice(1) || "overview"; }
  function highlight(name = route()) {
    document.querySelectorAll(".studio-links a,.console-side a").forEach(a => {
      const url = new URL(a.href), target = url.pathname === "/photodream/" ? url.hash.slice(1) || "overview" : url.pathname.slice("/photodream/".length);
      a.classList.toggle("active", target === name);
      if (target === name) a.setAttribute("aria-current","page"); else a.removeAttribute("aria-current");
    });
  }
  function notifyRoute() {
    const name = route(); highlight(name);
    for (const listener of listeners) listener({name, projectId:new URL(location.href).searchParams.get("project") || currentProject});
  }
  function navigate(target, replace = false) {
    const url = new URL(target,location.href);
    if (url.origin !== location.origin) { location.assign(url); return; }
    if (["/photodream/single","/photodream/storyboard"].includes(url.pathname) && !url.searchParams.has("project") && currentProject) url.searchParams.set("project",currentProject);
    if (url.href === location.href) return;
    history[replace ? "replaceState" : "pushState"](null,"",url); notifyRoute();
  }
  function projectSelected(id) {
    currentProject = id || "";
    if (["single","storyboard"].includes(route())) {
      const url = new URL(location.href); id ? url.searchParams.set("project",id) : url.searchParams.delete("project"); history.replaceState(null,"",url);
    }
  }
  document.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const anchor = event.composedPath().find(node => node instanceof HTMLAnchorElement);
    if (!anchor || anchor.target || anchor.hasAttribute("download")) return;
    const url = new URL(anchor.href);
    if (url.origin !== location.origin || !["/photodream/","/photodream/single","/photodream/storyboard"].includes(url.pathname)) return;
    event.preventDefault(); navigate(url);
  });
  window.addEventListener("popstate", notifyRoute); window.addEventListener("hashchange", notifyRoute);
  const ready = api("/photodream/api/auth/me").then(me => {
    user = me; document.documentElement.dataset.userId = me.id;
    const bar = document.createElement("nav"); bar.className = "studio-nav"; bar.setAttribute("aria-label","系统导航");
    bar.innerHTML = `<a class="studio-logo" href="/photodream/">▥ <strong>Photo Dream</strong><span>创作中台</span></a><div class="studio-links"><a href="/photodream/#overview">总览</a><a href="/photodream/#projects">项目管理</a><a href="/photodream/single">单图工作台</a><a href="/photodream/storyboard">分镜工作台</a><a href="/photodream/#resources">资源中心</a><a href="/photodream/#teams">团队</a></div><div class="studio-account"><a href="/photodream/#account">${escape(me.display_name)}</a><button id="studioLogout" type="button">退出</button></div>`;
    if (me.is_admin) { for (const [name, label] of [["users", "用户管理"], ["exceptions", "异常管理"]]) { const a = document.createElement("a"); a.href=`/photodream/#${name}`; a.textContent=label; bar.querySelector(".studio-links").append(a); } }
    document.body.prepend(bar);
    new ResizeObserver(() => document.documentElement.style.setProperty("--studio-nav-height",`${bar.offsetHeight}px`)).observe(bar);
    bar.querySelector("#studioLogout").addEventListener("click",async () => {
      if (!confirm("确认退出登录？请先确认当前草稿已同步。")) return;
      try { await post("/photodream/api/auth/logout",{}); location.assign("/photodream/login"); }
      catch (error) { StudioErrors.show(error); }
    });
    return me;
  }).catch(error => {
    if (error.status === 401) location.replace("/photodream/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash));
    const view = document.getElementById("consoleView"); if (view) view.textContent = "工作空间加载失败，请刷新重试。";
    throw error;
  });
  return {ready,api,post,cached,escape,asset,downloadUrl,cardActions,removeCard,purgeImage,route,navigate,projectSelected,highlight,
    showError:StudioErrors.show, errorSummary:StudioErrors.summary, readResponse:StudioErrors.readResponse,
    onRoute(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    get projectId() { return currentProject; }, get user() { return user; }};
})();
