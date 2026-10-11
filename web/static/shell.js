"use strict";
window.Studio = (() => {
  let user, currentProject = new URL(location.href).searchParams.get("project") || "";
  const listeners = new Set(), cache = new Map();
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    const url = new URL(String(args[0]?.url || args[0]), location.href);
    if (response.status === 401 && url.origin === location.origin && url.pathname.startsWith("/api/") && !url.pathname.startsWith("/api/auth/") && location.pathname !== "/login") {
      location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash));
    }
    const method = (args[1]?.method || args[0]?.method || "GET").toUpperCase();
    if (response.ok && method !== "GET" && url.pathname.startsWith("/api/projects")) {
      cache.delete("/api/projects"); cache.delete("/api/resources");
    }
    return response;
  };
  function escape(value) { return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])); }
  async function api(url, options = {}) {
    const response = await fetch(url, options), body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "提交内容不正确，请检查填写项。");
    return body;
  }
  function cached(url, ttl = 3000) {
    const entry = cache.get(url);
    if (entry && entry.until > Date.now()) return entry.promise.then(value => structuredClone(value));
    const promise = api(url), next = {promise, until:Date.now() + ttl}; cache.set(url,next);
    promise.catch(() => { if (cache.get(url) === next) cache.delete(url); });
    return promise.then(value => structuredClone(value));
  }
  function post(url, body) { return api(url, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); }
  function route() { return location.pathname === "/single" ? "single" : location.pathname === "/storyboard" ? "storyboard" : location.hash.slice(1) || "overview"; }
  function highlight(name = route()) {
    document.querySelectorAll(".studio-links a,.console-side a").forEach(a => {
      const url = new URL(a.href), target = url.pathname === "/" ? url.hash.slice(1) || "overview" : url.pathname.slice(1);
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
    if (["/single","/storyboard"].includes(url.pathname) && !url.searchParams.has("project") && currentProject) url.searchParams.set("project",currentProject);
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
    if (url.origin !== location.origin || !["/","/single","/storyboard"].includes(url.pathname)) return;
    event.preventDefault(); navigate(url);
  });
  window.addEventListener("popstate", notifyRoute); window.addEventListener("hashchange", notifyRoute);
  const ready = api("/api/auth/me").then(me => {
    user = me; document.documentElement.dataset.userId = me.id;
    const bar = document.createElement("nav"); bar.className = "studio-nav"; bar.setAttribute("aria-label","系统导航");
    bar.innerHTML = `<a class="studio-logo" href="/">▥ <strong>Photo Dream</strong><span>创作中台</span></a><div class="studio-links"><a href="/#overview">总览</a><a href="/#projects">项目管理</a><a href="/single">单图工作台</a><a href="/storyboard">分镜工作台</a><a href="/#resources">资源中心</a><a href="/#teams">团队</a></div><div class="studio-account"><a href="/#account">${escape(me.display_name)}</a><button id="studioLogout" type="button">退出</button></div>`;
    if (me.is_admin) { const a = document.createElement("a"); a.href="/#users"; a.textContent="用户管理"; bar.querySelector(".studio-links").append(a); }
    document.body.prepend(bar);
    new ResizeObserver(() => document.documentElement.style.setProperty("--studio-nav-height",`${bar.offsetHeight}px`)).observe(bar);
    bar.querySelector("#studioLogout").addEventListener("click",async () => {
      if (!confirm("确认退出登录？请先确认当前草稿已同步。")) return;
      await post("/api/auth/logout",{}); location.assign("/login");
    });
    return me;
  }).catch(error => {
    if (error.message === "请先登录。") location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash));
    const view = document.getElementById("consoleView"); if (view) view.textContent = "工作空间加载失败，请刷新重试。";
    throw error;
  });
  return {ready,api,post,cached,escape,route,navigate,projectSelected,highlight,
    onRoute(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    get projectId() { return currentProject; }, get user() { return user; }};
})();
