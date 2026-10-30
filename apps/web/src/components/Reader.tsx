import { ExternalLink, Globe, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { WebPage } from "../lib/types";
import { useStore } from "../store/chat";
import { Markdown } from "./Markdown";

/** A web page read inside Nexus: fetched on the server and shown as clean text over everything
 * (a sheet on phones), so a search result can be read without leaving the app. Nexus doesn't
 * embed other sites (most refuse to be framed), so pages that need the real site (video,
 * sign-ins, apps) have Open in browser. */
export function Reader() {
  const url = useStore((s) => s.reader);
  const close = useStore((s) => s.openReader);
  const [page, setPage] = useState<WebPage | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!url) return;
    let live = true;
    setPage(null);
    setError("");
    api
      .webRead(url)
      .then((p) => live && setPage(p))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Couldn't open that page"));
    const esc = (e: KeyboardEvent) => e.key === "Escape" && close(null);
    document.addEventListener("keydown", esc);
    return () => {
      live = false;
      document.removeEventListener("keydown", esc);
    };
  }, [url, close]);

  if (!url) return null;
  const site = page?.site ?? safeHost(url);
  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/60 backdrop-blur-sm sm:p-6"
      onMouseDown={(e) => e.target === e.currentTarget && close(null)}
    >
      <article
        role="dialog"
        aria-label={page?.title ?? "Web page"}
        className="flex min-h-0 w-full max-w-3xl flex-col overflow-hidden border-edge bg-panel shadow-2xl sm:rounded-2xl sm:border"
      >
        <header className="flex shrink-0 items-center gap-2 border-b border-edge px-3 py-2">
          <Globe size={15} className="shrink-0 text-teal-300" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-slate-100">{page?.title ?? "Opening…"}</p>
            <p className="truncate text-[11px] text-slate-400">{site} · reading view</p>
          </div>
          <a
            href={page?.url ?? url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex shrink-0 items-center gap-1 rounded-md border border-edge px-2 py-1 text-xs text-slate-300 hover:border-sky-400/60 hover:text-white"
          >
            <ExternalLink size={12} /> Open in browser
          </a>
          <button onClick={() => close(null)} aria-label="Close" className="rounded-md p-1 text-slate-400 hover:bg-edge/60 hover:text-white">
            <X size={16} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {error ? (
            <div className="py-10 text-center">
              <p className="text-sm text-slate-300">{error}</p>
              <p className="mt-1 text-xs text-slate-500">Some sites only work in a browser.</p>
            </div>
          ) : page ? (
            <Markdown>{page.text}</Markdown>
          ) : (
            <p className="py-10 text-center text-sm text-slate-500">Loading the page…</p>
          )}
        </div>
      </article>
    </div>
  );
}

function safeHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
