function formatModbusValue(value, functionCode) {
  if (functionCode === "1" || functionCode === "2" || functionCode === 1 || functionCode === 2) {
    return value ? "ON" : "OFF";
  }
  const hex = value.toString(16).toUpperCase().padStart(4, "0");
  return `${value} (0x${hex})`;
}

function formatBytesHex(hex) {
  if (!hex) return "-";
  return hex.toUpperCase().match(/.{1,2}/g).join(" ");
}

// ---------------------------------------------------------------------
// Tabs (Read / Scan / Monitor / Traffic)
// ---------------------------------------------------------------------

function switchModbusTab(tabName) {
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.hidden = panel.id !== `tab-${tabName}`;
  }
}

// ---------------------------------------------------------------------
// Read (existing single-read form + auto-refresh, plus the new
// Device Identification / Data Interpretation / raw view additions)
// ---------------------------------------------------------------------

// Remembers the last-used connection settings (host/port/unit/
// function/address/quantity/interval) in the browser so the form
// comes pre-filled next time instead of needing every field retyped
// for each read -- most reads during a session are against the same
// device with only one or two fields changing.
const _STORAGE_KEY = "lanpi-modbus-config";
const _DEFAULT_CONFIG = {
  host: "", port: "502", unit: "1", function: "3", address: "0", quantity: "10", interval: "",
};

let modbusPollTimer = null;
let lastModbusReadValues = null;
let lastModbusReadAddress = null;

function loadModbusConfig() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(_STORAGE_KEY));
    return { ..._DEFAULT_CONFIG, ...(saved || {}) };
  } catch (err) {
    return { ..._DEFAULT_CONFIG };
  }
}

function applyModbusConfig(config) {
  document.getElementById("modbus-host").value = config.host;
  document.getElementById("modbus-port").value = config.port;
  document.getElementById("modbus-unit").value = config.unit;
  document.getElementById("modbus-function").value = config.function;
  document.getElementById("modbus-address").value = config.address;
  document.getElementById("modbus-quantity").value = config.quantity;
  document.getElementById("modbus-interval").value = config.interval;
}

function saveModbusConfig(config) {
  try {
    window.localStorage.setItem(_STORAGE_KEY, JSON.stringify(config));
  } catch (err) {
    // localStorage unavailable (private browsing, quota) -- fields
    // just won't persist across reloads, not worth surfacing an error
  }
}

function currentModbusFormValues() {
  return {
    host: document.getElementById("modbus-host").value.trim(),
    port: document.getElementById("modbus-port").value.trim(),
    unit: document.getElementById("modbus-unit").value.trim(),
    function: document.getElementById("modbus-function").value,
    address: document.getElementById("modbus-address").value.trim(),
    quantity: document.getElementById("modbus-quantity").value.trim(),
    interval: document.getElementById("modbus-interval").value.trim(),
  };
}

function updateRawView(result) {
  const details = document.getElementById("modbus-raw-view");
  const timingEl = document.getElementById("modbus-raw-timing");
  const txEl = document.getElementById("modbus-raw-tx");
  const rxEl = document.getElementById("modbus-raw-rx");

  if (!result.raw_request) {
    details.hidden = true;
    return;
  }
  timingEl.textContent = result.response_time_ms != null ? `Response time: ${result.response_time_ms} ms` : "";
  txEl.textContent = `TX: ${formatBytesHex(result.raw_request)}`;
  rxEl.textContent = result.raw_response ? `RX: ${formatBytesHex(result.raw_response)}` : "RX: (no response)";
  details.hidden = false;
}

