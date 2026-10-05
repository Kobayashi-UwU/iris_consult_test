"""Adverse-impact monitoring (four-fifths rule). Reads demographics only; never feeds the AI."""
import pandas as pd

FOUR_FIFTHS = 0.8
SMALL_N = 5


def impact_table(df: pd.DataFrame, group_col: str, selected_col: str) -> pd.DataFrame:
    """Selection rate per group and its ratio to the highest-rate group."""
    g = df.groupby(group_col)[selected_col].agg(["count", "sum"]).reset_index()
    g.columns = ["group", "n", "selected"]
    g["selection_rate"] = (g["selected"] / g["n"]).round(3)
    top = g["selection_rate"].max()
    g["impact_ratio"] = (g["selection_rate"] / top).round(2) if top > 0 else 1.0
    g["small_sample"] = g["n"] < SMALL_N

    def status(r) -> str:
        if top == 0:
            return "No selections yet"
        if r["impact_ratio"] < FOUR_FIFTHS:
            return "Below 0.8 — investigate" + (" (small n)" if r["small_sample"] else "")
        return "OK" + (" (small n)" if r["small_sample"] else "")

    g["status"] = g.apply(status, axis=1)
    return g.sort_values("selection_rate", ascending=False).reset_index(drop=True)
