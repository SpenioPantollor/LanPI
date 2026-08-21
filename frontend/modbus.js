function formatModbusValue(value, functionCode) {
  if (functionCode === "1" || functionCode === "2" || functionCode === 1 || functionCode === 2) {
    return value ? "ON" : "OFF";
  }
  const hex = value.toString(16).toUpperCase().padStart(4, "0");
  return `${value} (0x${hex})`;
}

async function submitModbusRead(event) {
  event.preventDefault();
  const host = document.getElementById("modbus-host").value.trim();
  const portValue = document.getElementById("modbus-port").value.trim();
  const unitValue = document.getElementById("modbus-unit").value.trim();
  const functionCode = document.getElementById("modbus-function").value;
  const addressValue = document.getElementById("modbus-address").value.trim();
  const quantityValue = document.getElementById("modbus-quantity").value.trim();

  const statusEl = document.getElementById("modbus-status");
  const table = document.getElementById("modbus-results");
  const tbody = document.getElementById("modbus-results-body");
  const btn = document.getElementById("modbus-read-btn");

  if (!host || !addressValue || !quantityValue) return;

  const port = portValue ? parseInt(portValue, 10) : 502;
  const unitId = unitValue ? parseInt(unitValue, 10) : 1;
  const address = parseInt(addressValue, 10);
  const quantity = parseInt(quantityValue, 10);

  btn.disabled = true;
  btn.textContent = "Reading...";
  table.hidden = true;
  statusEl.textContent = "";

  try {
    const res = await fetch("/api/tools/modbus/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host, port, unit_id: unitId, function_code: parseInt(functionCode, 10),
        address, quantity,
      }),
    });
    const result = await res.json();

    if (!result.ok) {
      statusEl.textContent = result.message || "Read failed.";
      return;
    }

    statusEl.textContent = `${result.function} -- ${result.values.length} value(s)`;
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

document.getElementById("modbus-form").addEventListener("submit", submitModbusRead);

updateFooter();
setInterval(updateFooter, 60000);