function updateDecodePanel(functionCode, address, values) {
  const panel = document.getElementById("modbus-decode-panel");
  const select = document.getElementById("modbus-decode-index");

  // Only registers (FC3/FC4) are numeric enough to interpret --
  // coils/discrete inputs (FC1/FC2) are just on/off bits.
  if ((functionCode !== "3" && functionCode !== "4") || !values || values.length === 0) {
    panel.hidden = true;
    lastModbusReadValues = null;
    return;
  }

  lastModbusReadValues = values;
  lastModbusReadAddress = address;
  select.innerHTML = "";
  values.forEach((_, i) => {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = address + i;
    select.appendChild(opt);
  });
  panel.hidden = false;
  document.getElementById("modbus-decode-results").hidden = true;
}

async function submitModbusDecode(event) {
  event.preventDefault();
  if (!lastModbusReadValues) return;

  const index = parseInt(document.getElementById("modbus-decode-index").value, 10);
  const byteOrder = document.getElementById("modbus-decode-order").value;
  const values = lastModbusReadValues.slice(index, index + 2);

  try {
    const res = await fetch("/api/tools/modbus/decode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values, byte_order: byteOrder }),
    });
    const result = await res.json();
    renderDecodeResult(result);
  } catch (err) {
    // leave table as-is
  }
}

function renderDecodeResult(result) {
  const table = document.getElementById("modbus-decode-results");
  const tbody = document.getElementById("modbus-decode-results-body");
  if (result.error) {
    table.hidden = true;
    return;
  }

  const rows = [
    ["UINT16", result.uint16],
    ["INT16", result.int16],
    ["HEX16", result.hex16],
    ["BINARY16", result.binary16],
  ];
  if ("uint32" in result) {
    rows.push(
      ["UINT32", result.uint32],
      ["INT32", result.int32],
      ["FLOAT32", result.float32 != null ? result.float32.toFixed(4) : "n/a"],
      ["HEX32", result.hex32],
      [`Byte order`, result.byte_order],
    );
  }

  tbody.innerHTML = "";
  for (const [label, value] of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${label}</td><td>${value}</td>`;
    tbody.appendChild(tr);
  }
  table.hidden = false;
}

// The actual read -- no event, no button/state juggling, so it can be
// called both by the form submit and by the auto-refresh timer.
async function performModbusRead() {
  const values = currentModbusFormValues();
  const statusEl = document.getElementById("modbus-status");
  const table = document.getElementById("modbus-results");
  const tbody = document.getElementById("modbus-results-body");

  if (!values.host || values.address === "" || values.quantity === "") return;

  const port = values.port ? parseInt(values.port, 10) : 502;
  const unitId = values.unit ? parseInt(values.unit, 10) : 1;
  const functionCode = values.function;
  const address = parseInt(values.address, 10);
  const quantity = parseInt(values.quantity, 10);

  try {
    const res = await fetch("/api/tools/modbus/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: values.host, port, unit_id: unitId,
        function_code: parseInt(functionCode, 10), address, quantity,
      }),
    });
    const result = await res.json();
    updateRawView(result);

    if (!result.ok) {
      table.hidden = true;
      statusEl.textContent = result.message || "Read failed.";
      updateDecodePanel(functionCode, address, null);
      return;
    }

    const now = new Date().toLocaleTimeString();
    const timing = result.response_time_ms != null ? `, ${result.response_time_ms} ms` : "";
    statusEl.textContent = `${result.function} -- ${result.values.length} value(s) (updated ${now}${timing})`;
    tbody.innerHTML = "";
    result.values.forEach((value, i) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${result.address + i}</td>
        <td>${formatModbusValue(value, functionCode)}</td>
      `;
      tbody.appendChild(tr);
    });
    table.hidden = false;
    updateDecodePanel(functionCode, address, result.values);
  } catch (err) {
    statusEl.textContent = "Read failed.";
  }
}

function stopModbusPoll() {
  if (modbusPollTimer) {
    clearInterval(modbusPollTimer);
    modbusPollTimer = null;
  }
  document.getElementById("modbus-stop-btn").disabled = true;
  window.lanpiLocalActiveTasks.delete("Modbus Read");
}

