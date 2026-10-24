import { BellRing } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { currentSubscription, disablePush, enablePush, pushSupport } from "../lib/push";
import { toast } from "./Feedback";

/** Notifications on this device: turn them on (the browser asks), send a test, or turn them
 * off. Routine results with "Push" ticked and steps waiting for approval arrive here. */
export function PushSettings({ onError }: { onError: (msg: string) => void }) {
  const [state, setState] = useState<{ enabled: boolean; public_key: string | null; devices: number } | null>(null);
  const [here, setHere] = useState(false);
  const [busy, setBusy] = useState(false);
  const support = pushSupport();

  const load = async () => {
    try {
      setState(await api.pushState());
      setHere(!!(await currentSubscription()));
    } catch (e) {
      onError((e as Error).message);
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!state?.enabled) return null;

  const run = async (fn: () => Promise<void>, done: string) => {
    setBusy(true);
    try {
      await fn();
      toast(done);
      await load();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mb-6 rounded-lg border border-edge bg-panel p-4">
      <h2 className="mb-1 flex items-center gap-2 font-medium">
        <BellRing size={16} className="text-sky-300" /> Notifications
      </h2>
      <p className="mb-3 text-xs text-slate-500">
        Get a notification on this phone or computer when a routine has something for you (tick
        Push on the routine) and when a team is waiting for your approval.{" "}
        {state.devices > 0 && `On for ${state.devices} device${state.devices === 1 ? "" : "s"}.`}
      </p>
      {support === "ios-needs-install" && (
        <p className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
          On iPhone and iPad (iOS 16.4 or later): tap Share, then <em>Add to Home Screen</em>, open
          Nexus from the Home Screen, and turn notifications on here.
        </p>
      )}
      {support === "unsupported" && (
        <p className="mb-3 text-xs text-slate-400">This browser can't show notifications.</p>
      )}
      <div className="flex flex-wrap gap-2 text-sm">
        {support === "ok" && !here && (
          <button
            disabled={busy}
            onClick={() => run(() => enablePush(state.public_key!), "Notifications are on")}
            className="rounded-md bg-sky-500 px-3 py-1.5 font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
          >
            Turn on for this device
          </button>
        )}
        {here && (
          <>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const { sent } = await api.pushTest();
                  if (!sent) throw new Error("No device took it. Turn notifications off and on again.");
                }, "Sent. It should arrive in a few seconds.")
              }
              className="rounded-md border border-edge px-3 py-1.5 hover:bg-edge/60"
            >
              Send a test
            </button>
            <button
              disabled={busy}
              onClick={() => run(disablePush, "Notifications are off on this device")}
              className="rounded-md border border-edge px-3 py-1.5 hover:bg-edge/60"
            >
              Turn off here
            </button>
          </>
        )}
      </div>
    </section>
  );
}
