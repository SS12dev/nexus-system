import { useEffect, useState } from "react";
import { api } from "../lib/api";
import {
  DAY_LABELS,
  DAYS,
  DEFAULT_SCHEDULE,
  formatInZone,
  fromRule,
  toRule,
  type Repeat,
  type SimpleSchedule,
} from "../lib/schedule";
import type { SchedulePreview } from "../lib/types";
import { Select } from "./Select";

const REPEATS: { id: Repeat; label: string }[] = [
  { id: "daily", label: "Every day" },
  { id: "weekdays", label: "Weekdays" },
  { id: "weekly", label: "On days of the week" },
  { id: "monthly", label: "Once a month" },
];

/** Pick when a routine runs. Common shapes use the picker; any other RRULE (say, from a
 * routine made in chat) is shown and edited as text. Below, the schedule in words and its
 * next runs, checked by the server. */
export function SchedulePicker({
  value,
  timezone,
  onChange,
}: {
  value: string;
  timezone: string;
  onChange: (rule: string) => void;
}) {
  const simple = fromRule(value);
  const [custom, setCustom] = useState(!simple);
  const s: SimpleSchedule = simple ?? DEFAULT_SCHEDULE;
  const set = (changes: Partial<SimpleSchedule>) => onChange(toRule({ ...s, ...changes }));
  const [preview, setPreview] = useState<SchedulePreview | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => {
      api
        .previewSchedule(value, timezone)
        .then(setPreview)
        .catch(() => setPreview(null));
    }, 300);
    return () => window.clearTimeout(t);
  }, [value, timezone]);

  return (
    <div className="space-y-2">
      {custom ? (
        <input
          aria-label="Schedule (RRULE)"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-md border border-edge bg-ink px-3 py-2 font-mono text-sm"
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Select
            aria-label="Repeat"
            value={s.repeat}
            onChange={(e) => set({ repeat: e.target.value as Repeat })}
          >
            {REPEATS.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </Select>
          {s.repeat === "monthly" && (
            <Select
              aria-label="Day of the month"
              value={s.monthday}
              onChange={(e) => set({ monthday: Number(e.target.value) })}
            >
              {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
                <option key={d} value={d}>
                  on day {d}
                </option>
              ))}
            </Select>
          )}
          <span className="text-sm text-slate-400">at</span>
          <input
            type="time"
            aria-label="Time"
            value={s.time}
            onChange={(e) => e.target.value && set({ time: e.target.value })}
            className="rounded-md border border-edge bg-ink px-2 py-1 text-base [color-scheme:dark] sm:text-sm"
          />
        </div>
      )}
      {!custom && s.repeat === "weekly" && (
        <div className="flex flex-wrap gap-1">
          {DAYS.map((d) => {
            const on = s.days.includes(d);
            return (
              <button
                key={d}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  set({ days: on ? s.days.filter((x) => x !== d) : [...s.days, d] })
                }
                className={`rounded-full border px-2.5 py-0.5 text-xs ${
                  on ? "border-sky-400 bg-sky-500/15 text-sky-200" : "border-edge text-slate-400"
                }`}
              >
                {DAY_LABELS[d]}
              </button>
            );
          })}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {preview?.ok && (
          <span className="text-slate-400">
            {preview.text} ({timezone}). Next:{" "}
            {preview.next.map((n) => formatInZone(n, timezone)).join(" · ")}
          </span>
        )}
        {preview && !preview.ok && <span className="text-rose-400">{preview.error}</span>}
        <button
          type="button"
          onClick={() => {
            if (custom && !fromRule(value)) onChange(toRule(DEFAULT_SCHEDULE));
            setCustom(!custom);
          }}
          className="text-sky-300 hover:underline"
        >
          {custom ? "Use the picker" : "Write a custom rule"}
        </button>
      </div>
    </div>
  );
}