async function submitModbusRead(event) {
  event.preventDefault();
  const values = currentModbusFormValues();
  if (!values.host || values.address === "" || values.quantity === "") return;

  saveModbusConfig(values);
  stopModbusPoll(); // clear any existing auto-poll before (re)starting with current settings

  const btn = document.getElementById("modbus-read-btn");
  btn.disabled = true;
  btn.textContent = "Reading...";
  await performModbusRead();
  btn.disabled = false;
  btn.textContent = "Read";

  if (values.interval) {
    const intervalMs = Math.max(1, parseInt(values.interval, 10)) * 1000;
    modbusPollTimer = setInterval(performModbusRead, intervalMs);
    document.getElementById("modbus-stop-btn").disabled = false;
    // Unlike Modbus Poll (a real backend background job), this is a
    // plain client-side timer -- it stops the instant this page is
    // left, so it only ever shows in the badge here, never cross-page.
    window.lanpiLocalActiveTasks.add("Modbus Read");
  }
}

// ---------------------------------------------------------------------
// Device Identification
// ---------------------------------------------------------------------

async function requestModbusDeviceId() {
  const statusEl = document.getElementById("modbus-device-id-status");
  const table = document.getElementById("modbus-device-id-results");
  const tbody = document.getElementById("modbus-device-id-results-body");
  const btn = document.getElementById("modbus-device-id-btn");

  const host = document.getElementById("modbus-host").value.trim();
  const portValue = document.getElementById("modbus-port").value.trim();
  const unitValue = document.getElementById("modbus-unit").value.trim();
  if (!host) {
    statusEl.textContent = "Enter a host above first.";
    return;
  }
  const port = portValue ? parseInt(portValue, 10) : 502;
  const unitId = unitValue ? parseInt(unitValue, 10) : 1;

  btn.disabled = true;
  btn.textContent = "Reading...";
  table.hidden = true;

  try {
    const res = await fetch("/api/tools/modbus/device-id", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port, unit_id: unitId }),
    });
    const result = await res.json();

    if (!result.ok) {
      statusEl.textContent = result.supported === false
        ? "Device Identification not supported by this device."
        : (result.message || "Request failed.");
      return;
    }

    const entries = Object.entries(result.objects || {});
    if (entries.length === 0) {
      statusEl.textContent = "Device responded but returned no identification objects.";
      return;
    }
    statusEl.textContent = `${entries.length} object(s), ${result.response_time_ms} ms`;
    tbody.innerHTML = "";
    for (const [key, value] of entries) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${key.replace(/_/g, " ")}</td><td>${value}</td>`;
      tbody.appendChild(tr);
    }
    table.hidden = false;
  } catch (err) {
    statusEl.textContent = "Request failed.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Device Info";
  }
}

// ---------------------------------------------------------------------
// Unit ID Scan
// ---------------------------------------------------------------------

let unitScanPollTimer = null;

function renderUnitScanResults(results) {
  const table = document.getElementById("unit-scan-results");
  const tbody = document.getElementById("unit-scan-results-body");
  if (results.length === 0) {
    table.hidden = true;
    return;
  }
  tbody.innerHTML = "";
  for (const r of results) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.unit_id}</td>
      <td class="${r.status === "responding" ? "link-up" : ""}">${r.status === "responding" ? "Responding" : "No response"}</td>
      <td>${r.detail || ""}</td>
    `;
    tbody.appendChild(tr);
  }
  table.hidden = false;
}

async function pollUnitScanStatus() {
  try {
    const res = await fetch("/api/tools/modbus/unit-scan/status");
    const status = await res.json();

    const statusEl = document.getElementById("unit-scan-status");
    const progressEl = document.getElementById("unit-scan-progress");
    const startBtn = document.getElementById("unit-scan-start-btn");
    const stopBtn = document.getElementById("unit-scan-stop-btn");

    const percent = status.total > 0 ? Math.round(100 * status.progress / status.total) : 0;
    progressEl.style.width = `${percent}%`;
    statusEl.textContent = status.running
      ? `Scanning ${status.host}: unit ${status.progress}/${status.total}`
      : (status.total > 0 ? `Done -- ${status.total} unit ID(s) scanned.` : "");
    startBtn.disabled = status.running;
    stopBtn.disabled = !status.running;
    renderUnitScanResults(status.results);

    if (!status.running && unitScanPollTimer) {
      clearInterval(unitScanPollTimer);
      unitScanPollTimer = null;
    }
  } catch (err) {
    // next poll retries
  }
}

