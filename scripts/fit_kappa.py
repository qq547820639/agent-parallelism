#!/usr/bin/env python3
"""Fit USL (Universal Scalability Law) parameters from observed (N, speedup) pairs.

    C(N)   = N / (1 + sigma*(N-1) + kappa*N*(N-1))
    N_max  = sqrt((1 - sigma) / kappa)

RUN THIS SCRIPT DIRECTLY. Do not read it into context.
Stdlib only. No network access. Read-only (never writes files).

Usage:
    python3 fit_kappa.py --data "2:1.60,4:1.95,8:1.65,16:1.05"
    python3 fit_kappa.py --csv assets/parallel-log.csv
    python3 fit_kappa.py --csv log.csv --sigma 0.15     # fix sigma, fit kappa only

CSV format: first column = concurrency N, second column = observed net speedup.
A header row is auto-detected and skipped. Extra columns are ignored.
"""

import argparse
import csv
import math
import sys

GRID = 40
ROUNDS = 7
K_LO, K_HI = 1e-4, 0.30
S_LO, S_HI = 0.0, 0.60


def _sse(pairs, s, k):
    total = 0.0
    for n, obs in pairs:
        pred = n / (1.0 + s * (n - 1.0) + k * n * (n - 1.0))
        total += (pred - obs) ** 2
    return total


def fit(pairs, sigma_fixed=None):
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
                continue  # header or comment row
    return pairs


def main():
    ap = argparse.ArgumentParser(description="Fit USL sigma/kappa from observed speedups.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help='inline pairs, e.g. "2:1.60,4:1.95,8:1.65"')
    src.add_argument("--csv", help="path to a CSV with N in col 1 and speedup in col 2")
    ap.add_argument("--sigma", type=float, default=None, help="fix sigma and fit kappa only")
    args = ap.parse_args()

    pairs = parse_data(args.data) if args.data else load_csv(args.csv)
    pairs = [(n, o) for n, o in pairs if n >= 1 and o > 0]
    if len(pairs) < 2:
        print("ERROR: need at least 2 valid (N, speedup) observations.", file=sys.stderr)
        return 2

    distinct = len({n for n, _ in pairs})
    sigma, kappa, sse = fit(pairs, args.sigma)

    mean = sum(o for _, o in pairs) / len(pairs)
    sst = sum((o - mean) ** 2 for _, o in pairs)
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")

    print("== USL fit ==")
    print("observations        : %d  (distinct N: %d)" % (len(pairs), distinct))
    print("sigma  (serial)     : %.4f%s" % (sigma, "  [fixed]" if args.sigma is not None else ""))
    print("kappa  (crosstalk)  : %.5f" % kappa)
    print("SSE                 : %.5f" % sse)
    print("R^2                 : %.4f" % r2)

    if sigma < 1.0 and kappa > 0:
        n_max = math.sqrt((1.0 - sigma) / kappa)
        print("N_max = sqrt((1-sigma)/kappa) : %.2f" % n_max)
        rec = max(1, min(16, int(math.floor(n_max))))
        if n_max < 1.5:
            print("recommended K       : 1  (N_max < 1.5 -> parallelism is not worth it)")
        else:
            print("recommended K       : %d" % rec)
    else:
        print("N_max               : undefined (sigma >= 1 or kappa <= 0)")

    print("\n   N   observed  predicted")
    for n, obs in sorted(pairs):
        pred = n / (1.0 + sigma * (n - 1.0) + kappa * n * (n - 1.0))
        print("  %3d  %8.3f  %9.3f" % (n, obs, pred))

    print()
    if distinct < 3:
        print("WARN: fewer than 3 distinct N. Fit is weakly identified; get more K levels.")
    if r2 < 0.5:
        print("WARN: R^2 < 0.5, data is noisy. Treat the fit as directional only.")
    if kappa <= K_LO * 1.01 or kappa >= K_HI * 0.99:
        print("WARN: kappa hit the search boundary. Widen the range or check the data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
