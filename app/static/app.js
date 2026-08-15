const modeSelects = [...document.querySelectorAll(".source-mode-select")];
const dirtyStatus = document.querySelector("#source-mode-dirty");

function markModesChanged(message = "Wijzigingen nog niet opgeslagen") {
  if (dirtyStatus) dirtyStatus.textContent = message;
}

for (const button of document.querySelectorAll("[data-source-mode]")) {
  button.addEventListener("click", () => {
    for (const select of modeSelects) select.value = button.dataset.sourceMode;
    markModesChanged("Alle bronnen aangepast; sla de wijzigingen hieronder op");
  });
}

for (const select of modeSelects) {
  select.addEventListener("change", () => markModesChanged());
}

for (const form of document.querySelectorAll("form[data-confirm]")) {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
}

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js"));
}
