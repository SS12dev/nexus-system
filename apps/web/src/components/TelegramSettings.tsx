import { ExternalLink, Loader2, MessageSquare, Send } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { formatAgo, formatDate } from "../lib/format";
import { isOwner, type TelegramStatus } from "../lib/types";
import { useStore } from "../store/chat";
import { confirmAction, toast } from "./Feedback";

const POLL_MS = 3000;

/** Settings › Telegram: connect a Telegram account so the assistant answers there too. */
export function TelegramSettings({ onError }: { onError: (msg: string) => void }) {
  const user = useStore((s) => s.user)!;
  const assistant = useAssistantName();
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [pending, setPending] = useState<{ code: string; url: string | null; expires_at: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () =>
      api
        .telegram()
        .then((s) => {
          setStatus(s);
          if (s.link) setPending(null);
          return s;
        })
        .catch((e) => onError((e as Error).message)),
    [onError],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // While a code is out, check every few seconds whether Telegram has used it.
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => {
      if (Date.parse(pending.expires_at) < Date.now()) {
        setPending(null);
        return;
      }
      void load().then((s) => s && s.link && toast("Telegram connected"));
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [pending, load]);

  const connect = async () => {
    setBusy(true);
    try {
      const code = await api.telegramLink();
      setPending(code);
      if (code.url) window.open(code.url, "_blank", "noopener");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    const ok = await confirmAction({
      title: "Disconnect Telegram?",
      body: `${assistant} stops answering on Telegram and routines stop sending there. The chat stays in Nexus. You can connect again any time.`,
      confirmLabel: "Disconnect",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.telegramUnlink();
      await load();
      toast("Telegram disconnected");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  if (!status) return null;
  const link = status.link;
  const botUrl = status.bot_username ? `https://t.me/${status.bot_username}` : null;
  return (
    <section className="mb-6 rounded-lg border border-edge bg-panel p-4">
      <div className="mb-1 flex items-center gap-2">
        <Send size={16} className="text-sky-300" />
        <h2 className="font-medium">Telegram</h2>
        {link && <span className="ml-auto text-xs text-emerald-400">Connected</span>}
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Talk to {assistant} from Telegram on any phone: text, voice notes, photos and documents, with
        the same memory and tools as here. Conversations also show in Nexus as the chat called
        Telegram, and routines can send their results there.
      </p>
      {!status.enabled ? (
        isOwner(user) ? (
          <div className="rounded-md border border-edge bg-ink/40 p-3 text-sm text-slate-400">
            <p className="mb-2 text-slate-300">Not set up yet. It takes two minutes and is free:</p>
            <ol className="list-decimal space-y-1 pl-5">
              <li>
                In Telegram, message <span className="text-slate-200">@BotFather</span>, send{" "}
                <code>/newbot</code> and copy the token it gives you.
              </li>
              <li>
                On the Mac: <code>./scripts/secrets.sh set TELEGRAM_BOT_TOKEN</code>, then{" "}
                <code>openssl rand -hex 32 | ./scripts/secrets.sh set TELEGRAM_WEBHOOK_SECRET</code>.
              </li>
              <li>
                Restart with <code>./scripts/compose.sh --profile tunnel up -d</code> and come back here.
              </li>
            </ol>
          </div>
        ) : (
          <p className="text-sm text-slate-400">The owner hasn't set Telegram up on this Nexus yet.</p>
        )
      ) : link ? (
        <div className="space-y-3 text-sm">
          <p className="text-slate-300">
            {link.tg_username ? `@${link.tg_username}` : (link.tg_name ?? "Your Telegram account")}
            <span className="text-slate-500">
              {" "}
              · connected {formatDate(link.linked_at)}
              {link.last_message_at && ` · last message ${formatAgo(link.last_message_at)}`}
            </span>
          </p>
          <div className="flex flex-wrap gap-2">
            {botUrl && (
              <a
                href={botUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 hover:bg-edge/60"
              >
                <ExternalLink size={14} /> Open in Telegram
              </a>
            )}
            {link.chat_id && (
              <Link
                to={`/c/${link.chat_id}`}
                className="inline-flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 hover:bg-edge/60"
              >
                <MessageSquare size={14} /> Open the chat here
              </Link>
            )}
            <button
              onClick={() => void disconnect()}
              className="rounded-md border border-rose-500/30 px-3 py-1.5 text-rose-300 hover:bg-rose-500/10"
            >
              Disconnect
            </button>
          </div>
          <p className="text-xs text-slate-500">
            In Telegram: /new starts a fresh chat, /unlink disconnects, /help lists both.
          </p>
        </div>
      ) : pending ? (
        <div className="space-y-2 text-sm">
          <p className="flex items-center gap-2 text-slate-300">
            <Loader2 size={14} className="animate-spin" /> Waiting for Telegram… tap Start in the
            chat with {status.bot_username ? `@${status.bot_username}` : "the bot"}.
          </p>
          {pending.url && (
            <a
              href={pending.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 font-medium text-ink hover:bg-sky-400"
            >
              <ExternalLink size={14} /> Open Telegram
            </a>
          )}
          <p className="text-xs text-slate-500">
            Or send <code className="select-all">/start {pending.code}</code> to the bot. The code
            works once, for ten minutes.
          </p>
        </div>
      ) : (
        <button
          onClick={() => void connect()}
          disabled={busy}
          className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
        >
          {busy ? "Opening…" : "Connect Telegram"}
        </button>
      )}
    </section>
  );
}
