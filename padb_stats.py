"""
padb_stats.py — Non-parametric statistics for production data analysis

Primary use case: deriving datasheet specifications from n=15 instruments
where data is not guaranteed to be Gaussian.
"""
from __future__ import annotations
import math
import warnings
import numpy as np
from scipy import stats


INT_SENTINEL = 2_147_483_647


def _clean(data) -> np.ndarray:
    data = np.asarray(data, dtype=float)
    data = data[~np.isnan(data)]
    data = data[np.abs(data) < INT_SENTINEL]
    return data


# ---------------------------------------------------------------------------
# Sample-size adequacy
# ---------------------------------------------------------------------------

def n_required_nonparam(proportion: float, confidence: float) -> int:
    """Minimum n for a valid non-parametric tolerance interval."""
    if proportion >= 1 or confidence >= 1:
        return 9_999
    try:
        return math.ceil(math.log(1 - confidence) / math.log(proportion))
    except (ValueError, ZeroDivisionError):
        return 9_999


def sample_size_adequacy(n: int, proportion: float, confidence: float) -> tuple[bool, int, str]:
    """Returns (adequate, n_required, message)."""
    n_req = n_required_nonparam(proportion, confidence)
    adequate = n >= n_req
    if adequate:
        msg = f"n={n} adequate for P{100*proportion:.0f}/C{100*confidence:.0f} tolerance interval."
    else:
        msg = (f"n={n} < n_required={n_req} for P{100*proportion:.0f}/C{100*confidence:.0f} TI. "
               f"Results are indicative only.")
    return adequate, n_req, msg


# ---------------------------------------------------------------------------
# Non-parametric tolerance intervals (order-statistics)
# ---------------------------------------------------------------------------

def nonparam_tolerance_interval(
    data,
    proportion: float = 0.90,
    confidence: float = 0.90,
) -> tuple[float, float, bool]:
    """
    Two-sided non-parametric tolerance interval via order statistics.

    Returns (lower_bound, upper_bound, sample_size_warning).
    The interval covers at least `proportion` of the population with
    `confidence` confidence.  No normality assumption.
    """
    data = _clean(data)
    n = len(data)
    if n < 2:
        return np.nan, np.nan, True

    sorted_data = np.sort(data)
    n_req = n_required_nonparam(proportion, confidence)

    best_i, best_j = 0, n - 1
    best_width = np.inf
    found = False

    for i in range(n):
        for j in range(i + 1, n):
            # P(at least `proportion` of population falls between X_(i+1) and X_(j))
            coverage = 1 - stats.beta.cdf(proportion, j - i, n - j + i + 1)
            if coverage >= confidence:
                width = sorted_data[j] - sorted_data[i]
                if width < best_width:
                    best_width = width
                    best_i, best_j = i, j
                    found = True

    if not found:
        return sorted_data[0], sorted_data[-1], True

    return sorted_data[best_i], sorted_data[best_j], n < n_req


def onesided_tolerance_bound(
    data,
    proportion: float = 0.90,
    confidence: float = 0.90,
    side: str = "upper",
) -> tuple[float, bool]:
    """
    One-sided non-parametric tolerance bound.

    Returns (bound, sample_size_warning).
    `side` = 'upper' or 'lower'.
    """
    data = _clean(data)
    n = len(data)
    if n < 2:
        return np.nan, True

    sorted_data = np.sort(data)
    n_req = n_required_nonparam(proportion, confidence)

    if side == "upper":
        for k in range(n - 1, -1, -1):
            p_cov = 1 - stats.binom.cdf(k - 1, n, proportion) if k > 0 else 1.0
            if p_cov >= confidence:
                return sorted_data[k], n < n_req
        return sorted_data[-1], True
    else:
        for k in range(n):
            p_cov = stats.binom.cdf(k, n, 1 - proportion)
            if p_cov >= confidence:
                return sorted_data[k], n < n_req
        return sorted_data[0], True


# ---------------------------------------------------------------------------
# KDE
# ---------------------------------------------------------------------------

def kde(
    data,
    x_points: np.ndarray | None = None,
    bandwidth: str | float = "scott",
) -> tuple[np.ndarray, np.ndarray]:
    """Gaussian KDE. Returns (x, density)."""
    data = _clean(data)
    if len(data) < 3:
        return np.array([]), np.array([])

    kernel = stats.gaussian_kde(data, bw_method=bandwidth)

    if x_points is None:
        margin = (data.max() - data.min()) * 0.25
        x_points = np.linspace(data.min() - margin, data.max() + margin, 400)

    return x_points, kernel(x_points)


