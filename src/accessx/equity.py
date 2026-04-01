from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

import numpy as np
import pandas as pd


def _integrate_trapezoid(y: np.ndarray, x: np.ndarray) -> float:
    """
    NumPy-version-safe trapezoid integration.
    """
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(y, x))
    if hasattr(np, "trapz"):
        return float(np.trapz(y, x))
    # Fallback if neither alias exists in the runtime.
    return float(np.sum((y[1:] + y[:-1]) * 0.5 * (x[1:] - x[:-1])))


def calculate_lorenz(
    properties: Iterable[str],
    df: pd.DataFrame,
    *,
    weights: Optional[Union[str, Iterable[float], pd.Series]] = None,
    fillna_value: float = 0.0,
    clip_nonnegative: bool = True,
) -> Tuple[Dict[str, List[float]], Dict[str, List[float]], Dict[str, float], Dict[str, List[float]]]:
    """
    Compute Lorenz curve arrays and Gini index for each property.

    Question answered
    -----------------
    How equally is accessibility distributed across locations or people?

    Parameters
    ----------
    properties : iterable[str]
        Accessibility columns to evaluate.
    df : DataFrame
        Input table.
    weights : str | iterable[float] | Series, optional
        Optional weights for population-based equity. If provided, the Lorenz
        curve is computed over cumulative weight share rather than cumulative
        location share.
    fillna_value : float, default 0.0
        Value used for missing entries.
    clip_nonnegative : bool, default True
        If True, negative values are clipped to zero.

    Returns
    -------
    (A, P, gini, sorted_vals) : tuple of dicts
        A[prop] : cumulative accessibility share (y values)
        P[prop] : cumulative location share (x values)
        gini[prop] : Gini index
        sorted_vals[prop] : sorted raw values
    """
    properties = list(properties)
    if not properties:
        raise ValueError("properties is empty.")
    if len(df) == 0:
        raise ValueError("df is empty.")

    if isinstance(weights, str):
        if weights not in df.columns:
            raise ValueError(f"Weight column '{weights}' not found in DataFrame.")
        weight_values = pd.to_numeric(df[weights], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    elif weights is None:
        weight_values = None
    else:
        weight_values = pd.to_numeric(pd.Series(weights), errors="coerce").fillna(0.0).to_numpy(dtype=float)
        if len(weight_values) != len(df):
            raise ValueError("weights must have the same length as df.")

    if weight_values is not None:
        if np.any(weight_values < 0):
            raise ValueError("weights must be >= 0.")
        if float(weight_values.sum()) <= 0:
            raise ValueError("weights must sum to > 0.")

    A: Dict[str, List[float]] = {}
    P: Dict[str, List[float]] = {}
    gini: Dict[str, float] = {}
    sorted_vals: Dict[str, List[float]] = {}

    for prop in properties:
        if prop not in df.columns:
            raise ValueError(f"Property column '{prop}' not found in DataFrame.")

        values = pd.to_numeric(df[prop], errors="coerce").fillna(float(fillna_value)).to_numpy(dtype=float)
        if clip_nonnegative:
            values = np.clip(values, 0, None)

        if weight_values is None:
            sorted_values = np.sort(values)
            sorted_vals[prop] = sorted_values.tolist()

            n = len(sorted_values)
            cumulative_values = np.cumsum(sorted_values)

            population_share = np.arange(0, n + 1) / n
            if cumulative_values[-1] <= 0:
                accessibility_share = np.zeros(n + 1, dtype=float)
                gini_value = 0.0
            else:
                accessibility_share = np.concatenate(([0.0], cumulative_values / cumulative_values[-1]))
                area_under_curve = _integrate_trapezoid(accessibility_share, population_share)
                gini_value = float(1 - 2 * area_under_curve)
        else:
            order = np.argsort(values, kind="mergesort")
            sorted_values = values[order]
            sorted_weights = weight_values[order]
            sorted_vals[prop] = sorted_values.tolist()

            cumulative_weights = np.cumsum(sorted_weights)
            population_share = np.concatenate(([0.0], cumulative_weights / cumulative_weights[-1]))
            cumulative_weighted_values = np.cumsum(sorted_values * sorted_weights)

            if cumulative_weighted_values[-1] <= 0:
                accessibility_share = np.zeros(len(sorted_values) + 1, dtype=float)
                gini_value = 0.0
            else:
                accessibility_share = np.concatenate(
                    ([0.0], cumulative_weighted_values / cumulative_weighted_values[-1])
                )
                area_under_curve = _integrate_trapezoid(accessibility_share, population_share)
                gini_value = float(1 - 2 * area_under_curve)

        P[prop] = population_share.tolist()
        A[prop] = accessibility_share.tolist()
        gini[prop] = gini_value

    return A, P, gini, sorted_vals


def compute_sufficientarian_score(
    df: pd.DataFrame,
    *,
    thresholds_ge: Optional[Dict[str, float]] = None,
    thresholds_le: Optional[Dict[str, float]] = None,
    weights: Optional[Dict[str, float]] = None,
    indicator_suffix: str = "_sufficient",
    score_col: str = "sufficient_score",
    weighted_score_col: str = "sufficient_score_weighted",
    fillna_value: float = 0.0,
    add_indicator_cols: bool = True,
) -> pd.DataFrame:
    """
    Compute sufficientarian score from explicit threshold sets.

    Question answered
    -----------------
    What share of the selected conditions are met?

    Parameters
    ----------
    df : DataFrame
        Input table with accessibility metrics.
    thresholds_ge : dict[str, float], optional
        Metrics where higher is better:
        metric is sufficient if value >= threshold.
    thresholds_le : dict[str, float], optional
        Metrics where lower is better:
        metric is sufficient if value <= threshold.
    weights : dict[str, float], optional
        Optional metric weights for weighted sufficientarian score.
        Missing metrics default to 1.0.
    indicator_suffix : str, default "_sufficient"
        Suffix for per-metric binary indicator columns.
    score_col : str, default "sufficient_score"
        Name for unweighted score column in [0, 1].
    weighted_score_col : str, default "sufficient_score_weighted"
        Name for weighted score column in [0, 1] (only created if `weights` is provided).
    fillna_value : float, default 0.0
        Value used for missing metric values before thresholding.
    add_indicator_cols : bool, default True
        If True, add per-metric binary indicator columns.

    Returns
    -------
    DataFrame
        Copy of input with sufficientarian score columns added.
    """
    thresholds_ge = thresholds_ge or {}
    thresholds_le = thresholds_le or {}
    weights = weights or {}

    if not thresholds_ge and not thresholds_le:
        raise ValueError("At least one of thresholds_ge or thresholds_le must be provided.")

    overlap = set(thresholds_ge).intersection(set(thresholds_le))
    if overlap:
        raise ValueError(f"Metrics cannot be in both thresholds_ge and thresholds_le: {sorted(overlap)}")

    out = df.copy()
    indicator_series: Dict[str, pd.Series] = {}

    # Higher-is-better rules: value >= threshold
    for metric_col, threshold_value in thresholds_ge.items():
        if metric_col not in out.columns:
            raise ValueError(f"Missing column in DataFrame: '{metric_col}'.")
        values = pd.to_numeric(out[metric_col], errors="coerce").fillna(float(fillna_value))
        indicator = (values >= float(threshold_value)).astype(int)
        indicator_series[metric_col] = indicator
        if add_indicator_cols:
            out[f"{metric_col}{indicator_suffix}"] = indicator

    # Lower-is-better rules: value <= threshold
    for metric_col, threshold_value in thresholds_le.items():
        if metric_col not in out.columns:
            raise ValueError(f"Missing column in DataFrame: '{metric_col}'.")
        values = pd.to_numeric(out[metric_col], errors="coerce").fillna(float(fillna_value))
        indicator = (values <= float(threshold_value)).astype(int)
        indicator_series[metric_col] = indicator
        if add_indicator_cols:
            out[f"{metric_col}{indicator_suffix}"] = indicator

    indicator_df = pd.DataFrame(indicator_series, index=out.index)
    out[score_col] = indicator_df.mean(axis=1)

    if weights:
        total_weight = 0.0
        weighted_sum = pd.Series(0.0, index=out.index)
        for metric_col in indicator_df.columns:
            w = float(weights.get(metric_col, 1.0))
            if w < 0:
                raise ValueError(f"Weight for '{metric_col}' must be >= 0.")
            weighted_sum = weighted_sum + indicator_df[metric_col] * w
            total_weight += w

        if total_weight <= 0:
            raise ValueError("Sum of weights must be > 0.")
        out[weighted_score_col] = weighted_sum / total_weight

    return out


def plot_lorenz_curves(
    *,
    df: pd.DataFrame,
    properties: Iterable[str],
    weights: Optional[Union[str, Iterable[float], pd.Series]] = None,
    title: Optional[str] = None,
    show_gini_in_legend: bool = True,
    palette: str = "tab10",
    figsize: Tuple[float, float] = (10, 8),
    save_path: Optional[Union[str, Path]] = None,
):
    """
    Plot Lorenz curves for multiple accessibility properties.

    Question answered
    -----------------
    What does the inequality pattern look like visually?

    Parameters
    ----------
    df : DataFrame
        Input table.
    properties : iterable[str]
        Accessibility columns to plot.
    weights : str | iterable[float] | Series, optional
        Optional weights passed through to `calculate_lorenz`.
    title : str, optional
        Plot title. If None, defaults to "Lorenz Curves".
    show_gini_in_legend : bool, default True
        If True, legend labels include "(Gini = x.xx)".
    palette : str, default "tab10"
        Seaborn palette name.
    figsize : tuple(float, float), default (10, 8)
        Figure size in inches.
    save_path : str | Path, optional
        If provided, save the figure to this path.

    Returns
    -------
    (fig, ax, gini) : tuple
        Matplotlib figure, axis, and dictionary of Gini values per property.
    """
    try:
        import matplotlib.pyplot as plt
        import seaborn as sns
    except ImportError as exc:
        raise ImportError(
            "plot_lorenz_curves requires matplotlib and seaborn. "
            "Install them with: pip install matplotlib seaborn"
        ) from exc

    properties = list(properties)
    A, P, gini, _ = calculate_lorenz(properties=properties, df=df, weights=weights)

    sns.set_theme(style="whitegrid", palette=palette)
    fig, ax = plt.subplots(figsize=figsize)

    # Line of equality
    ax.plot([0, 1], [0, 1], "k--", linewidth=1.5, label="Line of Equality")

    colors = sns.color_palette(palette, n_colors=len(properties))
    for i, prop in enumerate(properties):
        if show_gini_in_legend:
            label = f"{prop} (Gini = {gini[prop]:.2f})"
        else:
            label = prop
        ax.plot(P[prop], A[prop], label=label, color=colors[i])

    plot_title = title if title is not None else "Lorenz Curves"

    ax.set_title(plot_title)
    ax.set_xlabel("Cumulative Share of People" if weights is not None else "Cumulative Share of Locations")
    ax.set_ylabel("Cumulative Share of Accessibility")
    ax.legend(frameon=False)
    ax.grid(True, linestyle="--", alpha=0.5)

    if save_path is not None:
        fig.savefig(save_path, dpi=300, bbox_inches="tight")

    return fig, ax, gini





def plot_sufficientarian_score(
    *,
    df: pd.DataFrame,
    score_col: str = "sufficient_score",
    title: Optional[str] = None,
    sufficiency_target: float = 0.8,
    attainment_levels: Iterable[float] = (0.25, 0.50, 0.75, 1.00),
    bins: int = 20,
    figsize: Tuple[float, float] = (12, 5),
    color: str = "#3B82F6",
    save_path: Optional[Union[str, Path]] = None,
):
    """
    Plot a dedicated sufficientarian score summary.

    The figure includes:
    - score distribution (histogram)
    - attainment shares at selected score levels

    Question answered
    -----------------
    How widespread is adequate accessibility?

    Parameters
    ----------
    df : DataFrame
        Input table.
    score_col : str, default "sufficient_score"
        Column with sufficientarian score values.
    title : str, optional
        Figure title. If None, defaults to "Sufficientarian Score".
    sufficiency_target : float, default 0.8
        Main sufficiency cutoff used for the highlighted share statistic.
    attainment_levels : iterable[float], default (0.25, 0.50, 0.75, 1.00)
        Score levels used for attainment-share bars.
    bins : int, default 20
        Histogram bins.
    figsize : tuple(float, float), default (12, 5)
        Figure size in inches.
    color : str, default "#3B82F6"
        Main plot color.
    save_path : str | Path, optional
        If provided, save figure to this path.

    Returns
    -------
    (fig, axes, summary_df) : tuple
        Matplotlib figure, axes tuple (ax_hist, ax_attain), and summary DataFrame.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError(
            "plot_sufficientarian_score requires matplotlib. "
            "Install it with: pip install matplotlib"
        ) from exc

    if score_col not in df.columns:
        raise ValueError(f"Score column '{score_col}' not found in DataFrame.")
    if bins <= 0:
        raise ValueError("bins must be > 0.")

    values = pd.to_numeric(df[score_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if len(values) == 0:
        raise ValueError("No values available to plot.")

    levels = [float(v) for v in attainment_levels]
    if not levels:
        raise ValueError("attainment_levels is empty.")

    share_at_target = float((values >= float(sufficiency_target)).mean())
    mean_value = float(np.mean(values))
    median_value = float(np.median(values))
    std_value = float(np.std(values))
    min_value = float(np.min(values))
    max_value = float(np.max(values))

    summary_df = pd.DataFrame(
        {
            "metric": [
                "mean",
                "median",
                "std",
                "min",
                "max",
                f"share >= {sufficiency_target:g}",
            ],
            "value": [
                mean_value,
                median_value,
                std_value,
                min_value,
                max_value,
                share_at_target,
            ],
        }
    )

    attainment_share = [float((values >= level).mean()) for level in levels]

    fig, (ax_hist, ax_attain) = plt.subplots(1, 2, figsize=figsize)

    # Left: distribution
    ax_hist.hist(values, bins=bins, color=color, alpha=0.85, edgecolor="white")
    ax_hist.axvline(mean_value, linestyle="--", color="#111827", linewidth=1.5, label=f"Mean = {mean_value:.3f}")
    ax_hist.axvline(
        float(sufficiency_target),
        linestyle=":",
        color="#DC2626",
        linewidth=2.0,
        label=f"Target = {float(sufficiency_target):g}",
    )
    ax_hist.set_xlabel(score_col)
    ax_hist.set_ylabel("Number of Locations")
    ax_hist.set_title("Score Distribution")
    ax_hist.legend(frameon=False)
    ax_hist.grid(True, linestyle="--", alpha=0.4)

    # Right: attainment shares
    labels = [f">= {level:g}" for level in levels]
    ax_attain.barh(labels, [s * 100.0 for s in attainment_share], color="#10B981", alpha=0.9)
    ax_attain.set_xlim(0, 100)
    ax_attain.set_xlabel("Share of Locations (%)")
    ax_attain.set_title("Attainment Shares")
    ax_attain.grid(True, axis="x", linestyle="--", alpha=0.4)

    for i, share in enumerate(attainment_share):
        ax_attain.text(min(99, share * 100.0 + 1.0), i, f"{share * 100.0:.1f}%", va="center")

    fig_title = title if title is not None else "Sufficientarian Score"
    fig.suptitle(fig_title)
    fig.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=300, bbox_inches="tight")

    return fig, (ax_hist, ax_attain), summary_df
