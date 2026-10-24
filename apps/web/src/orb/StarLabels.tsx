import type { StarPoint } from "./model";

/** The stars' names, as page elements over the canvas; the scene positions and shows them. */
export function StarLabels({
  stars,
  lit,
  tags,
}: {
  stars: StarPoint[];
  lit: Set<string>;
  tags: Map<string, HTMLElement>;
}) {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {stars.map((s) => (
        <div
          key={s.id}
          ref={(el) => {
            if (el) tags.set(s.id, el);
            else tags.delete(s.id);
          }}
          style={{ position: "absolute", left: 0, top: 0, opacity: 0, transition: "opacity 150ms" }}
          className={`orb-label orb-label-star${lit.has(s.id) ? " orb-label-lit" : ""}`}
        >
          {s.self ? "You" : s.name}
        </div>
      ))}
    </div>
  );
}
