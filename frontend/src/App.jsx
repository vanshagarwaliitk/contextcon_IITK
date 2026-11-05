import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import Home from "./components/Home.jsx";
import Investigation from "./components/Investigation.jsx";

function useHashRoute() {
  const read = () => window.location.hash.replace(/^#\/?/, "");
  const [route, setRoute] = useState(read());
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

function useTheme() {
  const initial = (() => {
    try {
      return localStorage.getItem("coredata-theme");
    } catch {
      return null;
    }
  })() || (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const [theme, setTheme] = useState(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("coredata-theme", theme);
    } catch {}
  }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}

export default function App() {
  const route = useHashRoute();
  const [theme, toggleTheme] = useTheme();
  const [health, setHealth] = useState(null);
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ ok: false }));
  }, []);
  const invId = route.startsWith("inv/") ? route.slice(4) : null;

  return (
    <>
      <header className="topbar">
        <div className="logo" onClick={() => (window.location.hash = "")}>
          <div className="logo-mark">C</div> CoreData
        </div>
        <span className="faint tagline">competitor intelligence from public signals</span>
        <div className="spacer" />
        {health && (
          <span className={`pill ${health.provider_mode === "live" ? "good" : "bad"}`}>
            {health.ok === false ? "API offline" : health.provider_mode === "live" ? "Live data" : "Replay data"}
          </span>
        )}
        {health?.model && <span className="pill violet model">{health.model}</span>}
        <button className="ghost small" onClick={toggleTheme} aria-label="Toggle theme">
          {theme === "dark" ? "Light" : "Dark"}
        </button>
      </header>
      <main className="page">{invId ? <Investigation id={invId} /> : <Home health={health} />}</main>
    </>
  );
}
