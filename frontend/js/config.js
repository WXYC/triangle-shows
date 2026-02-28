// API configuration
const API_BASE = window.location.origin;

// Spotify — paste your Client ID from https://developer.spotify.com/dashboard
// Register redirect URIs: https://triangle-shows.org and http://localhost:8000
const SPOTIFY_CLIENT_ID = "";

// Color palettes — each entry maps CSS custom property names to values.
// applyPalette() sets these on :root and persists the choice to localStorage.
const PALETTES = {
  amber: {
    label: "Amber",
    accent: "#c87941",
    vars: {
      "--bg":           "#1a1008",
      "--surface":      "#241609",
      "--surface2":     "#2e1d0c",
      "--border":       "#3d2a12",
      "--text":         "#e8d5b0",
      "--muted":        "#9a7a50",
      "--dim":          "#5a4020",
      "--accent":       "#c87941",
      "--accent-hover": "#e09050",
      "--accent-bg":    "rgba(200,121,65,0.10)",
      "--today-bg":     "rgba(200,121,65,0.05)",
    },
  },
  phosphor: {
    label: "Phosphor",
    accent: "#44c754",
    vars: {
      "--bg":           "#060d07",
      "--surface":      "#0c1a0d",
      "--surface2":     "#122014",
      "--border":       "#1e3820",
      "--text":         "#a8d8ac",
      "--muted":        "#4e8858",
      "--dim":          "#28502e",
      "--accent":       "#44c754",
      "--accent-hover": "#68e078",
      "--accent-bg":    "rgba(68,199,84,0.10)",
      "--today-bg":     "rgba(68,199,84,0.05)",
    },
  },
  midnight: {
    label: "Midnight",
    accent: "#4888e8",
    vars: {
      "--bg":           "#050810",
      "--surface":      "#0a0f20",
      "--surface2":     "#101828",
      "--border":       "#1a2640",
      "--text":         "#c0cce0",
      "--muted":        "#4868a0",
      "--dim":          "#1a2e50",
      "--accent":       "#4888e8",
      "--accent-hover": "#68a8ff",
      "--accent-bg":    "rgba(72,136,232,0.12)",
      "--today-bg":     "rgba(72,136,232,0.05)",
    },
  },
  wisteria: {
    label: "Wisteria",
    accent: "#c060d0",
    vars: {
      "--bg":           "#0e0810",
      "--surface":      "#180e1c",
      "--surface2":     "#201428",
      "--border":       "#361e40",
      "--text":         "#e0c8e8",
      "--muted":        "#906898",
      "--dim":          "#4a2858",
      "--accent":       "#c060d0",
      "--accent-hover": "#d880e8",
      "--accent-bg":    "rgba(192,96,208,0.12)",
      "--today-bg":     "rgba(192,96,208,0.05)",
    },
  },
};

function applyPalette(key) {
  if (!PALETTES[key]) return;
  // Drive palette entirely through CSS: html[data-palette="..."] selectors in styles.css
  document.documentElement.dataset.palette = key;
  localStorage.setItem("triangle-shows-palette", key);
  document.querySelectorAll(".palette-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.palette === key);
  });
}

// Light / dark mode toggle
function applyMode(mode) {
  document.documentElement.dataset.mode = mode;
  localStorage.setItem("triangle-shows-mode", mode);
  const btn = document.getElementById("mode-toggle");
  if (btn) btn.textContent = mode === "light" ? "☾ dark" : "☀ light";
}

function toggleMode() {
  const current = document.documentElement.dataset.mode || "dark";
  applyMode(current === "dark" ? "light" : "dark");
}

// Dynamically shrink the ASCII title font so it never overlaps the header-right controls.
// Runs on load and on every resize; only active when the ASCII art is visible (desktop).
function fitAsciiTitle() {
  const header   = document.querySelector(".app-header");
  const title    = document.querySelector(".ascii-title");
  const rightBar = document.querySelector(".header-right");
  if (!header || !title) return;
  if (getComputedStyle(title).display === "none") return; // hidden on mobile

  // Leave room for the header-right buttons; fall back to 140px if not found.
  const headerW  = header.getBoundingClientRect().width;
  const rightW   = rightBar ? rightBar.getBoundingClientRect().width + 32 : 140;
  const available = headerW - rightW;

  // Step down from 14 → 7px until the title fits
  for (let size = 14; size >= 7; size -= 0.5) {
    title.style.fontSize = size + "px";
    if (title.scrollWidth <= available) return;
  }
}

document.addEventListener("DOMContentLoaded", fitAsciiTitle);
window.addEventListener("resize", fitAsciiTitle);

// City color mappings — jewel-tone palette, matches venue color families
// border = chip border/text color; activeBg = subtle tint for active state
const CITY_COLORS = {
  Raleigh:                { border: "#a83850", activeBg: "rgba(168,56,80,0.16)" },    // ruby
  Durham:                 { border: "#2a6098", activeBg: "rgba(42,96,152,0.16)" },    // sapphire
  "Chapel Hill-Carrboro": { border: "#2a7a50", activeBg: "rgba(42,122,80,0.16)" },   // emerald
  Saxapahaw:              { border: "#8a30a8", activeBg: "rgba(138,48,168,0.16)" },   // orchid
};

