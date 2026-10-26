/** The sounds a timer, alarm or reminder rings with, made in the browser (no audio files).
 * The server keeps the list too (nexus_common/widgets.py TUNES), so a new tune goes in both. */

export type TuneId = "chime" | "bell" | "marimba" | "sunrise" | "birdsong" | "harp" | "retro" | "digital" | "pulse";

/** One note: frequency in Hz (gliding to `to`), when it starts and how long it lasts in
 * seconds, its waveform and loudness (0..1). */
interface Note {
  f: number;
  at: number;
  len: number;
  wave?: OscillatorType;
  vol?: number;
  to?: number;
}

export interface Tune {
  id: TuneId;
  name: string;
  about: string;
  /** Seconds from one play to the next while it rings. */
  every: number;
  notes: Note[];
}

// Note frequencies, by name.
const N = {
  C4: 261.63, E4: 329.63, G4: 392, A4: 440, B4: 493.88,
  C5: 523.25, D5: 587.33, E5: 659.25, G5: 783.99, A5: 880, B5: 987.77,
  C6: 1046.5, D6: 1174.66, E6: 1318.51, G6: 1567.98,
};

/** A struck bell: the note and two quieter overtones, ringing on. */
const bell = (f: number, at: number): Note[] => [
  { f, at, len: 1.6, vol: 0.5 },
  { f: f * 2.01, at, len: 1.1, vol: 0.2 },
  { f: f * 3.02, at, len: 0.7, vol: 0.1 },
];

export const TUNES: Tune[] = [
  { id: "chime", name: "Chime", about: "Soft two-tone", every: 1.8, notes: [
    { f: N.A5, at: 0, len: 0.25 },
    { f: N.E5, at: 0.25, len: 0.25 },
  ] },
  { id: "bell", name: "Bell", about: "A ringing bell", every: 2.6, notes: [...bell(N.C5, 0), ...bell(N.G4, 0.6)] },
  { id: "marimba", name: "Marimba", about: "Bright and bouncy", every: 1.8, notes: [
    N.C5, N.E5, N.G5, N.C6, N.G5, N.C6,
  ].map((f, i) => ({ f, at: i * 0.13, len: 0.3, wave: "triangle" as const, vol: 0.8 })) },
  { id: "sunrise", name: "Sunrise", about: "Gentle, rising", every: 3.2, notes: [
    N.C4, N.E4, N.G4, N.C5, N.E5,
  ].map((f, i) => ({ f, at: i * 0.38, len: 1.2, vol: 0.45 })) },
  { id: "birdsong", name: "Birdsong", about: "Little chirps", every: 2.4, notes: [
    { f: 2200, to: 3300, at: 0, len: 0.09, vol: 0.35 },
    { f: 2400, to: 3600, at: 0.14, len: 0.09, vol: 0.35 },
    { f: 3000, to: 2100, at: 0.5, len: 0.16, vol: 0.3 },
    { f: 2600, to: 3800, at: 0.85, len: 0.08, vol: 0.3 },
    { f: 2800, to: 4000, at: 0.97, len: 0.08, vol: 0.3 },
  ] },
  { id: "harp", name: "Harp", about: "A soft glissando", every: 2.6, notes: [
    N.C6, N.B5, N.A5, N.G5, N.E5, N.D5, N.C5, N.A4,
  ].map((f, i) => ({ f, at: i * 0.09, len: 0.9, wave: "triangle" as const, vol: 0.5 })) },
  { id: "retro", name: "Retro", about: "8-bit tune", every: 2, notes: [
    N.E5, N.G5, N.E6, N.C6, N.D6, N.G6,
  ].map((f, i) => ({ f, at: i * 0.11, len: 0.1, wave: "square" as const, vol: 0.25 })) },
  { id: "digital", name: "Digital", about: "Classic beep-beep", every: 1.2, notes: [0, 0.14, 0.28, 0.42].map((at) => ({
    f: 2000, at, len: 0.08, wave: "square" as const, vol: 0.2,
  })) },
  { id: "pulse", name: "Pulse", about: "Loud and urgent", every: 1, notes: [0, 0.25].flatMap((at) => [
    { f: N.A4, at, len: 0.12, wave: "sawtooth" as const, vol: 0.3 },
    { f: N.A5, at: at + 0.12, len: 0.12, wave: "sawtooth" as const, vol: 0.3 },
  ]) },
];

export const DEFAULT_TUNE: TuneId = "chime";

export function tuneOf(id: string | null | undefined): Tune {
  return TUNES.find((t) => t.id === id) ?? TUNES[0];
}

/** Play a tune once on `ctx`, `loud` (0..1) times its own loudness. */
export function playTune(ctx: AudioContext, tune: Tune, loud = 1) {
  const start = ctx.currentTime + 0.02;
  for (const n of tune.notes) {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    const t = start + n.at;
    o.type = n.wave ?? "sine";
    o.frequency.setValueAtTime(n.f, t);
    if (n.to) o.frequency.exponentialRampToValueAtTime(n.to, t + n.len);
    const peak = Math.max(0.0002, 0.25 * (n.vol ?? 1) * loud);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + Math.min(0.02, n.len / 3));
    g.gain.exponentialRampToValueAtTime(0.0001, t + n.len);
    o.connect(g).connect(ctx.destination);
    o.start(t);
    o.stop(t + n.len + 0.05);
  }
}

/** How loud the nth play is: alarms start gentler and grow over about 20 seconds. */
export function swell(seconds: number): number {
  return Math.min(1, 0.45 + seconds / 36);
}

let preview: AudioContext | null = null;

/** Play a tune once to hear it, cutting off the one playing before. */
export function previewTune(id: TuneId) {
  try {
    preview?.close().catch(() => {});
    preview = new AudioContext();
    playTune(preview, tuneOf(id));
  } catch {
    /* no audio here */
  }
}

const LAST = "nexus.tune";

/** The tune picked last in the add menu, so the next alarm starts with it. */
export function lastTune(): TuneId {
  try {
    const v = localStorage.getItem(LAST);
    return TUNES.some((t) => t.id === v) ? (v as TuneId) : DEFAULT_TUNE;
  } catch {
    return DEFAULT_TUNE;
  }
}

export function rememberTune(id: TuneId) {
  try {
    localStorage.setItem(LAST, id);
  } catch {
    /* not saved; the default comes back next time */
  }
}
