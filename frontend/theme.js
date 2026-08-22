// Shared across every page (see each .html's <script> include right
// after active-tasks.js). The actual flash-prevention happens via the
// tiny inline snippet in each page's <head> (runs before style.css is
// even requested) -- this file just keeps things in sync and, on
// Settings (the only page with a #theme-select control), wires up
// switching.
const LANPI_THEME_KEY = "lanpi-theme";
const LANPI_THEME_COLORS = { win98: "#008080", default: "#0f1115" };

function _applyLanpiTheme(theme) {
  if (theme === "win98") {
    document.documentElement.setAttribute("data-theme", "win98");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", LANPI_THEME_COLORS[theme === "win98" ? "win98" : "default"]);
  }
}

let _lanpiTheme = "default";
try {
  _lanpiTheme = localStorage.getItem(LANPI_THEME_KEY) === "win98" ? "win98" : "default";
} catch (err) {
  _lanpiTheme = "default";
}
_applyLanpiTheme(_lanpiTheme);

// This script tag sits right after <header>, before <main> -- on
// Settings, #theme-select lives inside <main> and doesn't exist in
// the DOM yet at this point, so the lookup has to wait for the DOM
// to finish parsing rather than running inline here.
document.addEventListener("DOMContentLoaded", () => {
  const themeSelect = document.getElementById("theme-select");
  if (!themeSelect) return;
  themeSelect.value = _lanpiTheme;
  themeSelect.addEventListener("change", () => {
    const theme = themeSelect.value === "win98" ? "win98" : "default";
    try {
      localStorage.setItem(LANPI_THEME_KEY, theme);
    } catch (err) {
      // localStorage unavailable (private mode, etc) -- theme still
      // applies for this page load, just won't persist.
    }
    _applyLanpiTheme(theme);
  });
});
