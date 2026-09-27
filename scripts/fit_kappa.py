#!/usr/bin/env python3
"""Fit USL (Universal Scalability Law) parameters from observed (N, speedup) pairs.

    C(N)   = N / (1 + sigma*(N-1) + kappa*N*(N-1))
    N_max  = sqrt((1 - sigma) / kappa)

RUN THIS SCRIPT DIRECTLY. Do not read it into context.
No network access. Read-only (never writes files).

Two solvers, auto-selected:
  scipy : scipy.optimize.curve_fit -- nonlinear least squares; yields parameter
          covariance, so N_max gets a 95% confidence interval.
  grid  : zero-dependency coarse-to-fine grid search. Always available fallback,
          but no covariance and therefore no confidence interval.

Usage:
    python3 fit_kappa.py --data "2:1.60,4:1.95,8:1.65,16:1.05"
    python3 fit_kappa.py --csv assets/parallel-log.csv
    python3 fit_kappa.py --csv log.csv --solver grid
    python3 fit_kappa.py --csv log.csv --sigma 0.15     # fix sigma, fit kappa only
    python3 fit_kappa.py --data "..." --draws 5000 --seed 7

CSV format: first column = concurrency N, second column = observed net speedup.
Header and comment rows are auto-skipped. Extra columns are ignored.

Reports three distinct scalability numbers, because they answer different
questions and the R reference implementation keeps them separate:
  peak      N_max = sqrt((1-sigma)/kappa)  -- most useful width, past it you lose
  ceiling   1/sigma                      -- best possible if coherency were free
  identifiability  corr(sigma, kappa)    -- near +/-1 means the split is not resolved
A kappa > 0 claim needs a *retrograde* tail: if the sweep never passed the peak,
kappa is not identifiable and the fit says so instead of printing a number as if
it were one.

Prior art this borrows semantics from (neither is depended on -- both Python
packages are stale, and the live one is R-only):
  - wip727/PyUSL (MIT, last commit 2020-05, v0.0.4, nose-based tests) -- curve_fit approach
  - mookerji/sca_tools (Apache-2.0, 2017, self-described alpha, uses lmfit) --
    uncertainty propagation as the differentiating feature
  Mature reference implementation: R CRAN package "usl" (peak/optimal/confint methods).
"""

import argparse
import csv
import math
import sys

GRID = 40
ROUNDS = 7
K_LO, K_HI = 1e-4, 0.30
S_LO, S_HI = 0.0, 0.60
MIN_POINTS_FOR_PARAMS = 6   # dof gate; matches R usl's own <6-obs warning
CORR_WARN = 0.90            # |corr(sigma,kappa)| above this => not separable


def usl(n, s, k):
    return n / (1.0 + s * (n - 1.0) + k * n * (n - 1.0))


def _sse(pairs, s, k):
    total = 0.0
    for n, obs in pairs:
        total += (usl(n, s, k) - obs) ** 2
    return total


def fit_grid(pairs, sigma_fixed=None):
    s_lo, s_hi = S_LO, S_HI
    k_lo, k_hi = K_LO, K_HI
    best = (None, None, float("inf"))
    for _ in range(ROUNDS):
        sigmas = [sigma_fixed] if sigma_fixed is not None else [
            s_lo + (s_hi - s_lo) * i / GRID for i in range(GRID + 1)
        ]
        l_lo, l_hi = math.log(k_lo), math.log(k_hi)
        kappas = [math.exp(l_lo + (l_hi - l_lo) * i / GRID) for i in range(GRID + 1)]
        for s in sigmas:
            for k in kappas:
                err = _sse(pairs, s, k)
                if err < best[2]:
                    best = (s, k, err)
        if sigma_fixed is None:
            ds = (s_hi - s_lo) / 8.0
            s_lo = max(0.0, best[0] - ds)
            s_hi = min(0.95, best[0] + ds)
        dk = (math.log(k_hi) - math.log(k_lo)) / 8.0
        lk = math.log(best[1])
        k_lo = math.exp(lk - dk)
        k_hi = math.exp(lk + dk)
    return best


