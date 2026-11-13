const serviceRoot = document.getElementById("services");
const globalStatus = document.getElementById("globalStatus");
const labels = {running:"Running",starting:"Starting",stopped:"Stopped",error:"Error"};
let busy = false;

async function api(path) {
  const response = await fetch(path, {method: path === "/api/status" ? "GET" : "POST"});
  const data = await response.json();
  if (!response.ok && !data.message) throw new Error("Request failed");
  return data;
}
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function render(services) {
  serviceRoot.innerHTML = services.map(s => {
    const active = s.state === "running" || s.state === "starting";
    const buttonLabel = active ? "Stop" : "Start";
    const disabled = busy || (active && !s.managed);
    const detail = s.error || s.detail;
    return `<article class="service">
      <div class="service-top">
        <div><h2>${esc(s.label)}</h2><div class="port">Port ${s.port}</div></div>
        <span class="state ${esc(s.state)}">${labels[s.state] || esc(s.state)}</span>
      </div>
      <p class="detail">${esc(detail)}</p>
      <div class="service-bottom">
        <span class="managed">${s.managed ? "Managed here" : (active ? "External process" : "Local service")}</span>
        <button class="${active ? "stop" : "primary"}" data-action="${active ? "stop" : "start"}" data-id="${esc(s.id)}" ${disabled ? "disabled" : ""}>${buttonLabel}</button>
      </div>
    </article>`;
  }).join("");
}
async function refresh() {
  try {
    const data = await api("/api/status");
    render(data.services);
    const running = data.services.filter(s => s.state === "running").length;
    globalStatus.textContent = `${running} of 4 services running`;
  } catch (e) {
    globalStatus.textContent = "Control service unavailable";
  }
}
async function runAction(path, successText) {
  if (busy) return;
  busy = true;
  globalStatus.textContent = successText;
  await refresh();
  try {
    const data = await api(path);
    if (data.results) {
      const failures = data.results.filter(x => !x.success);
      globalStatus.textContent = failures.length ? `${failures.length} service action(s) need attention` : "All requested actions completed";
    } else {
      globalStatus.textContent = data.message || (data.success ? "Action successful" : "Action failed");
    }
  } catch (e) {
    globalStatus.textContent = `Error: ${e.message}`;
  } finally {
    busy = false;
    await refresh();
  }
}
serviceRoot.addEventListener("click", e => {
  const button = e.target.closest("button[data-action]");
  if (!button) return;
  runAction(`/api/${button.dataset.action}/${button.dataset.id}`, `${button.dataset.action}ing ${button.dataset.id}…`);
});
document.getElementById("startAll").addEventListener("click", () => runAction("/api/start-all","Starting services…"));
document.getElementById("stopAll").addEventListener("click", () => runAction("/api/stop-all","Stopping managed services…"));
refresh();
setInterval(refresh, 2000);
