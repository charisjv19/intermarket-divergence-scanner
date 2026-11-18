"""Live v8.10 — dual-confirm protocol on tagged OOS v8.8.

NQ-confirm SMT is scored even when ES-confirm exists. Tagged OOS v8.8
is not modified.
"""

from live.runner import main

if __name__ == "__main__":
    raise SystemExit(main(version="v8.10"))
