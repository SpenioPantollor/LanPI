function renderDevices(devices) {
  const tableEl = document.getElementById("profinet-table");
  const bodyEl = document.getElementById("profinet-table-body");
  bodyEl.innerHTML = "";

  if (devices.length === 0) {
    tableEl.hidden = true;
    return;
  }

  tableEl.hidden = false;
  for (const device of devices) {
    const tr = document.createElement("tr");
    const vendor = [device.vendor_value, device.vendor_id].filter(Boolean).join(" ");
    tr.innerHTML = `
      <td>${device.name_of_station || "-"}</td>
      <td>${device.mac}</td>
      <td>${device.ip || "-"}</td>
      <td>${vendor || "-"}</td>
      <td>${device.device_role || "-"}</td>
      <td>${device.ip_info || "-"}</td>
    `;
    bodyEl.appendChild(tr);
  }
}

async function runProfinetScan() {
  const scanBtn = document.getElementById("profinet-scan-btn");
  const statusEl = document.getElementById("profinet-scan-status");

  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  statusEl.textContent = "sending DCP Identify request...";

  try {
    const res = await fetch("/api/tools/profinet-scan", { method: "POST" });
    const result = await res.json();

    if (!result.ok) {
      statusEl.textContent = result.message || "Scan failed.";
      renderDevices([]);
      return;
    }

    statusEl.textContent = `${result.devices.length} device(s) found`;
    renderDevices(result.devices);
  } catch (err) {
    statusEl.textContent = "Scan failed.";
    renderDevices([]);
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan";
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

document.getElementById("profinet-scan-btn").addEventListener("click", runProfinetScan);

updateFooter();
setInterval(updateFooter, 60000);
