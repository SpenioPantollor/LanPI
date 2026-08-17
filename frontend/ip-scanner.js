let scanPollTimer = null;

function renderScanStatus(result) {
  const statusEl = document.getElementById("ip-scan-status");
  const resultsEl = document.getElementById("ip-scan-results");
  const startBtn = document.getElementById("ip-scan-start-btn");
  const stopBtn = document.getElementById("ip-scan-stop-btn");

  startBtn.disabled = result.running;
  stopBtn.disabled = !result.running;

  if (result.running) {
    statusEl.textContent = `scanning ${result.target}... (${result.hosts.length} found so far)`;
  } else if (result.target) {
    statusEl.textContent = `done (${result.target}) -- ${result.hosts.length} host(s) found`;
  } else {
    statusEl.textContent = "";
  }

  resultsEl.innerHTML = "";
  if (result.hosts.length === 0) {
    if (!result.running && result.target) {
      resultsEl.innerHTML = "<li class=\"wifi-empty\">No hosts found.</li>";
    }
    return;
  }
  for (const host of result.hosts) {
    const li = document.createElement("li");
    const mac = host.mac ? ` - ${host.mac}` : "";
    const vendor = host.vendor ? ` (${host.vendor})` : "";
    li.innerHTML = `<span>${host.ip}${mac}${vendor}</span>`;
    resultsEl.appendChild(li);
  }
}

async function pollScanStatus() {
  try {
    const res = await fetch("/api/tools/ip-scan/status");
    const result = await res.json();
    renderScanStatus(result);

    if (result.running && !scanPollTimer) {
      scanPollTimer = setInterval(pollScanStatus, 1000);
    } else if (!result.running && scanPollTimer) {
      clearInterval(scanPollTimer);
      scanPollTimer = null;
    }
  } catch (err) {
    // next poll (or the next manual start) will pick things back up
  }
}

async function startScan(event) {
  event.preventDefault();
  const target = document.getElementById("ip-scan-target").value.trim();
  if (!target) return;

  try {
    const res = await fetch("/api/tools/ip-scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target }),
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

  pollScanStatus();
}

async function stopScan() {
  try {
    await fetch("/api/tools/ip-scan/stop", { method: "POST" });
  } catch (err) {
    // next poll reflects actual state
  }
  pollScanStatus();
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

document.getElementById("ip-scan-form").addEventListener("submit", startScan);
document.getElementById("ip-scan-stop-btn").addEventListener("click", stopScan);

updateFooter();
setInterval(updateFooter, 60000);

// In case a scan was left running from before a page reload, pick its
// status up immediately (resumes its own faster polling if active).
pollScanStatus();

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollScanStatus();
  }
});
