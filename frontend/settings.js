async function loadWifiStatus() {
  const modeEl = document.getElementById("wifi-mode");
  const ssidEl = document.getElementById("wifi-ssid");
  const ipEl = document.getElementById("wifi-ip");
  const scanActionsEl = document.getElementById("wifi-scan-actions");
  const scanHintEl = document.getElementById("wifi-scan-ap-hint");
  const scanResultsEl = document.getElementById("wifi-networks");
  const retryActionsEl = document.getElementById("wifi-retry-actions");

  try {
    const res = await fetch("/api/network/wifi");
    const wifi = await res.json();

    if (!wifi.available) {
      modeEl.textContent = "unavailable";
      return;
    }

    const modeLabels = { client: "connected", ap: "fallback AP", none: "disconnected" };
    modeEl.textContent = modeLabels[wifi.mode] || wifi.mode;
    modeEl.className = wifi.mode === "ap" ? "link-up" : "";
    ssidEl.textContent = wifi.ssid || "-";
    ipEl.textContent = wifi.ip_address || "-";

    // Single Wi-Fi radio -- while the fallback AP owns wlan0, a scan
    // can't return anything real (see wifi.py's scan() docstring).
    // Swap the button for a hint pointing at "Add known network"
    // instead, which works regardless of AP state.
    const isAp = wifi.mode === "ap";
    scanActionsEl.hidden = isAp;
    scanHintEl.hidden = !isAp;
    retryActionsEl.hidden = !isAp;
    if (isAp) {
      scanResultsEl.innerHTML = "";
    }
  } catch (err) {
    modeEl.textContent = "unreachable";
  }
}

async function loadSavedNetworks() {
  const listEl = document.getElementById("wifi-saved");
  listEl.innerHTML = "";

  try {
    const res = await fetch("/api/network/wifi/saved");
    const saved = await res.json();

    if (saved.length === 0) {
      listEl.innerHTML = "<li class=\"wifi-empty\">No saved networks.</li>";
      return;
    }

    for (const network of saved) {
      const li = document.createElement("li");
      li.innerHTML = `<span>${network.ssid}</span>`;
      const forgetBtn = document.createElement("button");
      forgetBtn.type = "button";
      forgetBtn.textContent = "Forget";
      forgetBtn.addEventListener("click", () => forgetNetwork(network.name));
      li.appendChild(forgetBtn);
      listEl.appendChild(li);
    }
  } catch (err) {
    listEl.innerHTML = "<li class=\"wifi-empty\">Unreachable.</li>";
  }
}

async function scanWifi() {
  const listEl = document.getElementById("wifi-networks");
  const scanBtn = document.getElementById("wifi-scan-btn");
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  listEl.innerHTML = "";

  try {
    const res = await fetch("/api/network/wifi/scan");
    const networks = await res.json();

    if (networks.length === 0) {
      listEl.innerHTML = "<li class=\"wifi-empty\">No networks found.</li>";
    }

    for (const network of networks) {
      const li = document.createElement("li");
      const lock = network.secured ? " (secured)" : "";
      li.innerHTML = `<span>${network.ssid}${lock} - ${network.signal ?? "?"}%</span>`;
      const connectBtn = document.createElement("button");
      connectBtn.type = "button";
      connectBtn.textContent = "Connect";
      connectBtn.addEventListener("click", () => connectWifi(network.ssid, network.secured));
      li.appendChild(connectBtn);
      listEl.appendChild(li);
    }
  } catch (err) {
    listEl.innerHTML = "<li class=\"wifi-empty\">Scan failed.</li>";
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan networks";
  }
}

async function retryKnownNetworks() {
  const btn = document.getElementById("wifi-retry-btn");
  const statusEl = document.getElementById("wifi-retry-status");
  btn.disabled = true;
  statusEl.textContent = "Retrying (up to 10s)...";

  try {
    const res = await fetch("/api/network/wifi/retry-known", { method: "POST" });
    const result = await res.json();
    statusEl.textContent = result.message || (result.ok ? "Done." : "Failed.");
  } catch (err) {
    // Expected if this session is itself riding on the fallback AP --
    // it just dropped mid-request. loadWifiStatus() below (once
    // reachable again, whether reconnected or the AP came back) shows
    // the real outcome either way.
    statusEl.textContent = "Connection interrupted -- checking current status...";
  }

  btn.disabled = false;
  loadWifiStatus();
  loadSavedNetworks();
}