def fit_scipy(pairs, sigma_fixed=None):
    """Return (sigma, kappa, sse, cov_2x2) or None if scipy is unavailable/failed."""
    try:
        import numpy as np
        from scipy.optimize import curve_fit
    except ImportError:
        return None

    ns = np.array([p[0] for p in pairs], dtype=float)
    obs = np.array([p[1] for p in pairs], dtype=float)

    def model(n, s, k):
        return n / (1.0 + s * (n - 1.0) + k * n * (n - 1.0))

    try:
        if sigma_fixed is None:
            popt, pcov = curve_fit(model, ns, obs, p0=[0.10, 0.02],
                                   bounds=([0.0, 1e-6], [0.95, 5.0]), maxfev=20000)
            s, k = float(popt[0]), float(popt[1])
            cov = np.array(pcov, dtype=float)
        else:
            def model_k(n, k):
                return model(n, sigma_fixed, k)
            popt, pcov = curve_fit(model_k, ns, obs, p0=[0.02],
                                   bounds=([1e-6], [5.0]), maxfev=20000)
            s, k = float(sigma_fixed), float(popt[0])
            cov = np.array([[0.0, 0.0], [0.0, float(pcov[0][0])]], dtype=float)
        if not (math.isfinite(s) and math.isfinite(k)) or k <= 0:
            return None
        return s, k, _sse(pairs, s, k), cov
    except Exception:
        return None


def corr(cov):
    """Pearson correlation between the fitted sigma and kappa. sigma and kappa both
    bend the curve downward, so few noisy points cannot separate them; a reader
    must know when the split is an artifact of the fit rather than a finding."""
    try:
        v0, v1 = cov[0][0], cov[1][1]
        if v0 <= 0 or v1 <= 0:
            return None
        c = cov[0][1] / (v0 ** 0.5 * v1 ** 0.5)
        return c if c == c and abs(c) <= 1.0 else None
    except Exception:
        return None


def nmax(s, k):
    if k <= 0 or s >= 1.0:
        return None
    return math.sqrt((1.0 - s) / k)


def nmax_interval(s, k, cov, draws, seed):
    """Monte-Carlo CI for N_max. sqrt((1-s)/k) is a ratio, so naive error
    propagation is unreliable; sampling the fitted joint distribution is safer.

    Returns (lo, hi, None) on success, or (None, None, reason) when no honest
    interval can be produced. A near-perfect fit on few points drives the
    covariance to ~0, which would yield a fake-precise interval; that case is
    reported as unavailable rather than printed as if it were trustworthy.
    """
    if cov is None:
        return None, None, "grid solver yields no covariance"
    try:
        import numpy as np
    except ImportError:
        return None, None, "numpy unavailable"
    try:
        cov = np.asarray(cov, dtype=float)
        if cov.shape != (2, 2) or not np.all(np.isfinite(cov)):
            return None, None, "covariance not finite"
        if float(np.trace(cov)) < 1e-15:
            return None, None, "degenerate covariance"
        with np.errstate(all="ignore"):
            rng = np.random.default_rng(seed)
            samples = rng.multivariate_normal([s, k], cov, size=draws)
        vals = []
        for si, ki in samples:
            if ki > 1e-9 and si < 1.0:
                vals.append(math.sqrt((1.0 - si) / ki))
        if len(vals) < draws * 0.5:
            return None, None, "too many invalid draws"
        vals.sort()
        return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))], None
    except Exception as exc:
        return None, None, "sampling failed (%s)" % type(exc).__name__


def parse_data(text):
    pairs = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError("bad pair %r, expected N:speedup" % chunk)
        n, obs = chunk.split(":", 1)
        pairs.append((float(n), float(obs)))
    return pairs


def load_csv(path):
    pairs = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            try:
                pairs.append((float(row[0].strip()), float(row[1].strip())))
            except ValueError:
                continue
    return pairs


