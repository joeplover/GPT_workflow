"use strict";
let registering = false;
const form = document.getElementById("authForm");
function tab(value) {
  registering = value;
  document.getElementById("displayLabel").hidden = !value;
  document.getElementById("authTitle").textContent = value ? "开始你的创作" : "欢迎回来";
  document.getElementById("authSubtitle").textContent = value ? "创建个人空间，也可以加入团队一起创作。" : "登录后继续你的创作。";
  document.getElementById("authSubmit").textContent = value ? "创建账号" : "登录";
  document.getElementById("loginTab").classList.toggle("active", !value);
  document.getElementById("registerTab").classList.toggle("active", value);
  form.elements.password.minLength = value ? 10 : 1;
  form.elements.password.autocomplete = value ? "new-password" : "current-password";
  document.getElementById("authError").textContent = "";
}
document.getElementById("loginTab").onclick = () => tab(false);
document.getElementById("registerTab").onclick = () => tab(true);
fetch("/api/auth/options").then(r => r.json()).then(options => { document.getElementById("registerTab").hidden = !options.registration_enabled; });
form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = document.getElementById("authSubmit"); button.disabled = true;
  try {
    const response = await fetch("/api/auth/" + (registering ? "register" : "login"), {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请检查填写内容。");
    let next = new URL(location.href).searchParams.get("next") || "/";
    if (!next.startsWith("/") || next.startsWith("//") || next.includes("\\") || /[\r\n]/.test(next)) next = "/";
    location.assign(next);
  } catch (error) { document.getElementById("authError").textContent = error.message; }
  finally { button.disabled = false; }
});