# ---------------------------------------------------------------------------
# Distribution fitting
# ---------------------------------------------------------------------------

_DIST_REGISTRY: dict[str, stats.rv_continuous] = {
    "normal":    stats.norm,
    "lognormal": stats.lognorm,
    "weibull":   stats.weibull_min,
    "gamma":     stats.gamma,
}


def fit_distributions(
    data,
    distributions: list[str] | None = None,
) -> list[dict]:
    """
    Fit distributions to data.  Returns list of result dicts sorted by AIC.

    Each dict: {name, params, aic, bic, dist}
    """
    data = _clean(data)
    n = len(data)
    if n < 5:
        return []

    if distributions is None:
        distributions = list(_DIST_REGISTRY.keys())

    results = []
    for name in distributions:
        dist = _DIST_REGISTRY.get(name)
        if dist is None:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                params = dist.fit(data)
                log_lik = float(np.sum(dist.logpdf(data, *params)))
                k = len(params)
                aic = 2 * k - 2 * log_lik
                bic = k * math.log(n) - 2 * log_lik
                results.append({"name": name, "params": params,
                                 "aic": aic, "bic": bic, "dist": dist})
        except Exception:
            pass

    results.sort(key=lambda r: r["aic"])
    return results


def best_fit_pdf(
    data,
    x_points: np.ndarray,
    distributions: list[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    Fit distributions and return best-fit PDF at x_points.
    Returns (x_points, pdf_values, fit_info_dict).
    """
    fits = fit_distributions(data, distributions)
    if not fits:
        return x_points, np.zeros_like(x_points, dtype=float), {}

    best = fits[0]
    pdf_vals = best["dist"].pdf(x_points, *best["params"])
    return x_points, pdf_vals, best


# ---------------------------------------------------------------------------
# Bootstrap confidence intervals
# ---------------------------------------------------------------------------

def bootstrap_ci(
    data,
    statistic=np.median,
    n_boot: int = 2000,
    confidence: float = 0.95,
    rng_seed: int = 42,
) -> tuple[float, float, float]:
    """
    Bootstrap confidence interval for a statistic.
    Returns (lower, upper, point_estimate).
    """
    data = _clean(data)
    n = len(data)
    if n < 3:
        est = float(statistic(data)) if n > 0 else np.nan
        return np.nan, np.nan, est

    rng = np.random.default_rng(rng_seed)
    boot_stats = np.array([
        statistic(rng.choice(data, size=n, replace=True))
        for _ in range(n_boot)
    ])

    alpha = 1 - confidence
    lo = float(np.percentile(boot_stats, 100 * alpha / 2))
    hi = float(np.percentile(boot_stats, 100 * (1 - alpha / 2)))
    return lo, hi, float(statistic(data))


# ---------------------------------------------------------------------------
# Band summary (for spec derivation)
# ---------------------------------------------------------------------------

def band_summary(
    data,
    proportion: float = 0.90,
    confidence: float = 0.90,
) -> dict:
    """
    Full summary for a frequency band: descriptive stats + TI + bootstrap CI.
    Returns a dict of scalar results.
    """
    data = _clean(data)
    n = len(data)

    result: dict = {
        "n": n,
        "mean": float(np.mean(data)) if n else np.nan,
        "median": float(np.median(data)) if n else np.nan,
        "std": float(np.std(data, ddof=1)) if n > 1 else np.nan,
        "min": float(data.min()) if n else np.nan,
        "max": float(data.max()) if n else np.nan,
        "p05": float(np.percentile(data, 5)) if n else np.nan,
        "p95": float(np.percentile(data, 95)) if n else np.nan,
    }

    ti_lo, ti_hi, warn = nonparam_tolerance_interval(data, proportion, confidence)
    result["ti_lower"] = ti_lo
    result["ti_upper"] = ti_hi
    result["ti_warning"] = warn
    result["ti_proportion"] = proportion
    result["ti_confidence"] = confidence

    ci_lo, ci_hi, med = bootstrap_ci(data, statistic=np.median, confidence=confidence)
    result["ci_lower"] = ci_lo
    result["ci_upper"] = ci_hi
    result["ci_median"] = med

    _, n_req, msg = sample_size_adequacy(n, proportion, confidence)
    result["n_required"] = n_req
    result["adequacy_msg"] = msg

    return result