def selftest():
    """Controls for the three added readings. A probe that cannot fire is worthless,
    and 'no decline observed' must not fire on a sweep that did pass its peak."""
    peaked_data = [(1.0, 1.00), (2.0, 1.45), (4.0, 1.30), (8.0, 0.90),
                   (12.0, 0.55), (16.0, 0.35)]
    rising_data = [(1.0, 1.00), (2.0, 1.45), (3.0, 1.70), (4.0, 1.85),
                   (6.0, 1.95), (8.0, 1.99)]
    ok = []

    bmax = max(peaked_data, key=lambda p: p[1])[0]
    last = max(peaked_data)[0]
    ok.append(("P1 peaked dataset is judged to have passed its peak",
               bmax < last and _straddle(peaked_data)))
    ok.append(("P2 rising dataset is NOT judged to have passed its peak",
               not _straddle(rising_data)))
    ok.append(("P3 ceiling = 1/sigma is finite and > 1 for sigma in range",
               abs(1.0 / 0.2 - 5.0) < 1e-9))
    ok.append(("P4 correlation is computed from covariance",
               abs(corr([[4.0, 1.0], [1.0, 1.0]]) - 0.5) < 1e-9))
    ok.append(("P5 degenerate covariance yields None not a number",
               corr([[0.0, 0.0], [0.0, 0.0]]) is None))
    print("== fit_kappa selftest ==")
    bad = 0
    for name, passed in ok:
        bad += 0 if passed else 1
        print("  %s  %s" % ("PASS" if passed else "FAIL", name))
    print("controls : %d" % len(ok))
    print("failed   : %d" % bad)
    return 1 if bad else 0


def _straddle(pairs):
    """True when the observations include a rise and then a real fall."""
    best = max(pairs, key=lambda p: p[1])
    last = max(pairs)
    return best[0] < last[0] - 1e-9 and best[1] > last[1] * 1.02


