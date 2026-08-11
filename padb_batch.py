"""
padb_batch.py — Python wrapper for PADB V4 Batch Interface (PADB-R.exe)

Default exe path is set to Keysight PADB-R.NET install per user:
    C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe

Highlights:
- Builds correct switch syntax and quoting (no spaces around '=' for -subex/-suban)
- Writes readable switch files (-f) for complex blocks
- Executes PADB-R.exe via subprocess with timeout and stdout/stderr capture
- Full coverage for switches: -dir, -lpod, -spod, -ext, -an, -suban, -fsuban,
  -subex, -fsubex, -imp, -exp dbdif/csv, -merge, -dump, -fan, -u, -f, -notify,
  -log, -trace, -datevar
- NEW: .lpods([...]) convenience method to add multiple POD loads

Author: 2026
"""
from __future__ import annotations
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

StrPath = Union[str, os.PathLike]


def _running_pids(exe_name: str) -> list[str]:
    """PIDs of any currently-running process named exe_name (e.g. "PADB-R.exe"),
    system-wide -- not just children of this process tree. Uses `tasklist`
    (always present on Windows) rather than adding psutil as a new dependency
    for one lookup.

    Checks the real OS process table, not a lock file, so a stale lock left
    behind by a crashed/killed process can never cause a false "still busy"
    deadlock -- the moment the real process is gone, this reports it gone."""
    try:
        cp = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []  # tasklist itself failing shouldn't block a run -- fail open
    pids = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("INFO:"):
            continue
        fields = [f.strip('"') for f in line.split('","')]
        if len(fields) >= 2 and fields[0].lower() == exe_name.lower():
            pids.append(fields[1])
    return pids


def wait_for_exclusive_padb_r(exe_path: StrPath, max_wait: float = 600.0, poll_interval: float = 5.0) -> None:
    """Block until no PADB-R.exe (matching exe_path's own filename) is running
    anywhere on this machine, or raise if none clears within max_wait.

    Found 2026-08-10: the webapp's single-worker job queue only serializes
    PADB-R.exe launches within one Flask process's lifetime. If that process
    is killed and restarted while a job is mid-run (happened for real -- a
    background-task lifecycle issue unrelated to the job itself), the
    already-launched PADB-R.exe becomes orphaned but keeps running,
    invisible to the new process's fresh, empty queue. The new process then
    launches its own PADB-R.exe with no idea the first one still exists --
    two concurrent instances interfere with each other and both stall with
    zero CPU progress (confirmed via Get-Process CPU sampling on a real
    stuck pair). This check is the actual fix: enforced at the one real
    choke point (PADBBatch.run(), called by every invocation path -- webapp
    and direct CLI both), by checking the live OS process table rather than
    trusting any one process's own in-memory state, so it holds even across
    a full process restart."""
    exe_name = Path(exe_path).name
    deadline = time.monotonic() + max_wait
    first_check = True
    while True:
        pids = _running_pids(exe_name)
        if not pids:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Refusing to launch {exe_name}: already running (PID {', '.join(pids)}) "
                f"and still hasn't exited after waiting {max_wait:.0f}s. Running two "
                f"instances concurrently causes both to interfere with each other and "
                f"stall (confirmed 2026-08-10) -- not launching a second one. If the "
                f"existing instance is truly stuck (not just slow), close it manually "
                f"(or `taskkill /IM {exe_name} /F`) and retry."
            )
        if first_check:
            print(f"  Waiting for existing {exe_name} (PID {', '.join(pids)}) to exit "
                  f"before launching -- running two at once would make both stall...")
            first_check = False
        time.sleep(poll_interval)


def _to_str_path(p: StrPath) -> str:
    return str(Path(p))


def _needs_quotes(val: str) -> bool:
    # Quote if contains whitespace or special separators used by PADB parsing.
    specials = set(' \t#=,"\'|')
    return any(c in specials for c in val)


def _escape_quotes(val: str) -> str:
    # Escape embedded double quotes with backslash for safety.
    return val.replace('"', r'\"')


def _quote(val: str) -> str:
    """Quote a value for PADB if needed, escaping embedded quotes."""
    if val is None:
        return '""'
    sval = str(val)
    sval = _escape_quotes(sval)
    if _needs_quotes(sval):
        return f'"{sval}"'
    return sval


def _kv_line(k: str, v: Union[str, int, float]) -> str:
    """Format key=value without spaces around '=' (PADB requirement)."""
    return f"{k}={_quote(str(v))}"


def _join_csv_patterns(patterns: Sequence[str]) -> str:
    """
    Join result name patterns for -merge as CSV with NO spaces,
    quoting entries that contain spaces.
    """
    cleaned = []
    for p in patterns:
        p = str(p)
        if _needs_quotes(p):
            cleaned.append(f'"{_escape_quotes(p)}"')
        else:
            cleaned.append(p)
    return ",".join(cleaned)


