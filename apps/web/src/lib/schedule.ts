/** Routine schedules are RRULEs (see nexus_common/routines.py). The picker covers the common
 * shapes; anything else is kept as written and edited as text. */

export const DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"] as const;
export type Day = (typeof DAYS)[number];
export const DAY_LABELS: Record<Day, string> = {
  MO: "Mon",
  TU: "Tue",
  WE: "Wed",
  TH: "Thu",
  FR: "Fri",
  SA: "Sat",
  SU: "Sun",
};

export type Repeat = "daily" | "weekdays" | "weekly" | "monthly";

export interface SimpleSchedule {
  repeat: Repeat;
  /** For weekly. */
  days: Day[];
  /** For monthly, 1 to 28. */
  monthday: number;
  /** "HH:MM" */
  time: string;
}

export const DEFAULT_SCHEDULE: SimpleSchedule = {
  repeat: "daily",
  days: ["MO"],
  monthday: 1,
  time: "08:00",
};

export function toRule(s: SimpleSchedule): string {
  const [h, m] = s.time.split(":").map((x) => parseInt(x, 10));
  const at = `BYHOUR=${h};BYMINUTE=${m}`;
  switch (s.repeat) {
    case "daily":
      return `FREQ=DAILY;${at}`;
    case "weekdays":
      return `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;${at}`;
    case "monthly":
      return `FREQ=MONTHLY;BYMONTHDAY=${s.monthday};${at}`;
    case "weekly": {
      const days = DAYS.filter((d) => s.days.includes(d));
      return `FREQ=WEEKLY;BYDAY=${(days.length ? days : ["MO"]).join(",")};${at}`;
    }
  }
}

/** The picker's form of a rule, or null when the rule is something the picker can't show. */
export function fromRule(rule: string): SimpleSchedule | null {
  const fields: Record<string, string> = {};
  for (const part of rule.replace(/^RRULE:/, "").split(";")) {
    const [k, v] = part.split("=");
    if (k && v !== undefined) fields[k.toUpperCase()] = v.toUpperCase();
  }
  const known = new Set(["FREQ", "BYDAY", "BYMONTHDAY", "BYHOUR", "BYMINUTE"]);
  if (Object.keys(fields).some((k) => !known.has(k))) return null;
  const hour = Number(fields.BYHOUR);
  const minute = Number(fields.BYMINUTE ?? "0");
  if (!/^\d+$/.test(fields.BYHOUR ?? "") || hour > 23 || !(minute >= 0 && minute < 60)) {
    return null;
  }
  const time = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  if (fields.FREQ === "DAILY" && !fields.BYDAY && !fields.BYMONTHDAY) {
    return { ...DEFAULT_SCHEDULE, repeat: "daily", time };
  }
  if (fields.FREQ === "WEEKLY" && fields.BYDAY) {
    const days = fields.BYDAY.split(",") as Day[];
    if (!days.every((d) => DAYS.includes(d))) return null;
    if (days.join(",") === "MO,TU,WE,TH,FR") return { ...DEFAULT_SCHEDULE, repeat: "weekdays", time };
    return { ...DEFAULT_SCHEDULE, repeat: "weekly", days, time };
  }
  if (fields.FREQ === "MONTHLY" && /^\d+$/.test(fields.BYMONTHDAY ?? "")) {
    const monthday = Number(fields.BYMONTHDAY);
    if (monthday < 1 || monthday > 28) return null;
    return { ...DEFAULT_SCHEDULE, repeat: "monthly", monthday, time };
  }
  return null;
}

/** The browser's time zone, e.g. "Asia/Kolkata". */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Every time zone the browser knows, for pickers; the given one first if it's missing. */
export function timezones(current?: string | null): string[] {
  let all: string[] = [];
  try {
    all = (Intl as unknown as { supportedValuesOf(k: string): string[] }).supportedValuesOf(
      "timeZone",
    );
  } catch {
    all = [];
  }
  if (!all.includes("UTC")) all = ["UTC", ...all];
  return current && !all.includes(current) ? [current, ...all] : all;
}

/** "Mon 27 Oct, 08:00" in the given zone. */
export function formatInZone(iso: string, zone: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: zone,
    }).format(new Date(iso));
  } catch {
    return new Date(iso).toLocaleString();
  }
}
