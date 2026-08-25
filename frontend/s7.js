function saveFieldValues(storageKey, fieldIds) {
  const values = {};
  for (const id of fieldIds) {
    const el = document.getElementById(id);
    if (el) values[id] = el.value;
  }
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(values));
  } catch (err) {
    // localStorage unavailable -- fields just won't persist
  }
}

function loadFieldValues(storageKey, fieldIds) {
  let saved = {};
  try {
    saved = JSON.parse(window.localStorage.getItem(storageKey)) || {};
  } catch (err) {
    saved = {};
  }
  for (const id of fieldIds) {
    const el = document.getElementById(id);
    if (el && saved[id] !== undefined) el.value = saved[id];
  }
}

function renderResult(result) {
  const resultEl = document.getElementById("s7-result");
  const statusEl = document.getElementById("s7-status");

  if (!result.ok) {
    resultEl.hidden = true;
    statusEl.textContent = result.message || "Identification failed.";
    return;
  }

  statusEl.textContent = "";
  resultEl.hidden = false;
  document.getElementById("s7-connected").textContent = result.connected ? "yes" : "no";
  document.getElementById("s7-module").textContent = result.module || "-";
  document.getElementById("s7-basic-hardware").textContent = result.basic_hardware || "-";
  document.getElementById("s7-version").textContent = result.version || "-";
  document.getElementById("s7-system-name").textContent = result.system_name || "-";
  document.getElementById("s7-module-type").textContent = result.module_type || "-";
  document.getElementById("s7-serial-number").textContent = result.serial_number || "-";
  document.getElementById("s7-plant-id").textContent = result.plant_identification || "-";
  document.getElementById("s7-copyright").textContent = result.copyright || "-";
  document.getElementById("s7-response-time").textContent =
    result.response_time_ms != null ? `${result.response_time_ms} ms` : "-";

  const warningEl = document.getElementById("s7-partial-warning");
  const errors = [result.module_identification_error, result.component_identification_error]
    .filter(Boolean);
  if (errors.length > 0) {
    warningEl.hidden = false;
    warningEl.textContent = `Some fields unavailable: ${errors.join("; ")}`;
  } else {
    warningEl.hidden = true;
  }
}

async function runIdentify(event) {
  event.preventDefault();
  const host = document.getElementById("s7-host").value.trim();
  const port = document.getElementById("s7-port").value.trim();
  if (!host) return;
  saveFieldValues("lanpi-s7-config", ["s7-host", "s7-port"]);

  const btn = document.getElementById("s7-identify-btn");
  const statusEl = document.getElementById("s7-status");
  const resultEl = document.getElementById("s7-result");

  btn.disabled = true;
  btn.textContent = "Identifying...";
  resultEl.hidden = true;
  statusEl.textContent = "connecting...";

  try {
    const res = await fetch("/api/tools/s7/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port: port ? parseInt(port, 10) : 102 }),
    });
    const result = await res.json();
    renderResult(result);
  } catch (err) {
    statusEl.textContent = "Identification failed.";
    resultEl.hidden = true;
  } finally {
    btn.disabled = false;
    btn.textContent = "Identify";
  }
}

function updateFooter() {
  const yearEl = document.getElementById("footer-year");
  const dateEl = document.getElementById("footer-date");
  const now = new Date();

  yearEl.textContent = now.getFullYear();
  dateEl.textContent = now.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

document.getElementById("s7-form").addEventListener("submit", runIdentify);

loadFieldValues("lanpi-s7-config", ["s7-host", "s7-port"]);
updateFooter();
setInterval(updateFooter, 60000);
