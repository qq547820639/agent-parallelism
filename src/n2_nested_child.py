#!/usr/bin/env python3
"""The process a nested declarative tool_call actually runs.

Prints exactly one JSON line to stdout:

    {"n2_child": "1", "pid": <self>, "ppid": <parent>, "secret": <fresh>,
     "nonce": <N2_NONCE>, "uname": <sysname>, "argv": [...]}

`secret` is generated HERE, in the child, so any consumer that reports it can only
have got it by running this process and reading its piped stdout.  That is what makes
"the nested result came from a subprocess" a checkable claim instead of a sentence:
`checks/n2_check.py` requires the secret in the artifacts file and its ABSENCE from
the top-level transcript stdout.

Used by src/n2_live.py and by tests/test_n2_live.py.  Not useful on its own, but it
runs standalone:

    python3 src/n2_nested_child.py --nonce demo
"""

import argparse
import json
import os
import platform
import secrets
import sys


def payload(argv=None):
    """Build the child's report.  Split out so tests can call it in-process."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--nonce", default=None)
    parser.add_argument("--fail", type=int, default=None,
                        help="exit with this code after printing (fault control)")
    opts, extra = parser.parse_known_args(list(argv))
    out = {
        "n2_child": "1",
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "secret": "sec-" + secrets.token_hex(8),
        "nonce": os.environ.get("N2_NONCE", opts.nonce),
        "child_env": os.environ.get("N2_CHILD"),
        "uname": platform.system(),
        "argv": extra,
    }
    return out, opts


def main(argv=None):
    out, opts = payload(argv)
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    sys.stdout.flush()
    if opts.fail is not None:
        return opts.fail
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
