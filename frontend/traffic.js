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

// Talkers are sorted client-side (the API returns every talker, not
// just a top-N slice, specifically so sorting by any column is
// accurate rather than limited to whichever subset happened to rank
// highest by bytes). Sort choice is kept across the 5s auto-refresh
// poll instead of resetting to the default every time.
let lastTalkers = [];
let talkerSort = { column: "bytes", direction: "desc" };

function talkerSortValue(talker, column) {
  if (column === "identity") return talker.ip ? `${talker.ip} (${talker.mac})` : talker.mac;
  if (column === "bytes") return talker.bytes;
  if (column === "packets") return talker.packets;
  if (column === "broadcast") return talker.broadcast;
  if (column === "multicast") return talker.multicast;
  return (talker.protocols || {})[column] ?? 0;
}

function renderTalkersTable() {
  const tbody = document.getElementById("traffic-talkers-body");
  const emptyEl = document.getElementById("traffic-talkers-empty");

  const sorted = [...lastTalkers].sort((a, b) => {
    const av = talkerSortValue(a, talkerSort.column);
    const bv = talkerSortValue(b, talkerSort.column);
    if (typeof av === "string") {
      return talkerSort.direction === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    }
    return talkerSort.direction === "asc" ? av - bv : bv - av;
  });

  tbody.innerHTML = "";
  if (sorted.length === 0) {
    emptyEl.textContent = "No traffic seen yet.";
  } else {
    emptyEl.textContent = "";
    for (const talker of sorted) {
      const tr = document.createElement("tr");
      const proto = talker.protocols || {};
      const identity = talker.ip ? `${talker.ip} (${talker.mac})` : talker.mac;
      tr.innerHTML = `
        <td>${identity}</td>
        <td>${formatBytes(talker.bytes)}</td>
        <td>${talker.packets}</td>
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

  document.querySelectorAll("#traffic-talkers-table th[data-sort]").forEach((th) => {
    const active = th.dataset.sort === talkerSort.column;
    th.classList.toggle("sorted", active);
    th.classList.toggle("sorted-desc", active && talkerSort.direction === "desc");
  });
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

    lastTalkers = stats.top_talkers;
    renderTalkersTable();
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
document.querySelectorAll("#traffic-talkers-table th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    const column = th.dataset.sort;
    if (talkerSort.column === column) {
      talkerSort.direction = talkerSort.direction === "desc" ? "asc" : "desc";
    } else {
      talkerSort = { column, direction: "desc" };
    }
    renderTalkersTable();
  });
});

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
