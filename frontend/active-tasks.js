// Shared across every page (see each .html's <script> include right
// after </header>) -- background jobs (ping/MTR/capture/IP scan/port
// scan) run on the Pi independently of any page, including one you've
// navigated away from or closed entirely, so this is the only place
// that's always visible showing what's still active. Polls every
// tool's own status endpoint directly rather than relying on
// individual pages to report in, since whichever page you're
// currently on may not be the one that started the job.
//
// ARP scan has no entry here -- it's a synchronous one-shot request
// (backend/tools/arp_scan.py has no start/status/stop, no background
// process), so there's no "still running elsewhere" state for it to
// report.
//
// window.lanpiLocalActiveTasks: for things that are NOT a backend
// background job and so have no status endpoint to poll -- Modbus
// Read's auto-refresh interval is a plain client-side setInterval, not
// a Popen/thread on the Pi (unlike Modbus Poll, its background-job
// cousin), so it genuinely stops the moment you navigate away. A page
// that has such a thing running adds its own name here directly (see
// modbus.js); it naturally disappears on any other page since that
// page's own JS never added it.
window.lanpiLocalActiveTasks = new Set();

const _ACTIVE_TASK_SOURCES = [
  { name: "Ping", url: "/api/tools/ping/status" },
  { name: "MTR", url: "/api/tools/mtr/status" },
  { name: "Capture", url: "/api/capture/status" },
  { name: "IP Scan", url: "/api/tools/ip-scan/status" },
  { name: "Port Scan", url: "/api/tools/port-scan/status" },
  { name: "Modbus Unit Scan", url: "/api/tools/modbus/unit-scan/status" },
  { name: "Modbus Register Scan", url: "/api/tools/modbus/register-scan/status" },
  { name: "Modbus Poll", url: "/api/tools/modbus/poll/status" },
];

async function _refreshActiveTasks() {
  const badge = document.getElementById("active-tasks-badge");
  const label = document.getElementById("active-tasks-label");
  if (!badge || !label) return;

  const results = await Promise.all(
    _ACTIVE_TASK_SOURCES.map(async (source) => {
      try {
        const res = await fetch(source.url);
        const data = await res.json();
        return data.running ? source.name : null;
      } catch (err) {
        return null;
      }
    })
  );

  const active = results.filter(Boolean);
  for (const name of window.lanpiLocalActiveTasks) {
    active.push(name);
  }
  if (active.length === 0) {
    badge.hidden = true;
  } else {
    badge.hidden = false;
    label.textContent = active.join(", ");
  }
}

_refreshActiveTasks();
setInterval(_refreshActiveTasks, 3000);
