import { api } from "./api";

/** Web push on this device: the browser's own permission and subscription, kept in step with
 * the server (nexus_common/push.py). */

export type PushSupport = "ok" | "unsupported" | "ios-needs-install";

export function pushSupport(): PushSupport {
  if (typeof window === "undefined") return "unsupported";
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const installed =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as unknown as { standalone?: boolean }).standalone === true;
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    return ios && !installed ? "ios-needs-install" : "unsupported";
  }
  return "ok";
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  // The PWA plugin registers the worker; in development there may be none, so register it.
  return (await navigator.serviceWorker.getRegistration()) ?? navigator.serviceWorker.ready;
}

/** This device's subscription, if it has one. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== "ok") return null;
  return await (await registration()).pushManager.getSubscription();
}

/** Ask for permission (must follow a tap) and subscribe this device. Throws with a message
 * that can be shown when it can't. */
export async function enablePush(publicKey: string): Promise<void> {
  const support = pushSupport();
  if (support === "ios-needs-install") {
    throw new Error("On iPhone and iPad, add Nexus to your Home Screen first, then open it from there.");
  }
  if (support !== "ok") throw new Error("This browser can't show notifications.");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notifications are blocked for this site. Allow them in the browser's settings.");
  }
  const reg = await registration();
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) }));
  const json = sub.toJSON() as { endpoint: string; keys: { p256dh: string; auth: string } };
  await api.pushSubscribe({ endpoint: json.endpoint, keys: json.keys });
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await api.pushUnsubscribe(sub.endpoint);
  await sub.unsubscribe();
}