@dataclass
class PADBBatch:
    exe_path: StrPath = r"C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe"
    _switches: List[Tuple[str, Union[List[str], Tuple[str, List[str]], None]]] = field(default_factory=list)

    last_switch_file: Optional[Path] = None
    last_cmd: Optional[List[str]] = None

    # --- Internal helpers ---
    def _add(self, switch: str, params: Optional[Iterable[str]] = None) -> "PADBBatch":
        if params is None:
            self._switches.append((switch, None))
        else:
            self._switches.append((switch, list(params)))
        return self

    def _add_block(self, switch: str, lines: Iterable[str]) -> "PADBBatch":
        self._switches.append((switch, ("BLOCK", list(lines))))
        return self

    # --- Switch methods ---
    def set_dir(self, path: StrPath) -> "PADBBatch":
        return self._add("-dir", [_to_str_path(path)])

    def lpod(self, mode: str, file: StrPath) -> "PADBBatch":
        mode = mode.lower().strip()
        if mode not in {"d", "s", "ds", "sd"}:
            raise ValueError("lpod mode must be one of: d, s, ds, sd")
        return self._add("-lpod", [mode, _to_str_path(file)])

    def lpods(self, pods: Sequence[Union[Dict[str, str], Sequence[str]]]) -> "PADBBatch":
        """Add multiple -lpod entries from a list of dicts or (mode,file) tuples."""
        for it in pods:
            if isinstance(it, dict):
                mode, file = it.get('mode'), it.get('file')
            elif isinstance(it, (list, tuple)) and len(it) >= 2:
                mode, file = it[0], it[1]
            else:
                raise ValueError("lpods expects items as dicts {'mode','file'} or tuples (mode,file)")
            if not mode or not file:
                raise ValueError("lpods item missing 'mode' or 'file'")
            self.lpod(str(mode), str(file))
        return self

    def spod(self, mode: str, file: StrPath) -> "PADBBatch":
        mode = mode.lower().strip()
        if mode not in {"d", "s", "ds", "sd"}:
            raise ValueError("spod mode must be one of: d, s, ds, sd")
        return self._add("-spod", [mode, _to_str_path(file)])

    def ext(self, mode: Optional[str] = None) -> "PADBBatch":
        if mode is None:
            return self._add("-ext")
        mode = mode.lower().strip()
        if mode not in {"r", "a"}:
            raise ValueError("ext mode must be 'r' or 'a'")
        return self._add("-ext", [mode])

    def an(self, names: Optional[Sequence[str]] = None) -> "PADBBatch":
        if not names:
            return self._add("-an")
        parts = []
        for n in names:
            parts.append(_quote(str(n)))
        return self._add("-an", parts)

    def suban(self, analysis_name: str, kvm: Dict[str, Union[str, int, float]]) -> "PADBBatch":
        lines = [_quote(analysis_name)]
        for k, v in kvm.items():
            lines.append(_kv_line(k, v))
        return self._add_block("-suban", lines)

    def fsuban(self, file: StrPath) -> "PADBBatch":
        return self._add("-fsuban", [_to_str_path(file)])

    def subex(self, kvm: Dict[str, Union[str, int, float]]) -> "PADBBatch":
        lines = []
        for k, v in kvm.items():
            lines.append(_kv_line(k, v))
        return self._add_block("-subex", lines)

    def fsubex(self, file: StrPath) -> "PADBBatch":
        return self._add("-fsubex", [_to_str_path(file)])

    def imp(self, mode: Optional[str] = None, scope: Optional[str] = None, files: Optional[Sequence[StrPath]] = None) -> "PADBBatch":
        params: List[str] = []
        if mode:
            m = mode.lower().strip()
            if m not in {"r", "a"}:
                raise ValueError("imp mode must be 'r' or 'a'")
            params.append(m)
        if scope:
            sc = scope.lower().strip()
            valid = {"allruns", "firstrun", "lastrun", "firstresult", "lastresult", "firstpoint", "lastpoint"}
            if sc not in valid:
                raise ValueError(f"imp scope must be one of: {', '.join(sorted(valid))}")
            params.append(sc)
        if files:
            joined = "|".join(_to_str_path(f) for f in files)
            params.append(joined)
        return self._add("-imp", params)

    def exp_dbdif(self, out_file: StrPath) -> "PADBBatch":
        return self._add("-exp", ["dbdif", _to_str_path(out_file)])

    def exp_csv(self, out_file: StrPath, template: Optional[StrPath] = None) -> "PADBBatch":
        params = ["csv", _to_str_path(out_file)]
        if template:
            params.append(_to_str_path(template))
        return self._add("-exp", params)

    def merge(self, options: Optional[Sequence[str]] = None, rename: Optional[str] = None, patterns: Optional[Sequence[str]] = None) -> "PADBBatch":
        params: List[str] = []
        if options:
            optset = "".join(ch.upper() for ch in options)
            for ch in optset:
                if ch not in "ADSX":
                    raise ValueError("merge options must be subset of {A,D,S,X}")
            params.append(optset)
        if rename:
            params.extend(["n", _quote(rename)])
        if not patterns:
            raise ValueError("merge requires at least one result name/pattern")
        params.append(_join_csv_patterns(patterns))
        return self._add("-merge", params)

    def dump(self, kind: str, file: StrPath, target: Optional[str] = None) -> "PADBBatch":
        kind_l = kind.lower().strip()
        if kind_l not in {"keys", "results", "conditions", "values"}:
            raise ValueError("dump kind must be one of: keys, results, conditions, values")
        params = [kind_l, _to_str_path(file)]
        if kind_l in {"conditions", "values"} and target:
            params.append(_quote(target))
        return self._add("-dump", params)

    def fan(self, file: StrPath) -> "PADBBatch":
        return self._add("-fan", [_to_str_path(file)])

    def usage(self) -> "PADBBatch":
        return self._add("-u")

    def from_file(self, file: StrPath) -> "PADBBatch":
        return self._add("-f", [_to_str_path(file)])

    def notify(self, emails: Union[str, Sequence[str]]) -> "PADBBatch":
        if isinstance(emails, str):
            em = emails
        else:
            em = ",".join(emails)
        return self._add("-notify", [em])

    def log(self, file: StrPath) -> "PADBBatch":
        return self._add("-log", [_to_str_path(file)])

    def trace(self, mode: str) -> "PADBBatch":
        mode_l = mode.lower().strip()
        if mode_l not in {"low", "med", "high"}:
            raise ValueError("trace mode must be: low, med, high")
        return self._add("-trace", [mode_l])

    def datevar(self, varname: str, start_date: str, intervals: int, interval_unit: str, fmt: Optional[str] = None) -> "PADBBatch":
        parts = [varname, _quote(start_date), str(int(intervals)), interval_unit]
        if fmt:
            parts.append(fmt)
        return self._add("-datevar", parts)

    # --- Rendering & run ---
    def _to_switch_file_text(self) -> str:
        lines: List[str] = []
        for sw, payload in self._switches:
            if payload is None:
                lines.append(sw)
            elif isinstance(payload, tuple) and payload[0] == "BLOCK":
                lines.append(sw)
                for pl in payload[1]:
                    lines.append(pl)
            else:
                parts = [sw]
                for p in payload:
                    if p.startswith('"') and p.endswith('"'):
                        parts.append(p)
                    else:
                        parts.append(_quote(p))
                lines.append(" ".join(parts))
        return "\n".join(lines) + "\n"

    def build_command(self, use_file: bool = True, switch_file_path: Optional[StrPath] = None) -> tuple[list[str], Optional[Path], str]:
        exe = _to_str_path(self.exe_path)
        if use_file:
            text = self._to_switch_file_text()
            if switch_file_path:
                sf = Path(switch_file_path)
                sf.parent.mkdir(parents=True, exist_ok=True)
                sf.write_text(text, encoding="utf-8")
                return [exe, "-f", str(sf)], sf, text
            else:
                tf = tempfile.NamedTemporaryFile(prefix="padb_", suffix=".txt", delete=False, mode="w", encoding="utf-8")
                tf.write(text)
                tf.flush()
                tf.close()
                return [exe, "-f", tf.name], Path(tf.name), text
        else:
            args: List[str] = [exe]
            for sw, payload in self._switches:
                if payload is None:
                    args.append(sw)
                elif isinstance(payload, tuple) and payload[0] == "BLOCK":
                    args.append(sw)
                    for pl in payload[1]:
                        args.append(pl)
                else:
                    args.append(sw)
                    for p in payload:
                        args.append(p)
            return args, None, ""

    def run(self, use_file: bool = True, switch_file_path: Optional[StrPath] = None, timeout: Optional[float] = None,
            cwd: Optional[StrPath] = None, env: Optional[dict] = None, check: bool = False, capture_output: bool = True) -> subprocess.CompletedProcess:
        # Enforced here, not just at the caller's queue, because this is the
        # one place every invocation path (webapp queue, direct CLI use)
        # actually goes through -- see wait_for_exclusive_padb_r()'s
        # docstring for why an in-memory queue alone isn't enough.
        wait_for_exclusive_padb_r(self.exe_path, max_wait=timeout or 600.0)
        cmd, sf, _ = self.build_command(use_file=use_file, switch_file_path=switch_file_path)
        self.last_switch_file = sf
        self.last_cmd = cmd
        run_cwd = None if cwd is None else _to_str_path(cwd)
        if capture_output:
            cp = subprocess.run(cmd, cwd=run_cwd, env=env, timeout=timeout, text=True, capture_output=True)
        else:
            cp = subprocess.run(cmd, cwd=run_cwd, env=env, timeout=timeout)
        if check and cp.returncode != 0:
            raise subprocess.CalledProcessError(cp.returncode, cmd, output=getattr(cp, "stdout", None), stderr=getattr(cp, "stderr", None))
        return cp

    def clear(self) -> "PADBBatch":
        self._switches.clear()
        self.last_switch_file = None
        self.last_cmd = None
        return self
