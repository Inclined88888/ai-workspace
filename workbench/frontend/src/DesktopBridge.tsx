// Lets the desktop shell steer the page: aiworkspace:// links and the tray menu (desktop/src-tauri/src/native.rs).
// Renders nothing; in a browser it does nothing at all.
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { DesktopLink, desktopReady, inDesktopShell } from "./desktop";

declare global {
  interface Window { __aiwRoute?: (link: DesktopLink) => void }
}

/** Only plain in-app paths; the shell already checks this, the page checks again. */
export const safePath = (p: unknown): p is string => typeof p === "string" && /^\/[A-Za-z0-9_\-/]*$/.test(p) && !p.startsWith("//") && !/^\/api(\/|$)/i.test(p);

export default function DesktopBridge() {
  const navigate = useNavigate();
  useEffect(() => {
    if (!inDesktopShell()) return;
    const go = (link: DesktopLink | null) => {
      if (!link || !safePath(link.path)) return;
      // A draft only fills the composer; the user still reads it and presses Send.
      navigate(link.path, link.draft ? { state: { draft: String(link.draft).slice(0, 4000) } } : undefined);
    };
    window.__aiwRoute = go;
    desktopReady().then(go).catch(() => {});
    return () => { if (window.__aiwRoute === go) delete window.__aiwRoute; };
  }, [navigate]);
  return null;
}
