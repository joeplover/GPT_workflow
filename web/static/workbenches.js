"use strict";
const prepared = new Map(), mounted = new Map();
const templates = {single:"index",storyboard:"storyboard"};
function prepare(name) {
  if (!prepared.has(name)) {
    const promise = Promise.all([
      fetch(`/static/${templates[name]}.html`).then(r => { if (!r.ok) throw new Error("工作台页面加载失败"); return r.text(); }),
      fetch(`/static/${name === "single" ? "styles" : "storyboard"}.css`).then(r => { if (!r.ok) throw new Error("工作台样式加载失败"); return r.text(); }),
      fetch("/static/workbench-theme.css").then(r => { if (!r.ok) throw new Error("工作台样式加载失败"); return r.text(); }),
      import(name === "single" ? "/static/app.js" : "/static/storyboard.js"),
      import("/static/image-settings.js"),
    ]);
    prepared.set(name,promise);
    promise.catch(() => { if (prepared.get(name) === promise) prepared.delete(name); });
  }
  return prepared.get(name);
}
export function preload() { for (const name of Object.keys(templates)) prepare(name).catch(() => {}); }
export async function open(name, projectId, Studio) {
  let record = mounted.get(name);
  if (!record) {
    const [html,css,theme,module,settings] = await prepare(name);
    if (mounted.has(name)) return open(name,projectId,Studio);
    const host = document.createElement("studio-workbench"); host.dataset.workbench = name; host.hidden = true;
    const root = host.attachShadow({mode:"open"});
    const style = document.createElement("style");
    style.textContent = css.replace(/:root\b/g,":host").replace(/\bbody\b/g,".workbench-body") + "\n" + theme;
    const template = new DOMParser().parseFromString(html,"text/html");
    template.querySelectorAll("script").forEach(script => script.remove());
    const body = document.createElement("div"); body.className = `workbench-body ${name}-workbench`; body.append(...template.body.childNodes);
    root.append(style,body); document.getElementById("workbenchViews").append(host);
    const controller = module.mount(root,Studio,settings.createImageSettings(root),projectId);
    record = {host,controller,scroll:0}; mounted.set(name,record);
  }
  try { await record.controller.ready; }
  catch (error) { record.controller.dispose(); record.host.remove(); mounted.delete(name); throw error; }
  return record;
}
export function hideAll() {
  for (const record of mounted.values()) {
    if (!record.host.hidden) record.scroll = document.getElementById("workspaceMain").scrollTop;
    record.host.shadowRoot.querySelectorAll("dialog[open]").forEach(dialog => dialog.close());
    record.host.hidden = true; record.controller.deactivate();
  }
}