async function submitUnitScan(event) {
  event.preventDefault();
  const host = document.getElementById("unit-scan-host").value.trim();
  const portValue = document.getElementById("unit-scan-port").value.trim();
  const startValue = document.getElementById("unit-scan-start").value.trim();
  const endValue = document.getElementById("unit-scan-end").value.trim();
  const timeoutValue = document.getElementById("unit-scan-timeout").value.trim();
  if (!host || startValue === "" || endValue === "") return;

  const body = {
    host,
    port: portValue ? parseInt(portValue, 10) : 502,
    start_unit: parseInt(startValue, 10),
    end_unit: parseInt(endValue, 10),
    timeout: timeoutValue ? parseFloat(timeoutValue) : 1.0,
  };

  try {
    const res = await fetch("/api/tools/modbus/unit-scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await res.json();
    if (!result.ok) {
      document.getElementById("unit-scan-status").textContent = result.message;
      return;
    }
  } catch (err) {
    document.getElementById("unit-scan-status").textContent = "Failed to start scan.";
    return;
  }

  if (!unitScanPollTimer) {
    unitScanPollTimer = setInterval(pollUnitScanStatus, 500);
  }
  pollUnitScanStatus();
}

async function stopUnitScan() {
  await fetch("/api/tools/modbus/unit-scan/stop", { method: "POST" });
  pollUnitScanStatus();
}

// ---------------------------------------------------------------------
// Register Range Scan
// ---------------------------------------------------------------------

let registerScanPollTimer = null;

function renderRegisterScanResults(segments) {
  const table = document.getElementById("register-scan-results");
  const tbody = document.getElementById("register-scan-results-body");
  if (segments.length === 0) {
    table.hidden = true;
    return;
  }
  tbody.innerHTML = "";
  for (const s of segments) {
    const tr = document.createElement("tr");
    const range = s.start === s.end ? `${s.start}` : `${s.start}-${s.end}`;
    tr.innerHTML = `
      <td>${range}</td>
      <td class="${s.readable ? "link-up" : "link-down"}">${s.readable ? "Readable" : "Unreadable"}</td>
      <td>${s.message || ""}</td>
    `;
    tbody.appendChild(tr);
  }
  table.hidden = false;
}

async function pollRegisterScanStatus() {
  try {
    const res = await fetch("/api/tools/modbus/register-scan/status");
    const status = await res.json();

    const statusEl = document.getElementById("register-scan-status");
    const progressEl = document.getElementById("register-scan-progress");
    const startBtn = document.getElementById("register-scan-start-btn");
    const stopBtn = document.getElementById("register-scan-stop-btn");

    const percent = status.total > 0 ? Math.round(100 * status.progress / status.total) : 0;
    progressEl.style.width = `${percent}%`;
    statusEl.textContent = status.running
      ? `Scanning ${status.host}: ${status.progress}/${status.total} addresses resolved`
      : (status.total > 0 ? `Done -- ${status.total} address(es) scanned.` : "");
    startBtn.disabled = status.running;
    stopBtn.disabled = !status.running;
    renderRegisterScanResults(status.segments);

    if (!status.running && registerScanPollTimer) {
      clearInterval(registerScanPollTimer);
      registerScanPollTimer = null;
    }
  } catch (err) {
    // next poll retries
  }
}

async function submitRegisterScan(event) {
  event.preventDefault();
  const host = document.getElementById("register-scan-host").value.trim();
  const portValue = document.getElementById("register-scan-port").value.trim();
  const unitValue = document.getElementById("register-scan-unit").value.trim();
  const registerType = document.getElementById("register-scan-type").value;
  const startValue = document.getElementById("register-scan-start").value.trim();
  const endValue = document.getElementById("register-scan-end").value.trim();
  const timeoutValue = document.getElementById("register-scan-timeout").value.trim();
  if (!host || startValue === "" || endValue === "") return;

  const body = {
    host,
    port: portValue ? parseInt(portValue, 10) : 502,
    unit_id: unitValue ? parseInt(unitValue, 10) : 1,
    register_type: registerType,
    start_address: parseInt(startValue, 10),
    end_address: parseInt(endValue, 10),
    timeout: timeoutValue ? parseFloat(timeoutValue) : 1.0,
  };

  try {
    const res = await fetch("/api/tools/modbus/register-scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await res.json();
    if (!result.ok) {
      document.getElementById("register-scan-status").textContent = result.message;
      return;
    }
  } catch (err) {
    document.getElementById("register-scan-status").textContent = "Failed to start scan.";
    return;
  }

  if (!registerScanPollTimer) {
    registerScanPollTimer = setInterval(pollRegisterScanStatus, 500);
  }
  pollRegisterScanStatus();
}

async function stopRegisterScan() {
  await fetch("/api/tools/modbus/register-scan/stop", { method: "POST" });
  pollRegisterScanStatus();
}

// ---------------------------------------------------------------------
// Live Polling (Monitor)
// ---------------------------------------------------------------------

let pollStatusTimer = null;

async function pollModbusPollStatus() {
  try {
    const res = await fetch("/api/tools/modbus/poll/status");
    const status = await res.json();

    document.getElementById("poll-start-btn").disabled = status.running;
    document.getElementById("poll-stop-btn").disabled = !status.running;

    if (status.values) {
      document.getElementById("poll-value").textContent = status.values.join(", ");
    } else {
      document.getElementById("poll-value").textContent = "-";
    }
    document.getElementById("poll-last-response").textContent = status.last_response_time_ms != null
      ? `${status.last_response_time_ms} ms${status.last_message ? " -- " + status.last_message : ""}`
      : (status.last_message || "-");
    document.getElementById("poll-requests").textContent = status.requests || "0";
    document.getElementById("poll-breakdown").textContent =
      `${status.successful || 0} / ${status.timeouts || 0} / ${status.exceptions || 0}`;
    document.getElementById("poll-failure-rate").textContent =
      status.failure_percent != null ? `${status.failure_percent}%` : "-";
    document.getElementById("poll-timing").textContent =
      status.min_ms != null ? `${status.min_ms} / ${status.avg_ms} / ${status.max_ms} ms` : "-";

    if (!status.running && pollStatusTimer) {
      clearInterval(pollStatusTimer);
      pollStatusTimer = null;
    }
  } catch (err) {
    // next poll retries
  }
}

async function submitModbusPoll(event) {
  event.preventDefault();
  const host = document.getElementById("poll-host").value.trim();
  const portValue = document.getElementById("poll-port").value.trim();
  const unitValue = document.getElementById("poll-unit").value.trim();
  const functionCode = document.getElementById("poll-function").value;
  const addressValue = document.getElementById("poll-address").value.trim();
  const quantityValue = document.getElementById("poll-quantity").value.trim();
  const intervalValue = document.getElementById("poll-interval").value.trim();
  if (!host || addressValue === "") return;

  const body = {
    host,
    port: portValue ? parseInt(portValue, 10) : 502,
    unit_id: unitValue ? parseInt(unitValue, 10) : 1,
    function_code: parseInt(functionCode, 10),
    address: parseInt(addressValue, 10),
    quantity: quantityValue ? parseInt(quantityValue, 10) : 1,
    interval_ms: intervalValue ? parseInt(intervalValue, 10) : 1000,
  };

  try {
    const res = await fetch("/api/tools/modbus/poll/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await res.json();
    if (!result.ok) {
      window.alert(result.message);
      return;
    }
  } catch (err) {
    window.alert("Failed to start polling.");
    return;
  }

  if (!pollStatusTimer) {
    pollStatusTimer = setInterval(pollModbusPollStatus, Math.max(300, (parseInt(intervalValue, 10) || 1000) / 2));
  }
  pollModbusPollStatus();
}

async function stopModbusPollSession() {
  await fetch("/api/tools/modbus/poll/stop", { method: "POST" });
  pollModbusPollStatus();
}

// ---------------------------------------------------------------------
// Passive Modbus TCP Traffic
// ---------------------------------------------------------------------

async function loadModbusTraffic() {
  const table = document.getElementById("modbus-traffic-results");
  const tbody = document.getElementById("modbus-traffic-results-body");
  const emptyEl = document.getElementById("modbus-traffic-empty");

  try {
    const res = await fetch("/api/tools/modbus/traffic");
    const data = await res.json();
    const relationships = data.relationships || [];

    if (relationships.length === 0) {
      table.hidden = true;
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    tbody.innerHTML = "";
    for (const r of relationships) {
      const tr = document.createElement("tr");
      const timing = r.min_ms != null ? `${r.min_ms} / ${r.avg_ms} / ${r.max_ms} ms` : "-";
      tr.innerHTML = `
        <td>${r.client_ip}</td>
        <td>${r.server_ip}</td>
        <td>${r.unit_id}</td>
        <td>${r.function_code}</td>
        <td>${r.requests}</td>
        <td>${r.responses}</td>
        <td class="${r.exceptions > 0 ? "warn" : ""}">${r.exceptions}</td>
        <td class="${r.missing > 0 ? "warn" : ""}">${r.missing}</td>
        <td>${timing}</td>
      `;
      tbody.appendChild(tr);
    }
    table.hidden = false;
  } catch (err) {
    // next poll retries
  }
}

async function resetModbusTraffic() {
  await fetch("/api/tools/modbus/traffic/reset", { method: "POST" });
  loadModbusTraffic();
}

// Only polls while the Traffic sub-tab is actually the visible one --
// started/stopped from the tab-click handler below, not on a page-wide
// timer, since this is one of four panels sharing the same page.
let modbusTrafficPollTimer = null;

// ---------------------------------------------------------------------
// Device Templates (existing)
// ---------------------------------------------------------------------

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

async function loadModbusTemplates() {
  const select = document.getElementById("modbus-template-select");
  try {
    const res = await fetch("/api/tools/modbus/templates");
    const templates = await res.json();

    select.innerHTML = "";
    if (templates.length === 0) {
      select.innerHTML = "<option value=\"\">No templates found</option>";
      return;
    }
    for (const t of templates) {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = t.name;
      select.appendChild(opt);
    }
  } catch (err) {
    select.innerHTML = "<option value=\"\">Unreachable</option>";
  }
}

async function submitModbusTemplateRead(event) {
  event.preventDefault();
  const templateId = document.getElementById("modbus-template-select").value;
  const host = document.getElementById("modbus-template-host").value.trim();
  const portValue = document.getElementById("modbus-template-port").value.trim();

  const statusEl = document.getElementById("modbus-template-status");
  const table = document.getElementById("modbus-template-results");
  const tbody = document.getElementById("modbus-template-results-body");
  const btn = document.getElementById("modbus-template-read-btn");

  if (!templateId || !host) return;
  const port = portValue ? parseInt(portValue, 10) : 502;

  btn.disabled = true;
  btn.textContent = "Reading...";
  table.hidden = true;
  statusEl.textContent = "";

  try {
    const res = await fetch("/api/tools/modbus/templates/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: templateId, host, port }),
    });
    const result = await res.json();

    if (!result.ok) {
      statusEl.textContent = result.message || "Read failed.";
      return;
    }

    statusEl.textContent = `${result.template} -- ${result.results.length} register(s)`;
    tbody.innerHTML = "";
    for (const r of result.results) {
      const tr = document.createElement("tr");
      let valueText;
      if (!r.ok) {
        valueText = r.message || "error";
      } else if (r.decoded_value !== null && r.decoded_value !== undefined) {
        valueText = Number(r.decoded_value.toFixed(4));
      } else if (r.function_code === 1 || r.function_code === 2) {
        valueText = r.values.map((v) => (v ? "ON" : "OFF")).join(", ");
      } else {
        valueText = r.values.join(", ");
      }
      tr.innerHTML = `
        <td>${r.label}</td>
        <td>${r.address}</td>
        <td>${valueText}</td>
      `;
      tbody.appendChild(tr);
    }
    table.hidden = false;
  } catch (err) {
    statusEl.textContent = "Read failed.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Read All";
  }
}

// ---------------------------------------------------------------------
// Wire-up
// ---------------------------------------------------------------------

for (const btn of document.querySelectorAll(".tab-btn")) {
  btn.addEventListener("click", () => {
    switchModbusTab(btn.dataset.tab);
    if (modbusTrafficPollTimer) {
      clearInterval(modbusTrafficPollTimer);
      modbusTrafficPollTimer = null;
    }
    if (btn.dataset.tab === "traffic") {
      loadModbusTraffic();
      modbusTrafficPollTimer = setInterval(loadModbusTraffic, 5000);
    }
  });
}

document.getElementById("modbus-form").addEventListener("submit", submitModbusRead);
document.getElementById("modbus-stop-btn").addEventListener("click", stopModbusPoll);
document.getElementById("modbus-device-id-btn").addEventListener("click", requestModbusDeviceId);
document.getElementById("modbus-decode-form").addEventListener("submit", submitModbusDecode);
document.getElementById("modbus-template-form").addEventListener("submit", submitModbusTemplateRead);

document.getElementById("modbus-unit-scan-form").addEventListener("submit", submitUnitScan);
document.getElementById("unit-scan-stop-btn").addEventListener("click", stopUnitScan);

document.getElementById("modbus-register-scan-form").addEventListener("submit", submitRegisterScan);
document.getElementById("register-scan-stop-btn").addEventListener("click", stopRegisterScan);

document.getElementById("modbus-poll-form").addEventListener("submit", submitModbusPoll);
document.getElementById("poll-stop-btn").addEventListener("click", stopModbusPollSession);

document.getElementById("modbus-traffic-reset-btn").addEventListener("click", resetModbusTraffic);

const _initialModbusConfig = loadModbusConfig();
applyModbusConfig(_initialModbusConfig);
document.getElementById("modbus-template-host").value = _initialModbusConfig.host;
document.getElementById("unit-scan-host").value = _initialModbusConfig.host;
document.getElementById("register-scan-host").value = _initialModbusConfig.host;
document.getElementById("poll-host").value = _initialModbusConfig.host;
loadModbusTemplates();
updateFooter();
setInterval(updateFooter, 60000);

// Pick up any scan/poll that was already running server-side before
// this page load (e.g. the user switched tabs and came back).
pollUnitScanStatus().then((_) => {
  if (document.getElementById("unit-scan-stop-btn").disabled === false && !unitScanPollTimer) {
    unitScanPollTimer = setInterval(pollUnitScanStatus, 500);
  }
});
pollRegisterScanStatus().then((_) => {
  if (document.getElementById("register-scan-stop-btn").disabled === false && !registerScanPollTimer) {
    registerScanPollTimer = setInterval(pollRegisterScanStatus, 500);
  }
});
pollModbusPollStatus().then((_) => {
  if (document.getElementById("poll-stop-btn").disabled === false && !pollStatusTimer) {
    pollStatusTimer = setInterval(pollModbusPollStatus, 500);
  }
});
