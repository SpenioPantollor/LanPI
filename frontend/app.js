function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(precision)} ${units[unitIndex]}`;
}

function formatTristate(value) {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "-";
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

async function loadStatus() {
  const statusEl = document.getElementById("status-value");
  const hostnameEl = document.getElementById("hostname-value");
  const versionEl = document.getElementById("version-value");
  const uptimeEl = document.getElementById("uptime-value");

  try {
    const [healthRes, statusRes] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/status"),
    ]);
    const health = await healthRes.json();
    const status = await statusRes.json();

    statusEl.textContent = health.status;
    hostnameEl.textContent = status.hostname;
    versionEl.textContent = status.lanpi_version;
    uptimeEl.textContent = `${status.backend_uptime_seconds}s`;
  } catch (err) {
    statusEl.textContent = "unreachable";
  }
}

async function loadSystem() {
  const modelEl = document.getElementById("system-model");
  const cpuEl = document.getElementById("system-cpu");
  const tempEl = document.getElementById("system-temp");
  const loadEl = document.getElementById("system-load");
  const memoryEl = document.getElementById("system-memory");
  const diskEl = document.getElementById("system-disk");
  const uptimeEl = document.getElementById("system-uptime");

  try {
    const res = await fetch("/api/system");
    const info = await res.json();

    modelEl.textContent = info.model || "-";
    cpuEl.textContent = info.cpu_percent !== null ? `${info.cpu_percent}%` : "-";
    tempEl.textContent = info.cpu_temp_celsius !== null ? `${info.cpu_temp_celsius}°C` : "-";

    const load = info.load_average || {};
    loadEl.textContent = [load["1min"], load["5min"], load["15min"]]
      .map((v) => (v ?? "-"))
      .join(" / ");

    const mem = info.memory || {};
    memoryEl.textContent = mem.percent !== null && mem.percent !== undefined
      ? `${formatBytes(mem.used_bytes)} / ${formatBytes(mem.total_bytes)} (${mem.percent}%)`
      : "-";

    const disk = info.disk || {};
    diskEl.textContent = disk.percent !== null && disk.percent !== undefined
      ? `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)} (${disk.percent}%)`
      : "-";

    uptimeEl.textContent = formatDuration(info.system_uptime_seconds);
  } catch (err) {
    modelEl.textContent = "unreachable";
  }
}

async function loadEth0() {
  const linkEl = document.getElementById("eth0-link");
  const speedEl = document.getElementById("eth0-speed");
  const duplexEl = document.getElementById("eth0-duplex");
  const autonegEl = document.getElementById("eth0-autoneg");
  const macEl = document.getElementById("eth0-mac");
  const mtuEl = document.getElementById("eth0-mtu");
  const rxEl = document.getElementById("eth0-rx");
  const txEl = document.getElementById("eth0-tx");
  const errorsEl = document.getElementById("eth0-errors");

  try {
    const res = await fetch("/api/network/eth0");
    const eth0 = await res.json();

    if (!eth0.present) {
      linkEl.textContent = "not found";
      linkEl.className = "";
      return;
    }

    const linkUp = eth0.link_detected === true || eth0.operstate === "UP";
    linkEl.textContent = linkUp ? "up" : "down";
    linkEl.className = linkUp ? "link-up" : "link-down";

    speedEl.textContent = eth0.speed_mbps ? `${eth0.speed_mbps} Mbps` : "-";
    duplexEl.textContent = eth0.duplex || "-";
    autonegEl.textContent = formatTristate(eth0.autoneg);
    macEl.textContent = eth0.mac_address || "-";
    mtuEl.textContent = eth0.mtu ?? "-";
    rxEl.textContent = `${formatBytes(eth0.rx_bytes)} (${eth0.rx_packets ?? "-"} pkts)`;
    txEl.textContent = `${formatBytes(eth0.tx_bytes)} (${eth0.tx_packets ?? "-"} pkts)`;
    errorsEl.textContent = `${eth0.rx_errors ?? "-"} / ${eth0.rx_dropped ?? "-"}`;
  } catch (err) {
    linkEl.textContent = "unreachable";
    linkEl.className = "";
  }
}

async function loadEth0Mode() {
  const modeEl = document.getElementById("eth0-mode");
  const addressEl = document.getElementById("eth0-address");
  const gatewayEl = document.getElementById("eth0-gateway");
  const dhcpServerEl = document.getElementById("eth0-dhcp-server");
  const leaseTimeEl = document.getElementById("eth0-lease-time");
  const domainEl = document.getElementById("eth0-domain");

  try {
    const res = await fetch("/api/network/eth0/mode");
    const config = await res.json();

    if (!config.available) {
      modeEl.textContent = "unavailable";
      return;
    }

    modeEl.textContent = config.mode;
    modeEl.className = config.mode === "passive" ? "" : "link-up";
    addressEl.textContent = config.address || "-";
    gatewayEl.textContent = config.gateway || "-";
    dhcpServerEl.textContent = config.dhcp_server || "-";
    leaseTimeEl.textContent = config.lease_time_seconds != null ? formatDuration(config.lease_time_seconds) : "-";
    domainEl.textContent = config.domain_name || "-";
  } catch (err) {
    modeEl.textContent = "unreachable";
  }
}

async function setEth0Mode(mode, extra) {
  try {
    const res = await fetch("/api/network/eth0/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, ...extra }),
    });
    const result = await res.json();
    if (!result.ok) {
      window.alert(`Failed to set eth0 to ${mode}: ${result.message}`);
    }
  } catch (err) {
    window.alert(`Failed to set eth0 to ${mode}.`);
  }
  // Passive/DHCP need no fields -- collapse the static form so the
  // card doesn't stay expanded when it's not relevant. Static leaves
  // it open (submitting it is what got us here).
  if (mode !== "static") {
    document.getElementById("eth0-static-form").hidden = true;
  }
  loadEth0Mode();
  loadEth0();
}

function toggleEth0StaticForm() {
  const form = document.getElementById("eth0-static-form");
  form.hidden = !form.hidden;
}

function applyEth0Static(event) {
  event.preventDefault();
  const address = document.getElementById("eth0-static-address").value.trim();
  const gateway = document.getElementById("eth0-static-gateway").value.trim();
  const dns = document.getElementById("eth0-static-dns").value.trim();
  setEth0Mode("static", { address, gateway, dns });
}

async function loadLldp() {
  const statusEl = document.getElementById("lldp-status");
  const systemEl = document.getElementById("lldp-system");
  const chassisEl = document.getElementById("lldp-chassis");
  const portEl = document.getElementById("lldp-port");
  const mgmtIpEl = document.getElementById("lldp-mgmt-ip");
  const vlanEl = document.getElementById("lldp-vlan");
  const ageEl = document.getElementById("lldp-age");

  try {
    const res = await fetch("/api/discovery/lldp");
    const lldp = await res.json();

    if (!lldp.present) {
      statusEl.textContent = "no neighbor seen";
      statusEl.className = "";
      systemEl.textContent = "-";
      chassisEl.textContent = "-";
      portEl.textContent = "-";
      mgmtIpEl.textContent = "-";
      vlanEl.textContent = "-";
      ageEl.textContent = "-";
      return;
    }

    statusEl.textContent = "neighbor found";
    statusEl.className = "link-up";
    systemEl.textContent = lldp.system_name || lldp.system_description || "-";
    chassisEl.textContent = lldp.chassis_id || "-";
    portEl.textContent = lldp.port_description || lldp.port_id || "-";
    mgmtIpEl.textContent = lldp.management_ip || "-";
    vlanEl.textContent = lldp.vlan ?? "-";
    ageEl.textContent = `${lldp.age_seconds}s ago`;
  } catch (err) {
    statusEl.textContent = "unreachable";
    statusEl.className = "";
  }
}

async function loadCdp() {
  const statusEl = document.getElementById("cdp-status");
  const deviceEl = document.getElementById("cdp-device");
  const portEl = document.getElementById("cdp-port");
  const platformEl = document.getElementById("cdp-platform");
  const softwareEl = document.getElementById("cdp-software");
  const vlanEl = document.getElementById("cdp-vlan");
  const addressEl = document.getElementById("cdp-address");
  const ageEl = document.getElementById("cdp-age");

  try {
    const res = await fetch("/api/discovery/cdp");
    const cdp = await res.json();

    if (!cdp.present) {
      statusEl.textContent = "no neighbor seen";
      statusEl.className = "";
      deviceEl.textContent = "-";
      portEl.textContent = "-";
      platformEl.textContent = "-";
      softwareEl.textContent = "-";
      vlanEl.textContent = "-";
      addressEl.textContent = "-";
      ageEl.textContent = "-";
      return;
    }

    statusEl.textContent = "neighbor found";
    statusEl.className = "link-up";
    deviceEl.textContent = cdp.device_id || "-";
    portEl.textContent = cdp.port_id || "-";
    platformEl.textContent = cdp.platform || "-";
    softwareEl.textContent = cdp.software_version || "-";
    vlanEl.textContent = cdp.native_vlan ?? "-";
    addressEl.textContent = cdp.address || "-";
    ageEl.textContent = `${cdp.age_seconds}s ago`;
  } catch (err) {
    statusEl.textContent = "unreachable";
    statusEl.className = "";
  }
}

async function loadMndp() {
  const statusEl = document.getElementById("mndp-status");
  const identityEl = document.getElementById("mndp-identity");
  const platformEl = document.getElementById("mndp-platform");
  const versionEl = document.getElementById("mndp-version");
  const interfaceEl = document.getElementById("mndp-interface");
  const addressEl = document.getElementById("mndp-address");
  const macEl = document.getElementById("mndp-mac");
  const uptimeEl = document.getElementById("mndp-uptime");
  const ageEl = document.getElementById("mndp-age");

  try {
    const res = await fetch("/api/discovery/mndp");
    const mndp = await res.json();

    if (!mndp.present) {
      statusEl.textContent = "no neighbor seen";
      statusEl.className = "";
      identityEl.textContent = "-";
      platformEl.textContent = "-";
      versionEl.textContent = "-";
      interfaceEl.textContent = "-";
      addressEl.textContent = "-";
      macEl.textContent = "-";
      uptimeEl.textContent = "-";
      ageEl.textContent = "-";
      return;
    }

    statusEl.textContent = "neighbor found";
    statusEl.className = "link-up";
    identityEl.textContent = mndp.identity || "-";
    platformEl.textContent = [mndp.platform, mndp.board].filter(Boolean).join(" / ") || "-";
    versionEl.textContent = mndp.version || "-";
    interfaceEl.textContent = mndp.interface_name || "-";
    addressEl.textContent = mndp.ipv4_address || "-";
    macEl.textContent = mndp.mac_address || "-";
    uptimeEl.textContent = mndp.uptime_seconds != null ? formatDuration(mndp.uptime_seconds) : "-";
    ageEl.textContent = `${mndp.age_seconds}s ago`;
  } catch (err) {
    statusEl.textContent = "unreachable";
    statusEl.className = "";
  }
}

async function runArpScan(event) {
  event.preventDefault();
  const network = document.getElementById("arp-scan-network").value.trim();
  const scanBtn = document.getElementById("arp-scan-btn");
  const resultsEl = document.getElementById("arp-scan-results");

  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  resultsEl.innerHTML = "";

  try {
    const res = await fetch("/api/tools/arp-scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ network: network || null }),
    });
    const result = await res.json();

    if (!result.ok) {
      resultsEl.innerHTML = `<li class="wifi-empty">${result.message || "Scan failed."}</li>`;
      return;
    }

    if (result.hosts.length === 0) {
      resultsEl.innerHTML = "<li class=\"wifi-empty\">No hosts found.</li>";
      return;
    }

    for (const host of result.hosts) {
      const li = document.createElement("li");
      const vendor = host.vendor ? ` (${host.vendor})` : "";
      li.innerHTML = `<span>${host.ip} - ${host.mac}${vendor}</span>`;
      resultsEl.appendChild(li);
    }
  } catch (err) {
    resultsEl.innerHTML = "<li class=\"wifi-empty\">Scan failed.</li>";
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan";
  }
}

async function submitMtr(event) {
  event.preventDefault();
  const host = document.getElementById("mtr-host").value.trim();
  const cyclesValue = document.getElementById("mtr-cycles").value.trim();
  const cycles = cyclesValue ? parseInt(cyclesValue, 10) : 10;
  const btn = document.getElementById("mtr-btn");
  const table = document.getElementById("mtr-results");
  const tbody = document.getElementById("mtr-results-body");
  const messageEl = document.getElementById("mtr-message");
  if (!host) return;

  btn.disabled = true;
  btn.textContent = "Running...";
  table.hidden = true;
  tbody.innerHTML = "";
  messageEl.textContent = "";

  try {
    const res = await fetch("/api/tools/mtr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, cycles }),
    });
    const result = await res.json();

    if (!result.ok) {
      messageEl.textContent = result.message || "MTR failed.";
      return;
    }

    if (result.hops.length === 0) {
      messageEl.textContent = "No hops reported.";
      return;
    }

    for (const hop of result.hops) {
      const tr = document.createElement("tr");
      const fmt = (v) => (v ?? "-");
      tr.innerHTML = `
        <td>${fmt(hop.hop)}</td>
        <td>${fmt(hop.host)}</td>
        <td>${fmt(hop.loss_percent)}</td>
        <td>${fmt(hop.sent)}</td>
        <td>${fmt(hop.last_ms)}</td>
        <td>${fmt(hop.avg_ms)}</td>
        <td>${fmt(hop.best_ms)}</td>
        <td>${fmt(hop.worst_ms)}</td>
      `;
      tbody.appendChild(tr);
    }
    table.hidden = false;
  } catch (err) {
    messageEl.textContent = "MTR failed.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Run";
  }
}

async function submitTcpTest(event) {
  event.preventDefault();
  const host = document.getElementById("tcp-test-host").value.trim();
  const port = parseInt(document.getElementById("tcp-test-port").value, 10);
  const targetEl = document.getElementById("tcp-test-target");
  const stateEl = document.getElementById("tcp-test-state");
  const latencyEl = document.getElementById("tcp-test-latency");
  if (!host || !port) return;

  // Shown here rather than left to the input fields -- this is what's
  // actually being tested right now, and stays correct even if the
  // inputs get edited again before the result comes back.
  targetEl.textContent = `${host}:${port}`;
  stateEl.textContent = "testing...";
  stateEl.className = "";
  latencyEl.textContent = "-";

  try {
    const res = await fetch("/api/tools/tcp-test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port }),
    });
    const result = await res.json();

    if (!result.ok) {
      stateEl.textContent = result.message || result.state || "error";
      latencyEl.textContent = "-";
      return;
    }

    const stateLabels = { open: "open", closed: "closed", timeout: "timeout / filtered" };
    stateEl.textContent = stateLabels[result.state] || result.state;
    stateEl.className = result.state === "open" ? "link-up" : "";
    latencyEl.textContent = result.latency_ms != null ? `${result.latency_ms} ms` : "-";
  } catch (err) {
    stateEl.textContent = "unreachable";
    latencyEl.textContent = "-";
  }
}

async function loadCaptureStatus() {
  const statusEl = document.getElementById("capture-status");
  const elapsedEl = document.getElementById("capture-elapsed");
  const startBtn = document.getElementById("capture-start-btn");
  const stopBtn = document.getElementById("capture-stop-btn");

  try {
    const res = await fetch("/api/capture/status");
    const status = await res.json();

    statusEl.textContent = status.running ? `capturing (${status.filename})` : "idle";
    statusEl.className = status.running ? "link-up" : "";
    elapsedEl.textContent = status.elapsed_seconds != null ? `${status.elapsed_seconds}s` : "-";
    startBtn.disabled = status.running;
    stopBtn.disabled = !status.running;

    return status.running;
  } catch (err) {
    statusEl.textContent = "unreachable";
    return false;
  }
}

async function loadCaptureList() {
  const listEl = document.getElementById("capture-list");
  try {
    const res = await fetch("/api/capture/list");
    const captures = await res.json();

    listEl.innerHTML = "";
    if (captures.length === 0) {
      listEl.innerHTML = "<li class=\"wifi-empty\">No captures yet.</li>";
      return;
    }

    for (const capture of captures) {
      const li = document.createElement("li");
      li.innerHTML = `<span>${capture.filename} (${formatBytes(capture.size_bytes)})</span>`;

      const downloadLink = document.createElement("a");
      downloadLink.href = `/api/capture/download/${encodeURIComponent(capture.filename)}`;
      downloadLink.textContent = "Download";
      downloadLink.className = "card-footnote";
      li.appendChild(downloadLink);

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.textContent = "Delete";
      deleteBtn.addEventListener("click", () => deleteCapture(capture.filename));
      li.appendChild(deleteBtn);

      listEl.appendChild(li);
    }
  } catch (err) {
    listEl.innerHTML = "<li class=\"wifi-empty\">Unreachable.</li>";
  }
}

let capturePollTimer = null;

async function pollCaptureStatus() {
  const running = await loadCaptureStatus();
  if (running && !capturePollTimer) {
    capturePollTimer = setInterval(async () => {
      const stillRunning = await loadCaptureStatus();
      if (!stillRunning) {
        clearInterval(capturePollTimer);
        capturePollTimer = null;
        loadCaptureList();
      }
    }, 1000);
  }
}

async function startCapture(event) {
  event.preventDefault();
  const durationValue = document.getElementById("capture-duration").value.trim();
  const filterValue = document.getElementById("capture-filter").value.trim();
  const duration = durationValue ? parseInt(durationValue, 10) : null;
  const bpf_filter = filterValue || null;

  try {
    const res = await fetch("/api/capture/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ duration, bpf_filter }),
    });
    const result = await res.json();
    if (!result.ok) {
      window.alert(`Failed to start capture: ${result.message}`);
      return;
    }
  } catch (err) {
    window.alert("Failed to start capture.");
    return;
  }

  pollCaptureStatus();
}

async function stopCapture() {
  try {
    await fetch("/api/capture/stop", { method: "POST" });
  } catch (err) {
    // next poll reflects actual state
  }
  await loadCaptureStatus();
  loadCaptureList();
}

async function deleteCapture(filename) {
  if (!window.confirm(`Delete "${filename}"?`)) return;
  try {
    await fetch("/api/capture/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
  } catch (err) {
    // ignore, list refresh below reflects actual state
  }
  loadCaptureList();
}

async function loadWifiStatus() {
  const modeEl = document.getElementById("wifi-mode");
  const ssidEl = document.getElementById("wifi-ssid");
  const ipEl = document.getElementById("wifi-ip");

  try {
    const res = await fetch("/api/network/wifi");
    const wifi = await res.json();

    if (!wifi.available) {
      modeEl.textContent = "unavailable";
      return;
    }

    const modeLabels = { client: "connected", ap: "fallback AP", none: "disconnected" };
    modeEl.textContent = modeLabels[wifi.mode] || wifi.mode;
    ssidEl.textContent = wifi.ssid || "-";
    ipEl.textContent = wifi.ip_address || "-";
  } catch (err) {
    modeEl.textContent = "unreachable";
  }
}

let pingPollTimer = null;

function renderPingStatus(result) {
  const statusEl = document.getElementById("ping-status");
  const countsEl = document.getElementById("ping-counts");
  const lossEl = document.getElementById("ping-loss");
  const rttEl = document.getElementById("ping-rtt");
  const startBtn = document.getElementById("ping-start-btn");
  const stopBtn = document.getElementById("ping-stop-btn");

  if (result.running) {
    statusEl.textContent = `pinging ${result.host}...`;
  } else if (result.host) {
    statusEl.textContent = `stopped (${result.host})`;
  } else {
    statusEl.textContent = "-";
  }
  statusEl.className = result.running ? "link-up" : "";
  const transmittedText = result.transmitted ?? (result.running ? "..." : "-");
  countsEl.textContent = `${result.received ?? 0} / ${transmittedText}`;

  if (result.packet_loss_percent !== null && result.packet_loss_percent !== undefined) {
    const lost = result.transmitted != null ? result.transmitted - result.received : null;
    lossEl.textContent = lost !== null ? `${result.packet_loss_percent}% (${lost} lost)` : `${result.packet_loss_percent}%`;
  } else {
    lossEl.textContent = "-";
  }

  const fmtMs = (v) => (v !== null && v !== undefined ? `${v} ms` : "-");
  rttEl.textContent = result.min_ms !== null && result.min_ms !== undefined
    ? `${fmtMs(result.min_ms)} / ${fmtMs(result.avg_ms)} / ${fmtMs(result.max_ms)}`
    : "-";

  startBtn.disabled = result.running;
  stopBtn.disabled = !result.running;
}

async function pollPingStatus() {
  try {
    const res = await fetch("/api/tools/ping/status");
    const result = await res.json();
    renderPingStatus(result);

    if (result.running && !pingPollTimer) {
      pingPollTimer = setInterval(pollPingStatus, 1000);
    } else if (!result.running && pingPollTimer) {
      clearInterval(pingPollTimer);
      pingPollTimer = null;
    }
  } catch (err) {
    // next poll (or the next manual start) will pick things back up
  }
}

async function startPing(event) {
  event.preventDefault();
  const host = document.getElementById("ping-host").value.trim();
  const countValue = document.getElementById("ping-count").value.trim();
  if (!host) return;

  const count = countValue ? parseInt(countValue, 10) : null;

  try {
    const res = await fetch("/api/tools/ping/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, count }),
    });
    const result = await res.json();
    if (!result.ok) {
      window.alert(`Failed to start ping: ${result.message}`);
      return;
    }
  } catch (err) {
    window.alert("Failed to start ping.");
    return;
  }

  pollPingStatus();
}

async function stopPing() {
  try {
    await fetch("/api/tools/ping/stop", { method: "POST" });
  } catch (err) {
    // ignore, next poll reflects actual state
  }
  pollPingStatus();
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

function loadAll() {
  loadStatus();
  loadSystem();
  loadEth0();
  loadEth0Mode();
  loadLldp();
  loadCdp();
  loadMndp();
  loadWifiStatus();
  loadCaptureStatus();
  updateFooter();
}

document.getElementById("eth0-passive-btn").addEventListener("click", () => setEth0Mode("passive"));
document.getElementById("eth0-dhcp-btn").addEventListener("click", () => setEth0Mode("dhcp"));
document.getElementById("eth0-static-btn").addEventListener("click", toggleEth0StaticForm);
document.getElementById("eth0-static-form").addEventListener("submit", applyEth0Static);
document.getElementById("ping-form").addEventListener("submit", startPing);
document.getElementById("ping-stop-btn").addEventListener("click", stopPing);
document.getElementById("arp-scan-form").addEventListener("submit", runArpScan);
document.getElementById("mtr-form").addEventListener("submit", submitMtr);
document.getElementById("tcp-test-form").addEventListener("submit", submitTcpTest);
document.querySelectorAll("#tcp-test-presets button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("tcp-test-port").value = btn.dataset.port;
  });
});
document.getElementById("capture-form").addEventListener("submit", startCapture);
document.getElementById("capture-stop-btn").addEventListener("click", stopCapture);

// Masonry-style layout with a FIXED, round-robin column assignment
// (by DOM order) rather than "shortest current column" packing: a
// card's column never changes just because some card's content
// height changed (that was reshuffling every card's position after
// things like an ARP scan) -- only its vertical offset within its own
// column does, and only its own column's cards shift as a result.
function layoutCards() {
  const container = document.querySelector("main");
  const cards = Array.from(container.querySelectorAll(".card"));
  if (cards.length === 0) return;

  // CSS custom properties (--gap, and formerly --card-col-width) come
  // back from getComputedStyle as raw text (e.g. "22rem" or
  // "clamp(1rem, 4vw, 1.5rem)"), not a resolved px number -- parseFloat
  // on those silently strips the unit instead of converting it,
  // producing a wildly wrong pixel value (22 instead of 352). Standard
  // properties like fontSize *are* resolved to px, so derive rem from
  // that instead of reading custom properties directly.
  const remPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const gap = remPx * 1.25;
  const minColWidth = remPx * 22;
  const containerWidth = container.clientWidth;
  const columns = Math.max(1, Math.floor((containerWidth + gap) / (minColWidth + gap)));
  const colWidth = (containerWidth - (columns - 1) * gap) / columns;

  const colHeights = new Array(columns).fill(0);
  cards.forEach((card, i) => {
    const col = i % columns;
    card.style.width = `${colWidth}px`;
    card.style.left = `${col * (colWidth + gap)}px`;
    card.style.top = `${colHeights[col]}px`;
    colHeights[col] += card.offsetHeight + gap;
  });

  container.style.height = `${Math.max(...colHeights) - gap}px`;
}

const _cardResizeObserver = new ResizeObserver(() => {
  window.requestAnimationFrame(layoutCards);
});
document.querySelectorAll(".card").forEach((card) => _cardResizeObserver.observe(card));
window.addEventListener("resize", layoutCards);
layoutCards();

loadAll();
loadCaptureList();
setInterval(loadAll, 5000);

// In case a ping or capture was left running from before a page
// reload, pick their status up immediately (both resume their own
// faster polling on their own if still active).
pollPingStatus();
pollCaptureStatus();

// Mobile browsers (especially "add to home screen" standalone mode)
// suspend timers while the tab/app is backgrounded, so the page can be
// left showing stale data until it happens to refresh. Force an
// immediate reload of all data as soon as the page becomes visible
// again instead of waiting for the next interval tick.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    loadAll();
  }
});
