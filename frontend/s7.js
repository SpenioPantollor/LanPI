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

function updateReadFormVisibility() {
  const area = document.getElementById("s7-read-area").value;
  const type = document.getElementById("s7-read-type").value;
  const dbVisible = area === "DB";
  const bitVisible = type === "BIT";
  document.getElementById("s7-read-db-label").hidden = !dbVisible;
  document.getElementById("s7-read-db").hidden = !dbVisible;
  document.getElementById("s7-read-bit-label").hidden = !bitVisible;
  document.getElementById("s7-read-bit").hidden = !bitVisible;
}

function renderReadResult(result) {
  const resultEl = document.getElementById("s7-read-result");
  const statusEl = document.getElementById("s7-read-status");

  if (!result.ok) {
    resultEl.hidden = true;
    statusEl.textContent = result.message || "Read failed.";
    return;
  }

  statusEl.textContent = "";
  resultEl.hidden = false;
  document.getElementById("s7-read-value").textContent = String(result.value);
  document.getElementById("s7-read-raw").textContent = result.raw_hex || "-";
  document.getElementById("s7-read-response-time").textContent =
    result.response_time_ms != null ? `${result.response_time_ms} ms` : "-";
}

async function runReadTag(event) {
  event.preventDefault();
  const host = document.getElementById("s7-read-host").value.trim();
  const port = document.getElementById("s7-read-port").value.trim();
  const area = document.getElementById("s7-read-area").value;
  const dbNumber = document.getElementById("s7-read-db").value.trim();
  const byteOffset = document.getElementById("s7-read-offset").value.trim();
  const type = document.getElementById("s7-read-type").value;
  const bitOffset = document.getElementById("s7-read-bit").value.trim();
  if (!host || byteOffset === "") return;

  saveFieldValues("lanpi-s7-read-config", [
    "s7-read-host", "s7-read-port", "s7-read-area", "s7-read-db",
    "s7-read-offset", "s7-read-type", "s7-read-bit",
  ]);

  const btn = document.getElementById("s7-read-btn");
  const statusEl = document.getElementById("s7-read-status");
  const resultEl = document.getElementById("s7-read-result");

  btn.disabled = true;
  btn.textContent = "Reading...";
  resultEl.hidden = true;
  statusEl.textContent = "reading...";

  try {
    const res = await fetch("/api/tools/s7/read-tag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host,
        port: port ? parseInt(port, 10) : 102,
        area,
        db_number: dbNumber ? parseInt(dbNumber, 10) : 0,
        byte_offset: parseInt(byteOffset, 10),
        type,
        bit_offset: bitOffset ? parseInt(bitOffset, 10) : 0,
      }),
    });
    const result = await res.json();
    renderReadResult(result);
  } catch (err) {
    statusEl.textContent = "Read failed.";
    resultEl.hidden = true;
  } finally {
    btn.disabled = false;
    btn.textContent = "Read";
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
document.getElementById("s7-read-form").addEventListener("submit", runReadTag);
document.getElementById("s7-read-area").addEventListener("change", updateReadFormVisibility);
document.getElementById("s7-read-type").addEventListener("change", updateReadFormVisibility);

loadFieldValues("lanpi-s7-config", ["s7-host", "s7-port"]);
loadFieldValues("lanpi-s7-read-config", [
  "s7-read-host", "s7-read-port", "s7-read-area", "s7-read-db",
  "s7-read-offset", "s7-read-type", "s7-read-bit",
]);
updateReadFormVisibility();
updateFooter();
setInterval(updateFooter, 60000);
