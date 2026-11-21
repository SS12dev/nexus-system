import { Loader2, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { KIND_ICONS, SHORTCUT, Snippet } from "../components/CommandBar";
import { Guide, GuideHeading } from "../components/Guide";
import { api } from "../lib/api";
import { KIND_LABELS, KIND_ORDER, groupResults } from "../lib/find";
import type { FindKind, FindResponse } from "../lib/types";

const PER_KIND = 8;
const MORE = 40;

/** Search across everything, with room for more results than the Cmd-K bar shows. */
export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const kind = (params.get("kind") as FindKind | null) ?? null;
  const [draft, setDraft] = useState(q);
  const [found, setFound] = useState<FindResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => setDraft(q), [q]);

  useEffect(() => {
    if (!q.trim()) {
      setFound(null);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    api
      .find(q, { kinds: kind ? [kind] : undefined, limit: kind ? MORE : PER_KIND, signal: ctrl.signal })
      .then((r) => {
        setFound(r);
        setError("");
      })
      .catch((e) => (e as Error).name !== "AbortError" && setError((e as Error).message))
      .finally(() => !ctrl.signal.aborted && setLoading(false));
    return () => ctrl.abort();
  }, [q, kind]);

  const set = (next: { q?: string; kind?: FindKind | null }) => {
    const p = new URLSearchParams();
    const nq = next.q ?? q;
    if (nq) p.set("q", nq);
    const nk = next.kind === undefined ? kind : next.kind;
    if (nk) p.set("kind", nk);
    setParams(p);
  };

  const groups = groupResults(found?.results ?? []);
  const present = new Set(found?.results.map((r) => r.kind));

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">Search</h1>
        <p className="mb-4 text-sm text-slate-400">
          One search over your chats, files, memories, people, links, routines, apps, agent teams
          and skills. Press {SHORTCUT} anywhere for the quick version.
        </p>
        <Guide title="How search works">
          <div>
            <GuideHeading>Words and meaning</GuideHeading>
            Every word you type is matched as the start of a word, so “invo” finds “invoice”.
            Nexus also compares meaning, so “flat deposit” can find a chat about a rental
            agreement. Results that match both ways rank higher.
          </div>
          <div>
            <GuideHeading>What it looks in</GuideHeading>
            Every message in every chat (yours and your assistant’s), the text inside files in
            your Library, your memories, the people and things in Mind, and the names of links,
            routines, apps, teams and skills. Items in Trash are left out.
          </div>
          <div>
            <GuideHeading>Opening a result</GuideHeading>
            A chat opens at the message that matched, highlighted. Files open in the preview,
            memories and people open in Mind.
          </div>
          <div>
            <GuideHeading>Private to you</GuideHeading>
            Search only ever looks at your own things, and what you search for is not stored or
            logged.
          </div>
        </Guide>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            set({ q: draft.trim() });
          }}
          className="mb-4 flex items-center gap-2 rounded-lg border border-edge bg-panel px-3 py-2 focus-within:border-sky-500/60"
        >
          {loading ? (
            <Loader2 size={16} className="animate-spin text-slate-400" />
          ) : (
            <Search size={16} className="text-slate-400" />
          )}
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Search everything"
            className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-slate-500"
            style={{ outline: "none" }}
            autoFocus
            maxLength={200}
            enterKeyHint="search"
            aria-label="Search everything"
          />
        </form>
        {q && (
          <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Filter by kind">
            <Chip on={!kind} onClick={() => set({ kind: null })}>
              All
            </Chip>
            {KIND_ORDER.filter((k) => present.has(k) || k === kind).map((k) => (
              <Chip key={k} on={kind === k} onClick={() => set({ kind: k })}>
                {KIND_LABELS[k]}
              </Chip>
            ))}
          </div>
        )}
        {error && <p role="alert" className="mb-4 text-sm text-rose-400">{error}</p>}
        {found && q && !found.results.length && !loading && (
          <div className="rounded-xl border border-edge bg-panel p-8 text-center text-sm text-slate-400">
            Nothing matches “{q}”. Try fewer or different words.
          </div>
        )}
        {!q && (
          <div className="rounded-xl border border-edge bg-panel p-8 text-center text-sm text-slate-400">
            Type above to search everything you have in Nexus.
          </div>
        )}
        <div className="space-y-6">
          {groups.map((g) => {
            const Icon = KIND_ICONS[g.kind];
            return (
              <section key={g.kind}>
                <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
                  <Icon size={15} className="text-slate-400" /> {KIND_LABELS[g.kind]}
                </h2>
                <ul className="space-y-1.5">
                  {g.items.map((r) => (
                    <li key={r.id}>
                      <Link
                        to={r.url}
                        className="block rounded-lg border border-edge bg-panel px-3 py-2.5 hover:border-sky-500/40 hover:bg-edge/40"
                      >
                        <div className="flex items-baseline gap-2">
                          <span className="truncate font-medium">
                            <Snippet text={r.title} />
                          </span>
                          <span className="ml-auto shrink-0 text-xs text-slate-500">
                            {r.matched.includes("meaning") && !r.matched.includes("words")
                              ? "similar meaning · "
                              : ""}
                            {r.detail}
                          </span>
                        </div>
                        {r.snippet && (
                          <p className="mt-1 line-clamp-3 text-sm text-slate-400">
                            <Snippet text={r.snippet} />
                          </p>
                        )}
                      </Link>
                    </li>
                  ))}
                </ul>
                {!kind && found?.more.includes(g.kind) && (
                  <button
                    onClick={() => set({ kind: g.kind })}
                    className="mt-2 text-sm text-sky-300 hover:text-sky-200"
                  >
                    Show more {KIND_LABELS[g.kind].toLowerCase()}
                  </button>
                )}
              </section>
            );
          })}
        </div>
        {found && q && (
          <p className="mt-6 text-xs text-slate-500">
            {found.semantic
              ? "Matched on words and meaning."
              : "Matched on words only: meaning search is offline right now (is Ollama running?)."}{" "}
            Took {found.took_ms} ms.
          </p>
        )}
      </div>
    </div>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: string }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={`rounded-full border px-3 py-1 text-sm ${
        on ? "border-sky-500/60 bg-sky-500/15 text-sky-100" : "border-edge text-slate-300 hover:bg-edge/60"
      }`}
    >
      {children}
    </button>
  );
}
