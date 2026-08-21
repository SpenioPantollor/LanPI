function formatModbusValue(value, functionCode) {
  if (functionCode === "1" || functionCode === "2" || functionCode === 1 || functionCode === 2) {
    return value ? "ON" : "OFF";
  }
  const hex = value.toString(16).toUpperCase().padStart(4, "0");
  return `${value} (0x${hex})`;
}

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

    if (!result.ok) {
      table.hidden = true;
      statusEl.textContent = result.message || "Read failed.";
      return;
    }

    const now = new Date().toLocaleTimeString();
    statusEl.textContent = `${result.function} -- ${result.values.length} value(s) (updated ${now})`;
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

document.getElementById("modbus-form").addEventListener("submit", submitModbusRead);
document.getElementById("modbus-stop-btn").addEventListener("click", stopModbusPoll);

applyModbusConfig(loadModbusConfig());
updateFooter();
setInterval(updateFooter, 60000);
