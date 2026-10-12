"""Forward `hive-attention <args…>` to `hive attention <args…>` without a shell.

The launcher replaces its own process with Hive (``os.execv``), so standard input, output
and error, signals and the exit status are Hive's own — there is no child to relay and no
wrapper to lose a signal in. It owns no database, HTTP client, command schema, ordering or
retry semantics.

Resolution: ``HIVE_BIN`` when set (an explicit path or a name on PATH), otherwise the
installed ``hive`` on PATH. A missing executable, or one that resolves back to this
launcher, is refused with a diagnostic instead of looping.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: Set on the exec'd environment; seeing it on entry means Hive resolved back to us.
RECURSION_GUARD = "HIVE_ATTENTION_LAUNCHER_ACTIVE"
#: The conventional "command not found" status, so callers can tell it from Hive's own codes.
EXIT_NOT_FOUND = 127


class LauncherError(Exception):
    """A refusal with the diagnostic the caller sees on stderr."""


def resolve_hive(env: dict[str, str], self_path: str) -> str:
    """The absolute path of the Hive executable this launcher should become."""
    configured = env.get("HIVE_BIN")
    candidate = configured or "hive"
    resolved = shutil.which(candidate, path=env.get("PATH"))
    if resolved is None:
        where = f"HIVE_BIN={configured!r}" if configured else "`hive` on PATH"
        raise LauncherError(
            f"hive-attention: cannot find {where}. Install Hive (the `hive` CLI) or set "
            "HIVE_BIN to its path; this launcher only forwards to `hive attention`."
        )
    if _same_file(resolved, self_path):
        raise LauncherError(
            f"hive-attention: {resolved} is this launcher, not Hive; "
            "point HIVE_BIN at the `hive` CLI."
        )
    return resolved


def _same_file(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    env = dict(os.environ)
    if env.get(RECURSION_GUARD) == "1":
        print(
            "hive-attention: refusing to recurse — the resolved Hive executable invoked "
            "hive-attention again; check HIVE_BIN.",
            file=sys.stderr,
        )
        return EXIT_NOT_FOUND
    try:
        hive = resolve_hive(env, sys.argv[0])
    except LauncherError as error:
        print(str(error), file=sys.stderr)
        return EXIT_NOT_FOUND
    env[RECURSION_GUARD] = "1"
    # Flush anything Python buffered before the process image is replaced.
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(hive, [hive, "attention", *args], env)
    return 0  # unreachable: execve either replaces this process or raises


if __name__ == "__main__":
    raise SystemExit(main())
