let portScanPollTimer = null;

function renderPortScanStatus(result) {
  const statusEl = document.getElementById("port-scan-status");
  const table = document.getElementById("port-scan-results");
  const tbody = document.getElementById("port-scan-results-body");
  const startBtn = document.getElementById("port-scan-start-btn");
  const stopBtn = document.getElementById("port-scan-stop-btn");

  startBtn.disabled = result.running;
  stopBtn.disabled = !result.running;

  if (result.running) {
    statusEl.textContent = `scanning ${result.host} (${result.port_range})...`;
    return;
  }

  if (result.ok === null) {
    // No scan has completed yet since page load -- leave the card blank.
    return;
  }

  if (!result.ok) {
    table.hidden = true;
    statusEl.textContent = result.message || "Scan failed.";
    return;
  }

  if (result.ports.length === 0) {
    table.hidden = true;
    statusEl.textContent = `done (${result.host}, ${result.port_range}) -- no open ports found`;
    return;
  }

  statusEl.textContent = `done (${result.host}, ${result.port_range}) -- ${result.ports.length} open port(s)`;
  tbody.innerHTML = "";
  for (const p of result.ports) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${p.port}</td>
      <td>${p.protocol}</td>
      <td>${p.state}</td>
      <td>${p.service}</td>
    `;
    tbody.appendChild(tr);
  }
  table.hidden = false;
}

async function pollPortScanStatus() {
  try {
    const res = await fetch("/api/tools/port-scan/status");
    const result = await res.json();
    renderPortScanStatus(result);

    if (result.running && !portScanPollTimer) {
      portScanPollTimer = setInterval(pollPortScanStatus, 1000);
    } else if (!result.running && portScanPollTimer) {
      clearInterval(portScanPollTimer);
      portScanPollTimer = null;
    }
  } catch (err) {
    // next poll (or the next manual start) will pick things back up
  }
}

async function startPortScan(event) {
  event.preventDefault();
  const host = document.getElementById("port-scan-host").value.trim();
  const portRange = document.getElementById("port-scan-range").value.trim();
  if (!host || !portRange) return;

  try {
    const res = await fetch("/api/tools/port-scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port_range: portRange }),
    });
    const result = await res.json();
    if (!result.ok) {
      window.alert(`Failed to start scan: ${result.message}`);
      return;
    }
  } catch (err) {
    window.alert("Failed to start scan.");
    return;
  }

  pollPortScanStatus();
}

async function stopPortScan() {
  try {
    await fetch("/api/tools/port-scan/stop", { method: "POST" });
  } catch (err) {
    // next poll reflects actual state
  }
  pollPortScanStatus();
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

document.getElementById("port-scan-form").addEventListener("submit", startPortScan);
document.getElementById("port-scan-stop-btn").addEventListener("click", stopPortScan);
document.querySelectorAll("#port-scan-presets button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("port-scan-range").value = btn.dataset.range;
  });
});

updateFooter();
setInterval(updateFooter, 60000);

// In case a scan was left running from before a page reload, pick its
// status up immediately (resumes its own faster polling if active).
pollPortScanStatus();

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollPortScanStatus();
  }
});