async function connectWifi(ssid, secured) {
  let password = null;
  if (secured) {
    password = window.prompt(`Password for "${ssid}":`);
    if (password === null) return;
  }

  try {
    const res = await fetch("/api/network/wifi/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid, password }),
    });
    const result = await res.json();
    if (!result.ok) {
      window.alert(`Failed to connect to "${ssid}": ${result.message}`);
    }
  } catch (err) {
    window.alert(`Failed to connect to "${ssid}".`);
  }

  loadWifiStatus();
  loadSavedNetworks();
}

async function addKnownNetwork(event) {
  event.preventDefault();
  const ssidInput = document.getElementById("wifi-add-ssid-input");
  const passwordInput = document.getElementById("wifi-add-password-input");

  const ssid = ssidInput.value.trim();
  const password = passwordInput.value.trim() || null;
  if (!ssid) return;

  try {
    const res = await fetch("/api/network/wifi/add-known", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid, password }),
    });
    const result = await res.json();
    window.alert(result.ok ? `Saved "${ssid}".` : `Failed to save "${ssid}": ${result.message}`);
    if (result.ok) {
      ssidInput.value = "";
      passwordInput.value = "";
    }
  } catch (err) {
    window.alert(`Failed to save "${ssid}".`);
  }

  loadSavedNetworks();
}

async function forgetNetwork(name) {
  if (!window.confirm(`Forget saved network "${name}"?`)) return;

  try {
    await fetch("/api/network/wifi/forget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (err) {
    // ignore, list refresh below will reflect actual state
  }

  loadSavedNetworks();
}

async function loadApStatus() {
  const statusEl = document.getElementById("ap-status");
  const ssidEl = document.getElementById("ap-current-ssid");
  const addressEl = document.getElementById("ap-address");
  const ssidInput = document.getElementById("ap-ssid-input");

  try {
    const res = await fetch("/api/network/ap");
    const config = await res.json();

    statusEl.textContent = config.active ? "broadcasting now" : "inactive (client mode active)";
    statusEl.className = config.active ? "link-up" : "";
    ssidEl.textContent = config.ssid || "-";
    addressEl.textContent = config.address || "-";

    if (document.activeElement !== ssidInput) {
      ssidInput.value = config.ssid || "";
    }
  } catch (err) {
    statusEl.textContent = "unreachable";
    statusEl.className = "";
  }
}

async function saveApConfig(event) {
  event.preventDefault();
  const ssidInput = document.getElementById("ap-ssid-input");
  const passwordInput = document.getElementById("ap-password-input");

  const ssid = ssidInput.value.trim();
  const password = passwordInput.value.trim() || null;

  try {
    const res = await fetch("/api/network/ap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid, password }),
    });
    const result = await res.json();
    window.alert(result.message);
    if (result.ok) {
      passwordInput.value = "";
    }
  } catch (err) {
    window.alert("Failed to save fallback AP configuration.");
  }

  loadApStatus();
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
  loadWifiStatus();
  loadApStatus();
  updateFooter();
}

document.getElementById("wifi-scan-btn").addEventListener("click", scanWifi);
document.getElementById("wifi-retry-btn").addEventListener("click", retryKnownNetworks);
document.getElementById("wifi-add-form").addEventListener("submit", addKnownNetwork);
document.getElementById("ap-form").addEventListener("submit", saveApConfig);

// Fixed round-robin column masonry -- see app.js's layoutCards() for
// why (stable card positions, no reflow-jumping on content changes).
function layoutCards() {
  const container = document.querySelector("main");
  const cards = Array.from(container.querySelectorAll(".card"));
  if (cards.length === 0) return;

  // See app.js's layoutCards() for why this doesn't read --gap /
  // --card-col-width directly: custom properties come back from
  // getComputedStyle as raw text, not resolved px.
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
loadSavedNetworks();
setInterval(loadAll, 5000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    loadAll();
  }
});
