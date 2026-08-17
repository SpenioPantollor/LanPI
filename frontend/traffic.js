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

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

async function loadTrafficStats() {
  const elapsedEl = document.getElementById("traffic-elapsed");
  const ppsEl = document.getElementById("traffic-pps");
  const bpsEl = document.getElementById("traffic-bps");
  const totalsEl = document.getElementById("traffic-totals");
  const bmuEl = document.getElementById("traffic-bmu");
  const l3El = document.getElementById("traffic-l3");
  const appEl = document.getElementById("traffic-app");
  const discoveryEl = document.getElementById("traffic-discovery");
  const industrialEl = document.getElementById("traffic-industrial");
  const tbody = document.getElementById("traffic-talkers-body");
  const emptyEl = document.getElementById("traffic-talkers-empty");

  try {
    const res = await fetch("/api/traffic/stats");
    const stats = await res.json();

    elapsedEl.textContent = formatDuration(stats.elapsed_seconds);
    ppsEl.textContent = `${stats.packets_per_second}/s`;
    bpsEl.textContent = `${formatBytes(stats.bytes_per_second)}/s`;
    totalsEl.textContent = `${stats.packets} / ${formatBytes(stats.bytes)}`;
    bmuEl.textContent = `${stats.broadcast} / ${stats.multicast} / ${stats.unicast}`;

    const p = stats.protocols;
    l3El.textContent = `${p.arp} / ${p.ipv4} / ${p.ipv6}`;
    appEl.textContent = `${p.dhcp} / ${p.mdns} / ${p.ssdp}`;
    discoveryEl.textContent = `${p.lldp} / ${p.cdp}`;
    industrialEl.textContent = `${p.profinet} / ${p.s7}`;

    tbody.innerHTML = "";
    if (stats.top_talkers.length === 0) {
      emptyEl.textContent = "No traffic seen yet.";
    } else {
      emptyEl.textContent = "";
      for (const talker of stats.top_talkers) {
        const tr = document.createElement("tr");
        const proto = talker.protocols || {};
        tr.innerHTML = `
          <td>${talker.identity}</td>
          <td>${formatBytes(talker.bytes_per_second)}</td>
          <td>${talker.packets_per_second}</td>
          <td>${talker.broadcast}</td>
          <td>${talker.multicast}</td>
          <td>${proto.arp ?? 0}</td>
          <td>${proto.profinet ?? 0}</td>
          <td>${proto.s7 ?? 0}</td>
          <td>${proto.mdns ?? 0}</td>
        `;
        tbody.appendChild(tr);
      }
    }
  } catch (err) {
    elapsedEl.textContent = "unreachable";
  }
}

async function resetTrafficStats() {
  if (!window.confirm("Reset traffic statistics?")) return;
  try {
    await fetch("/api/traffic/reset", { method: "POST" });
  } catch (err) {
    // ignore, next load reflects actual state
  }
  loadTrafficStats();
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
  loadTrafficStats();
  updateFooter();
}

document.getElementById("traffic-reset-btn").addEventListener("click", resetTrafficStats);

// No JS masonry on this page -- just two cards, both full-width by
// design (see #traffic-main in style.css), so plain stacked flow is
// all that's needed.
loadAll();
setInterval(loadAll, 5000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    loadAll();
  }
});
