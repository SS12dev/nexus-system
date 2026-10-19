"""Render a chart from a JSON spec with matplotlib. See SKILL.md for the format.

python chart.py spec.json out.png
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

PALETTE = ["#2563EB", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#06B6D4", "#EC4899", "#84CC16"]

FORMATS = {
    "percent": lambda v, _: f"{v:.0%}" if abs(v) <= 1.5 else f"{v:.0f}%",
    "thousands": lambda v, _: f"{v / 1e3:,.0f}k",
    "millions": lambda v, _: f"{v / 1e6:,.1f}M",
    "currency": lambda v, _: f"${v:,.0f}",
}


def render(spec: dict, out: Path) -> None:
    kind = spec.get("type", "bar")
    series = spec.get("series") or []
    if not series:
        raise SystemExit("spec needs at least one series")
    x = spec.get("x") or list(range(1, len(series[0]["values"]) + 1))
    colors = spec.get("colors") or PALETTE
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "axes.titlesize": 14,
            "axes.titlelocation": "left",
        }
    )
    fig, ax = plt.subplots(figsize=(spec.get("width", 10), spec.get("height", 5.6)))
    labels = spec.get("value_labels", False)

    if kind in ("pie", "donut"):
        s = series[0]
        wedges, *_ = ax.pie(
            s["values"],
            labels=x,
            colors=colors,
            startangle=90,
            counterclock=False,
            autopct="%1.0f%%",
            pctdistance=0.78 if kind == "donut" else 0.6,
            wedgeprops={"width": 0.42} if kind == "donut" else None,
        )
        ax.axis("equal")
    elif kind in ("bar", "barh", "stacked_bar"):
        n = len(series)
        idx = list(range(len(x)))
        width = 0.8 / (1 if kind == "stacked_bar" else n)
        bottoms = [0.0] * len(x)
        for i, s in enumerate(series):
            vals = s["values"]
            if kind == "stacked_bar":
                bars = ax.bar(
                    idx,
                    vals,
                    0.6,
                    bottom=bottoms,
                    label=s.get("name"),
                    color=colors[i % len(colors)],
                )
                bottoms = [b + v for b, v in zip(bottoms, vals, strict=True)]
            elif kind == "barh":
                pos = [p + (i - (n - 1) / 2) * width for p in idx]
                bars = ax.barh(pos, vals, width, label=s.get("name"), color=colors[i % len(colors)])
            else:
                pos = [p + (i - (n - 1) / 2) * width for p in idx]
                bars = ax.bar(pos, vals, width, label=s.get("name"), color=colors[i % len(colors)])
            if labels:
                ax.bar_label(
                    bars,
                    fmt="%g",
                    padding=2,
                    fontsize=9,
                    label_type="center" if kind == "stacked_bar" else "edge",
                )
        if kind == "barh":
            ax.set_yticks(idx, x)
            ax.invert_yaxis()
            ax.grid(axis="x", alpha=0.3)
        else:
            ax.set_xticks(
                idx,
                x,
                rotation=30 if max(len(str(v)) for v in x) > 8 else 0,
                ha="right" if max(len(str(v)) for v in x) > 8 else "center",
            )
            ax.grid(axis="y", alpha=0.3)
    elif kind in ("line", "area"):
        for i, s in enumerate(series):
            c = colors[i % len(colors)]
            ax.plot(
                x,
                s["values"],
                marker="o" if len(x) <= 24 else None,
                label=s.get("name"),
                color=c,
                linewidth=2,
            )
            if kind == "area":
                ax.fill_between(x, s["values"], alpha=0.15, color=c)
            if labels:
                for xv, yv in zip(x, s["values"], strict=False):
                    ax.annotate(
                        f"{yv:g}",
                        (xv, yv),
                        textcoords="offset points",
                        xytext=(0, 6),
                        ha="center",
                        fontsize=9,
                    )
        ax.grid(axis="y", alpha=0.3)
    elif kind == "scatter":
        for i, s in enumerate(series):
            ax.scatter(
                s.get("x", x),
                s["values"],
                label=s.get("name"),
                color=colors[i % len(colors)],
                alpha=0.8,
            )
        ax.grid(alpha=0.3)
    else:
        raise SystemExit(f"unknown chart type {kind!r}")

    if spec.get("title"):
        ax.set_title(spec["title"], pad=12)
    if spec.get("x_label"):
        ax.set_xlabel(spec["x_label"])
    if spec.get("y_label"):
        ax.set_ylabel(spec["y_label"])
    if fmt := FORMATS.get(spec.get("y_format", "")):
        (ax.xaxis if kind == "barh" else ax.yaxis).set_major_formatter(FuncFormatter(fmt))
    if len(series) > 1 or (kind == "scatter" and series[0].get("name")):
        if kind not in ("pie", "donut"):
            ax.legend(frameon=False, loc="best")
    if spec.get("source"):
        fig.text(0.01, 0.01, f"Source: {spec['source']}", fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03 if spec.get("source") else 0, 1, 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=spec.get("dpi", 200), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("output", type=Path)
    a = ap.parse_args()
    render(json.loads(a.spec.read_text("utf-8")), a.output)
    print(f"Wrote {a.output}")


if __name__ == "__main__":
    main()
