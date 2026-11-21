/** The morning brief: what the Brief page shows and the settings that shape it. */

export type BriefSection = "weather" | "calendar" | "tasks" | "money";
export const BRIEF_SECTIONS: { key: BriefSection; label: string; hint: string }[] = [
  { key: "weather", label: "Weather and air", hint: "From Open-Meteo, for the place you pick." },
  { key: "calendar", label: "Calendar", hint: "Today's events from Google Calendar." },
  { key: "tasks", label: "Tasks", hint: "Overdue, due today, in progress and this week's focus." },
  { key: "money", label: "Money", hint: "Safe to spend, payments due soon and the next safety step." },
];

export interface BriefWeather {
  low_c: number;
  high_c: number;
  now_c: number | null;
  code: number | null;
  sky: string;
  rain_chance: number | null;
  rain_from: string | null;
  aqi: number | null;
  air: string | null;
}

export interface BriefEvent {
  title: string;
  all_day: boolean;
  start: string | null;
  end: string | null;
  starts: string | null;
  ends: string | null;
  where: string | null;
  video: string | null;
  link: string | null;
  past: boolean;
  others: number;
}

export interface BriefTask {
  id: string;
  title: string;
  type: string;
  status: string;
  due_on: string | null;
  priority: number;
}

export interface BriefMoney {
  safe: { known: boolean; left_paise: number; per_day_paise: number; days_left: number; status: string };
  due: { kind: "card" | "emi" | "subscription"; label: string; amount_paise: number; on: string; overdue: boolean }[];
  yesterday: { spent_paise: number; count: number };
  verdict: "good" | "watch" | "act";
  next_step: { title: string; value: string; status: string } | null;
  /** Advice items marked "do now", up to three. */
  advice?: { title: string; url: string }[];
}

export interface Brief {
  date: string;
  greeting: string;
  generated_at: string;
  headline: string;
  sections: BriefSection[];
  place: string | null;
  weather: BriefWeather | null;
  calendar: { connected: boolean; events: BriefEvent[] } | null;
  tasks: {
    overdue: BriefTask[];
    due: BriefTask[];
    doing: BriefTask[];
    focus: BriefTask[];
    more: number;
    inbox: number;
    review_done: boolean;
    monday: boolean;
  } | null;
  money: BriefMoney | null;
  errors: Partial<Record<BriefSection, string>>;
}

export interface BriefSettings {
  enabled: boolean;
  send_at: string;
  days: "every" | "weekdays";
  push: boolean;
  telegram: boolean;
  telegram_money: boolean;
  place: string | null;
  lat: number | null;
  lon: number | null;
  sections: BriefSection[];
  last_sent_on: string | null;
}

export interface BriefPage {
  brief: Brief;
  settings: BriefSettings;
  telegram_linked: boolean;
  money_available: boolean;
}

export interface Place {
  name: string;
  region: string;
  lat: number;
  lon: number;
}

/** "07:30" -> "7:30 am", the way the brief writes times. */
export function clockText(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return hhmm;
  const suffix = h < 12 ? "am" : "pm";
  const hour = h % 12 || 12;
  return m ? `${hour}:${String(m).padStart(2, "0")} ${suffix}` : `${hour} ${suffix}`;
}

/** When the next scheduled brief goes, in words: "Tomorrow at 7:30 am", "Monday at 7 am". */
export function nextSend(s: BriefSettings, now: Date = new Date()): string | null {
  if (!s.enabled) return null;
  const [h, m] = s.send_at.split(":").map(Number);
  const today = now.toLocaleDateString("en-CA");
  for (let i = 0; i < 8; i++) {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i, h, m);
    const weekend = day.getDay() === 0 || day.getDay() === 6;
    if (s.days === "weekdays" && weekend) continue;
    if (i === 0 && (day <= now || s.last_sent_on === today)) continue;
    const when = i === 0 ? "Today" : i === 1 ? "Tomorrow" : day.toLocaleDateString("en-GB", { weekday: "long" });
    return `${when} at ${clockText(s.send_at)}`;
  }
  return null;
}
