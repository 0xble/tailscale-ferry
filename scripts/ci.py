"""Repository-owned Go gate, with optional browser checks and private state."""
from contextlib import contextmanager, nullcontext, suppress
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
GO_VERSION = "go1.27.1"
_cancelled_signal = None


class Cancelled(Exception):
    def __init__(self, signum):
        super().__init__(f"Cancelled by {signal.Signals(signum).name}")
        self.signum = signum


@contextmanager
def cancellation_signals():
    global _cancelled_signal
    previous_signal = _cancelled_signal
    _cancelled_signal = None

    def cancel(signum, _frame):
        global _cancelled_signal
        # Defer unwinding until a newly spawned child has an owner.
        if _cancelled_signal is None:
            _cancelled_signal = signum

    previous = {s: signal.signal(s, cancel) for s in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _cancelled_signal = previous_signal


def check_cancellation():
    if _cancelled_signal is not None:
        raise Cancelled(_cancelled_signal)


def group_alive(group):
    if sys.platform == "linux":
        # No procps package is required in the contributor image.
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
            except (OSError, IndexError):
                continue
            if fields[2] == str(group) and fields[0] not in ("Z", "X"):
                return True
        return False
    rows = subprocess.check_output(["/bin/ps", "-axo", "pgid=,stat="], text=True, timeout=1).splitlines()
    return any(fields[0] == str(group) and not fields[1].startswith(("Z", "X"))
               for row in rows if len(fields := row.split()) == 2)


def stop_group(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        process.poll()
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            # macOS can retain an unsignalable group while an orphan is reaped.
            if group_alive(process.pid):
                raise
            return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            process.poll()
            if not group_alive(process.pid):
                return
            time.sleep(0.05)
    raise RuntimeError(f"Command group {process.pid} remains live after cancellation")


def run(command, env):
    print("+ " + " ".join(map(str, command)), flush=True)
    execute(command, env)


def execute(command, env, *, capture=False, check_cancel=True, timeout=1800):
    # Files avoid inherited stdout pipes holding capture open after leader exit.
    output_context = tempfile.TemporaryFile(dir=env.get("TMPDIR")) if capture else nullcontext()
    with output_context as output:
        if check_cancel:
            check_cancellation()
        process = subprocess.Popen(list(map(str, command)), cwd=ROOT, env=env,
                                   stdout=output, start_new_session=True)
        result = None
        try:
            deadline = time.monotonic() + timeout
            while result is None:
                if check_cancel:
                    check_cancellation()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                with suppress(subprocess.TimeoutExpired):
                    result = process.wait(timeout=min(.1, remaining))
        finally:
            original = sys.exc_info()[1]
            try:
                stop_group(process)
                process.wait(timeout=5)
            except Exception as error:
                if original is None and not result:
                    raise
                print(f"Additional process cleanup failure: {error}", file=sys.stderr)
        if check_cancel:
            check_cancellation()
        captured = b""
        if capture:
            output.seek(0)
            captured = output.read()
        if result:
            raise subprocess.CalledProcessError(result, command, output=captured)
        return captured


def environment(home):
    state = ROOT / ".ci"
    return dict(PATH=os.environ.get("PATH", os.defpath), HOME=str(home), TMPDIR=str(home),
                LANG="C.UTF-8", LC_ALL="C.UTF-8", GOENV="off", GOTOOLCHAIN="local",
                GOPATH=str(state / "gopath"), GOMODCACHE=str(state / "modules"),
                GOCACHE=str(state / "build"), GOFLAGS="-mod=readonly", CGO_ENABLED="1",
                GOPROXY="https://proxy.golang.org", GOSUMDB="sum.golang.org",
                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull,
                PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_CACHE_DIR=str(state / "pip"),
                PLAYWRIGHT_BROWSERS_PATH=str(state / "browsers"))


def snapshot(*, check_cancel=True):
    def git(*args):
        return execute(["git", "-C", str(ROOT), *args], environment(ROOT / ".ci"),
                       capture=True, check_cancel=check_cancel, timeout=30)
    digest = hashlib.sha256(git("rev-parse", "HEAD"))
    # Hash source itself, not just Git status, including edits already present.
    names = set(git("ls-files", "-z").split(b"\0")) | set(git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
    for name in sorted(names - {b""}):
        path = ROOT / os.fsdecode(name)
        digest.update(name + b"\0")
        if not path.exists() and not path.is_symlink():
            digest.update(b"missing")
        else:
            digest.update(str(path.lstat().st_mode).encode())
            digest.update(os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes())
    digest.update(git("diff", "--cached", "--binary"))
    return digest.hexdigest()


@contextmanager
def checkout():
    state = ROOT / ".ci"
    for name in ("", "gopath", "modules", "build", "pip", "browsers", "browser-env", "run.lock"):
        if (state / name).is_symlink():
            raise RuntimeError("CI state must belong to this checkout, without symlinks")
    state.mkdir(exist_ok=True)
    with (state / "run.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another CI invocation owns this checkout") from None
        before = snapshot()
        try:
            with tempfile.TemporaryDirectory(prefix="run-", dir=state) as home:
                yield environment(Path(home))
        finally:
            original = sys.exc_info()[1]
            try:
                # Finish the source guard while holding the lock, including
                # after cancellation. Its commands still own and drain groups.
                if snapshot(check_cancel=_cancelled_signal is None) != before:
                    raise RuntimeError("CI changed source or index; changes preserved for inspection")
            except Exception as error:
                if original is None:
                    check_cancellation()
                    raise
                print(f"Additional source inspection failure: {error}", file=sys.stderr)
            check_cancellation()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", default="all", choices=("all", "setup", "check", "browser-setup", "browser", "list"))
    args = parser.parse_args()
    if args.phase == "list":
        print("default: setup, vet, test, build, race, harness; optional: browser-setup, browser")
        return
    with cancellation_signals(), checkout() as env:
        version = execute(["go", "version"], env, capture=True, timeout=30).decode().split()[2]
        if version != GO_VERSION:
            raise RuntimeError(f"Go {GO_VERSION[2:]} required on PATH, found {version}")
        if args.phase in ("all", "setup"):
            run(["go", "mod", "download"], env)
        if args.phase in ("all", "check"):
            for command in (["go", "vet", "./..."], ["go", "test", "./..."], ["go", "build", "./..."], ["go", "test", "-race", "./..."], [sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests"], ["git", "diff", "--check"]):
                run(command, env)
        python = ROOT / ".ci/browser-env/bin/python"
        if args.phase == "browser-setup":
            run([sys.executable, "-m", "venv", ROOT / ".ci/browser-env"], env)
            run([python, "-m", "pip", "install", "-r", ROOT / "scripts/requirements-browser.txt"], env)
            run([python, "-m", "playwright", "install", "chromium", "webkit"], env)
        if args.phase == "browser":
            run([python, "scripts/test-responsive.py"], env)
        check_cancellation()


if __name__ == "__main__":
    try:
        main()
    except Cancelled as error:
        sys.exit(128 + error.signum)
    except KeyboardInterrupt:
        sys.exit(130)
    except subprocess.CalledProcessError as error:
        print(str(error), file=sys.stderr)
        sys.exit(error.returncode if error.returncode > 0 else 128 - error.returncode)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