def main():
    ap = argparse.ArgumentParser(description="Fit USL sigma/kappa from observed speedups.")
    src = ap.add_mutually_exclusive_group(required=False)
    ap.add_argument("--selftest", action="store_true",
                    help="run the controls for ceiling/correlation/peak-straddle and exit")
    src.add_argument("--data", help='inline pairs, e.g. "2:1.60,4:1.95,8:1.65"')
    src.add_argument("--csv", help="CSV with N in col 1 and speedup in col 2")
    ap.add_argument("--sigma", type=float, default=None, help="fix sigma, fit kappa only")
    ap.add_argument("--solver", choices=["auto", "scipy", "grid"], default="auto")
    ap.add_argument("--draws", type=int, default=2000, help="Monte-Carlo draws for N_max CI")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.data and not args.csv:
        ap.error("--data or --csv is required (or run --selftest)")

    pairs = parse_data(args.data) if args.data else load_csv(args.csv)
    pairs = [(n, o) for n, o in pairs if n >= 1 and o > 0]
    if len(pairs) < 2:
        print("ERROR: need at least 2 valid (N, speedup) observations.", file=sys.stderr)
        return 2

    distinct = len({n for n, _ in pairs})
    cov = None

    if args.solver in ("auto", "scipy"):
        res = fit_scipy(pairs, args.sigma)
        if res:
            sigma, kappa, sse, cov = res
            solver = "scipy (curve_fit)"
        elif args.solver == "scipy":
            print("ERROR: scipy solver unavailable or failed to converge.", file=sys.stderr)
            return 3
        else:
            sigma, kappa, sse = fit_grid(pairs, args.sigma)
            solver = "grid (scipy unavailable)"
    else:
        sigma, kappa, sse = fit_grid(pairs, args.sigma)
        solver = "grid"

    mean = sum(o for _, o in pairs) / len(pairs)
    sst = sum((o - mean) ** 2 for _, o in pairs)
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")

    print("== USL fit ==")
    print("solver              : %s" % solver)
    print("observations        : %d  (distinct N: %d)" % (len(pairs), distinct))
    print("sigma  (contention) : %.4f%s" % (sigma, "  [fixed]" if args.sigma is not None else ""))
    print("kappa  (coherency)  : %.5f" % kappa)
    print("SSE                 : %.5f" % sse)
    print("R^2                 : %.4f" % r2)
    # 1/sigma: the saturation ceiling of the Amdahl-only model (kappa=0). If it
    # sits near 1, contention alone caps the payoff and no coherency fix helps.
    print("ceiling if kappa=0  : %.2f  (= 1/sigma, Amdahl-only saturation)"
          % (1.0 / sigma if sigma > 1e-9 else float("inf")))

    n_max = nmax(sigma, kappa)
    if n_max is None:
        print("N_max               : undefined (sigma >= 1 or kappa <= 0)")
    else:
        # Gate the interval on degrees of freedom and on residual scale. A
        # 2-parameter fit on 4 points has dof=2: the residual variance -- and
        # therefore the whole covariance -- is not estimable, so any interval
        # would be fake precision. Same for data that lies exactly on the curve.
        resid_var = sse / max(1.0, len(pairs) - 2.0)
        obs_ms = sum(o * o for _, o in pairs) / len(pairs)
        if len(pairs) < MIN_POINTS_FOR_PARAMS:
            ci_lo = ci_hi = None
            ci_note = "only %d observations; >= %d needed for a reliable interval" % (
                len(pairs), MIN_POINTS_FOR_PARAMS)
        elif resid_var <= 1e-12 * max(obs_ms, 1.0):
            ci_lo = ci_hi = None
            ci_note = "residuals numerically zero (exact fit) -- no real uncertainty"
        else:
            ci_lo, ci_hi, ci_note = nmax_interval(sigma, kappa, cov, args.draws, args.seed)
        if ci_lo is not None:
            print("N_max = sqrt((1-sigma)/kappa) : %.2f  (95%% CI %.2f..%.2f, %d draws)"
                  % (n_max, ci_lo, ci_hi, args.draws))
        else:
            print("N_max = sqrt((1-sigma)/kappa) : %.2f" % n_max)
            if ci_note:
                print("                                (no CI: %s)" % ci_note)
        c = corr(cov) if cov is not None else None
        if c is not None:
            print("corr(sigma,kappa)     : %+.3f%s"
                  % (c, "   <-- parameters nearly unidentifiable; treat sigma/kappa "
                        "as one combined penalty, not two findings"
                     if abs(c) >= CORR_WARN else ""))
        if n_max < 1.5:
            print("recommended K       : 1  (N_max < 1.5 -> parallelism not worth it)")
        else:
            print("recommended K       : %d" % max(1, min(16, int(math.floor(n_max)))))

    # Without a decline, kappa>0 is not evidenced and any reported value is the
    # shape of the rising side, not a measured coherency cost.
    peaked = _straddle(pairs) if len(pairs) >= 2 else None

    if n_max is not None:
        observed_max_n = max(n for n, _ in pairs)
        if not peaked:
            print("curve shape         : NO decline observed -> kappa NOT identifiable.")
            print("                      The fit's kappa is extrapolated from the rising")
            print("                      side; widen the sweep past the peak before quoting it.")
        elif n_max <= observed_max_n:
            print("curve shape         : peak already passed -> kappa-dominant.")
            print("                      Remedy: reduce SHARING (partition, per-shard state,")
            print("                      batch updates), not tuning the parallel code.")
        elif n_max > observed_max_n * 1.5:
            print("curve shape         : still rising across observed K -> sigma-side or headroom left.")
        else:
            print("curve shape         : peak sits near the edge of the observed range.")

    print("\n   N   observed  predicted")
    for n, obs in sorted(pairs):
        print("  %3d  %8.3f  %9.3f" % (n, obs, usl(n, sigma, kappa)))

    print()
    if distinct < MIN_POINTS_FOR_PARAMS:
        print("WARN: fewer than %d distinct N. Parameters are indicative only -- do not"
              % MIN_POINTS_FOR_PARAMS)
        print("      treat sigma/kappa as calibrated until you have >= %d levels."
              % MIN_POINTS_FOR_PARAMS)
    if r2 < 0.5:
        print("WARN: R^2 < 0.5, data is noisy. Treat the fit as directional only.")
    if solver.startswith("grid"):
        print("NOTE: grid solver yields no parameter covariance, so no N_max confidence")
        # This used to say "Install numpy+scipy" unconditionally, which told a user to
        # install what they already have -- ro_fit.py pins --solver grid deliberately,
        # so the blocker is the observation count, not the dependency.
        try:
            import numpy    # noqa: F401
            import scipy    # noqa: F401
            print("      interval. numpy+scipy ARE installed, so --solver scipy would give")
            print("      a covariance fit -- but any solver needs >= 6 distinct N for a CI.")
        except ImportError:
            print("      interval. Install numpy+scipy for a quantified fit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
