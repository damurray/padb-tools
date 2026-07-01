"""
padb_scheduler.py — Windows Task Scheduler manager for PADB job files.

Manages schtasks entries for *_job.json files found in a user-specified
directory.  Task names follow the pattern PADB_{job_stem}.

Usage:
    py C:\\apps\\padb\\tools\\padb_scheduler.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = (
    r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data"
)
PADB_RUN = r"C:\apps\padb\tools\padb_run.py"
TASK_PREFIX = "PADB_"

# Day abbreviations used by schtasks (both input and output)
DAYS_ORDER = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_LABELS = {
    "MON": "Mon",
    "TUE": "Tue",
    "WED": "Wed",
    "THU": "Thu",
    "FRI": "Fri",
    "SAT": "Sat",
    "SUN": "Sun",
}

# ---------------------------------------------------------------------------
# schtasks helpers
# ---------------------------------------------------------------------------


def _run_schtasks(*args: str) -> tuple[int, str, str]:
    """Run schtasks with the given arguments. Returns (returncode, stdout, stderr)."""
    cmd = ["schtasks"] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "schtasks not found — this tool requires Windows."


def query_task(task_name: str) -> dict | None:
    """
    Query a single task by name.  Returns a dict with keys:
        schedule_type, days, start_time, status
    or None if the task does not exist.
    """
    rc, stdout, stderr = _run_schtasks("/query", "/tn", task_name, "/fo", "LIST")
    if rc != 0:
        return None
    return _parse_list_output(stdout)


def _parse_list_output(text: str) -> dict:
    """
    Parse schtasks /fo LIST output into a dict.
    Handles both English and localised field names defensively.
    Returns keys: schedule_type, days, start_time
    """
    info: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        info[key] = val

    # Schedule Type — field name varies by locale; look for common keywords
    stype = ""
    for k, v in info.items():
        if "schedule type" in k or "schedule" in k and "type" in k:
            stype = v.upper()
            break
        # Some locales use just "type" but that's too broad; skip unless confirmed
    if not stype:
        # Fallback: scan values for Weekly/Daily/Monthly
        for v in info.values():
            if v.upper() in ("WEEKLY", "DAILY", "MONTHLY", "ONE TIME"):
                stype = v.upper()
                break

    # Days — "days:" or "day:" field
    days_str = ""
    for k, v in info.items():
        if k in ("days", "day") or k.startswith("days"):
            days_str = v.upper()
            break

    # Start Time — "start time:" field; may look like "2:00:00 AM" or "02:00:00"
    start_raw = ""
    for k, v in info.items():
        if "start time" in k:
            start_raw = v
            break

    start_24 = _parse_time_to_24h(start_raw)

    return {
        "schedule_type": stype,
        "days": days_str,
        "start_time": start_24,
    }


def _parse_time_to_24h(raw: str) -> str:
    """
    Convert a time string like '2:00:00 AM', '14:30:00', or '2:00 AM' to 'HH:MM'.
    Returns '' if parsing fails.
    """
    if not raw:
        return ""
    raw = raw.strip()
    # Try 12-hour format with AM/PM
    m = re.match(r"(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)", raw, re.IGNORECASE)
    if m:
        h, minute, ampm = int(m.group(1)), m.group(2), m.group(3).upper()
        if ampm == "AM":
            if h == 12:
                h = 0
        else:
            if h != 12:
                h += 12
        return f"{h:02d}:{minute}"
    # Try 24-hour format
    m = re.match(r"(\d{1,2}):(\d{2})", raw)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return ""


def format_schedule_summary(info: dict) -> str:
    """Return a human-readable schedule string, e.g. 'Mon Wed Fri  02:00'."""
    if not info:
        return ""
    stype = info.get("schedule_type", "")
    days = info.get("days", "")
    time_str = info.get("start_time", "")

    if stype == "DAILY":
        return f"Daily  {time_str}"
    if stype == "WEEKLY":
        # Normalise: 'MON, WED, FRI' → 'Mon Wed Fri'
        day_parts = [d.strip().upper() for d in days.replace(",", " ").split() if d.strip()]
        day_display = " ".join(DAY_LABELS.get(d, d.capitalize()) for d in DAYS_ORDER if d in day_parts)
        return f"{day_display}  {time_str}"
    if stype:
        return f"{stype.capitalize()}  {time_str}"
    return ""


def discover_all_padb_tasks() -> set[str]:
    """
    Return the set of task names starting with PADB_ that exist in Task Scheduler.
    Runs schtasks /query /fo LIST on all tasks and filters.
    """
    rc, stdout, _ = _run_schtasks("/query", "/fo", "LIST")
    if rc != 0:
        return set()
    found: set[str] = set()
    for line in stdout.splitlines():
        # LIST format: "TaskName:    \PADB_foo" or "Task Name:   PADB_foo"
        # Task names may have a leading backslash (root folder prefix) — strip it.
        m = re.match(r"(?:Task\s*Name)\s*:\s*\\?(PADB_\S+)", line, re.IGNORECASE)
        if m:
            found.add(m.group(1).strip())
        # Fallback for localised field names
        elif line.strip().startswith("TaskName:") or "task name" in line.lower():
            parts = line.split(":", 1)
            if len(parts) == 2:
                name = parts[1].strip().lstrip("\\")
                if name.upper().startswith(TASK_PREFIX.upper()):
                    found.add(name)
    return found


def create_task(task_name: str, job_path: str, schedule_type: str,
                days: list[str], start_time: str) -> tuple[bool, str]:
    """
    Create or update a scheduled task.
    schedule_type: 'DAILY' or 'WEEKLY'
    days: list of day abbreviations (e.g. ['MON', 'WED']) — ignored for DAILY
    start_time: 'HH:MM' 24-hour string
    Returns (success, error_message).
    """
    # Build the task run command — quote the job path inside the /tr value
    tr_value = f'py "{PADB_RUN}" "{job_path}"'

    args = [
        "/create",
        "/tn", task_name,
        "/tr", tr_value,
        "/sc", schedule_type.lower(),
        "/st", start_time,
        "/f",               # force overwrite existing
    ]

    if schedule_type.upper() == "WEEKLY":
        if not days:
            return False, "No days selected for weekly schedule."
        args += ["/d", ",".join(days)]

    rc, stdout, stderr = _run_schtasks(*args)
    if rc == 0:
        return True, ""
    combined = (stdout + "\n" + stderr).strip()
    return False, combined or f"schtasks exited with code {rc}"


def delete_task(task_name: str) -> tuple[bool, str]:
    """Delete a scheduled task. Returns (success, error_message)."""
    rc, stdout, stderr = _run_schtasks("/delete", "/tn", task_name, "/f")
    if rc == 0:
        return True, ""
    combined = (stdout + "\n" + stderr).strip()
    return False, combined or f"schtasks exited with code {rc}"


# ---------------------------------------------------------------------------
# Schedule dialog
# ---------------------------------------------------------------------------

class ScheduleDialog(tk.Toplevel):
    """Modal dialog to add/edit a task schedule."""

    def __init__(self, parent: tk.Tk, job_name: str, job_path: Path,
                 existing: dict | None = None):
        super().__init__(parent)
        self.title(f"Schedule — {job_name}")
        self.resizable(False, False)
        self.grab_set()

        self.job_name = job_name
        self.job_path = job_path
        self.result: dict | None = None  # set on OK

        # Pre-fill from existing schedule
        pre_type = "WEEKLY"
        pre_days: set[str] = set()
        pre_time = "02:00"
        if existing:
            st = existing.get("schedule_type", "").upper()
            if st == "DAILY":
                pre_type = "DAILY"
            else:
                pre_type = "WEEKLY"
                days_raw = existing.get("days", "")
                pre_days = {d.strip().upper() for d in days_raw.replace(",", " ").split() if d.strip()}
            t = existing.get("start_time", "")
            if t:
                pre_time = t

        # --- Schedule type frame ---
        type_frame = ttk.LabelFrame(self, text="Schedule Type", padding=8)
        type_frame.pack(padx=14, pady=(14, 6), fill="x")

        self._stype = tk.StringVar(value=pre_type)
        ttk.Radiobutton(type_frame, text="Weekly (selected days)",
                        variable=self._stype, value="WEEKLY",
                        command=self._on_type_change).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(type_frame, text="Daily",
                        variable=self._stype, value="DAILY",
                        command=self._on_type_change).grid(row=0, column=1, sticky="w", padx=16)

        # --- Days frame ---
        days_frame = ttk.LabelFrame(self, text="Days of Week", padding=8)
        days_frame.pack(padx=14, pady=6, fill="x")

        self._day_vars: dict[str, tk.BooleanVar] = {}
        for col, day in enumerate(DAYS_ORDER):
            var = tk.BooleanVar(value=(day in pre_days))
            self._day_vars[day] = var
            cb = ttk.Checkbutton(days_frame, text=DAY_LABELS[day], variable=var)
            cb.grid(row=0, column=col, padx=4, sticky="w")

        # "Select all weekdays" shortcut
        shortcut_frame = ttk.Frame(days_frame)
        shortcut_frame.grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))
        ttk.Button(shortcut_frame, text="Weekdays",
                   command=self._select_weekdays).pack(side="left", padx=(0, 6))
        ttk.Button(shortcut_frame, text="All Days",
                   command=self._select_all).pack(side="left", padx=(0, 6))
        ttk.Button(shortcut_frame, text="Clear",
                   command=self._clear_days).pack(side="left")

        self._days_frame_widget = days_frame  # keep ref for enable/disable

        # --- Time frame ---
        time_frame = ttk.LabelFrame(self, text="Start Time (24-hour HH:MM)", padding=8)
        time_frame.pack(padx=14, pady=6, fill="x")

        # Parse pre_time
        try:
            ph, pm = pre_time.split(":")
        except ValueError:
            ph, pm = "02", "00"

        self._hour_var = tk.StringVar(value=ph)
        self._min_var = tk.StringVar(value=pm)

        hour_spin = ttk.Spinbox(time_frame, from_=0, to=23, width=4,
                                textvariable=self._hour_var, format="%02.0f",
                                wrap=True)
        hour_spin.grid(row=0, column=0)
        ttk.Label(time_frame, text=":").grid(row=0, column=1, padx=2)
        min_spin = ttk.Spinbox(time_frame, from_=0, to=59, width=4,
                               textvariable=self._min_var, format="%02.0f",
                               wrap=True)
        min_spin.grid(row=0, column=2)
        ttk.Label(time_frame, text="  (e.g. 02:00 for 2 AM, 23:30 for 11:30 PM)",
                  foreground="#666").grid(row=0, column=3, padx=10)

        # --- Button bar ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(padx=14, pady=(6, 14), fill="x")

        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side="right", padx=(6, 0))
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btn_frame, text="Test Run Now",
                   command=self._test_run).pack(side="left")

        # Apply initial state
        self._on_type_change()

        # Centre over parent
        self.update_idletasks()
        pw, ph_px = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        dw, dh = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - dw)//2}+{py + (ph_px - dh)//2}")

    # --- internal helpers ---

    def _on_type_change(self) -> None:
        state = "normal" if self._stype.get() == "WEEKLY" else "disabled"
        for widget in self._days_frame_widget.winfo_children():
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass  # frames don't support state

    def _select_weekdays(self) -> None:
        for day, var in self._day_vars.items():
            var.set(day in ("MON", "TUE", "WED", "THU", "FRI"))

    def _select_all(self) -> None:
        for var in self._day_vars.values():
            var.set(True)

    def _clear_days(self) -> None:
        for var in self._day_vars.values():
            var.set(False)

    def _build_time_str(self) -> str | None:
        try:
            h = int(self._hour_var.get())
            m = int(self._min_var.get())
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            return f"{h:02d}:{m:02d}"
        except ValueError:
            return None

    def _on_ok(self) -> None:
        stype = self._stype.get()
        time_str = self._build_time_str()
        if time_str is None:
            messagebox.showerror("Invalid Time",
                                 "Enter a valid time (HH 0–23, MM 0–59).", parent=self)
            return

        days: list[str] = []
        if stype == "WEEKLY":
            days = [d for d in DAYS_ORDER if self._day_vars[d].get()]
            if not days:
                messagebox.showerror("No Days Selected",
                                     "Select at least one day for a weekly schedule.",
                                     parent=self)
                return

        self.result = {
            "schedule_type": stype,
            "days": days,
            "start_time": time_str,
        }
        self.destroy()

    def _test_run(self) -> None:
        """Launch the job immediately in a non-blocking subprocess."""
        cmd = [sys.executable, PADB_RUN, str(self.job_path)]
        try:
            subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            messagebox.showinfo("Test Run Started",
                                f"Job launched in a new console window:\n{self.job_path.name}",
                                parent=self)
        except Exception as exc:
            messagebox.showerror("Launch Error", str(exc), parent=self)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class PADBScheduler(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PADB Scheduler")
        self.geometry("720x420")
        self.minsize(600, 300)

        self._data_dir: Path = Path(DEFAULT_DATA_DIR)
        # job rows: list of dicts with keys: name, path, task_name, scheduled, info
        self._rows: list[dict] = []

        self._build_ui()
        self._refresh()

    # --- UI construction ---

    def _build_ui(self) -> None:
        # Top bar: directory picker
        top = ttk.Frame(self, padding=(8, 6, 8, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Job directory:").pack(side="left")
        self._dir_var = tk.StringVar(value=str(self._data_dir))
        dir_entry = ttk.Entry(top, textvariable=self._dir_var, width=55)
        dir_entry.pack(side="left", padx=6)
        ttk.Button(top, text="Browse…", command=self._browse).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=4)

        # Treeview
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=6)

        cols = ("name", "scheduled", "schedule")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                   selectmode="browse")
        self._tree.heading("name", text="Job File")
        self._tree.heading("scheduled", text="Scheduled?")
        self._tree.heading("schedule", text="Schedule")

        self._tree.column("name", width=250, minwidth=150)
        self._tree.column("scheduled", width=80, minwidth=60, anchor="center")
        self._tree.column("schedule", width=270, minwidth=120)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                             command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Tag for orphan rows (task exists but no job file)
        self._tree.tag_configure("orphan", foreground="#888888")
        # Tag for scheduled rows
        self._tree.tag_configure("scheduled", foreground="#005500")

        self._tree.bind("<Double-1>", self._on_double_click)

        # Button bar
        btn_frame = ttk.Frame(self, padding=(8, 4, 8, 8))
        btn_frame.pack(fill="x")

        ttk.Button(btn_frame, text="Add / Edit Schedule",
                   command=self._add_edit).pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="Remove Schedule",
                   command=self._remove).pack(side="left", padx=(0, 6))
        ttk.Separator(btn_frame, orient="vertical").pack(side="left", fill="y",
                                                          padx=6)
        ttk.Button(btn_frame, text="Refresh",
                   command=self._refresh).pack(side="left")

        # Status bar
        self._status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self, textvariable=self._status_var,
                                relief="sunken", anchor="w", padding=(6, 2))
        status_bar.pack(fill="x", side="bottom")

    # --- Directory handling ---

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=str(self._data_dir),
            title="Select Job Directory",
        )
        if chosen:
            self._data_dir = Path(chosen)
            self._dir_var.set(str(self._data_dir))
            self._refresh()

    # --- Data refresh ---

    def _refresh(self) -> None:
        # Re-read directory from entry widget (user may have typed a path)
        typed = self._dir_var.get().strip()
        if typed:
            self._data_dir = Path(typed)

        self._status_var.set("Refreshing…")
        self.update_idletasks()

        # Discover job files
        job_files: list[Path] = []
        if self._data_dir.is_dir():
            job_files = sorted(self._data_dir.glob("*_job.json"))

        # Discover all PADB_ tasks currently in Task Scheduler
        all_padb_tasks = discover_all_padb_tasks()

        rows: list[dict] = []

        # Build rows for each job file
        job_task_names: set[str] = set()
        for jp in job_files:
            task_name = TASK_PREFIX + jp.stem
            job_task_names.add(task_name.upper())
            info = query_task(task_name)
            scheduled = info is not None
            rows.append({
                "name": jp.name,
                "path": jp,
                "task_name": task_name,
                "scheduled": scheduled,
                "info": info,
                "orphan": False,
            })

        # Add orphan rows: tasks in scheduler with no matching job file
        for tname in sorted(all_padb_tasks):
            if tname.upper() not in job_task_names:
                # Derive what the job stem would be
                stem = tname[len(TASK_PREFIX):]  # strip PADB_ prefix
                info = query_task(tname)
                rows.append({
                    "name": f"{stem}_job.json  (orphan — file not found)",
                    "path": None,
                    "task_name": tname,
                    "scheduled": True,
                    "info": info,
                    "orphan": True,
                })

        self._rows = rows
        self._populate_tree()
        n_jobs = sum(1 for r in rows if not r["orphan"])
        n_sched = sum(1 for r in rows if r["scheduled"] and not r["orphan"])
        n_orphan = sum(1 for r in rows if r["orphan"])
        status = f"{n_jobs} job(s) found, {n_sched} scheduled"
        if n_orphan:
            status += f", {n_orphan} orphan task(s)"
        self._status_var.set(status)

    def _populate_tree(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)

        for i, row in enumerate(self._rows):
            sched_text = "Yes" if row["scheduled"] else "No"
            summary = format_schedule_summary(row["info"]) if row["info"] else ""
            tags: list[str] = []
            if row["orphan"]:
                tags.append("orphan")
            elif row["scheduled"]:
                tags.append("scheduled")
            self._tree.insert("", "end", iid=str(i),
                               values=(row["name"], sched_text, summary),
                               tags=tags)

    # --- Selection helper ---

    def _selected_row(self) -> dict | None:
        sel = self._tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        return self._rows[idx]

    # --- Button actions ---

    def _on_double_click(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._add_edit()

    def _add_edit(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("No Selection", "Select a job row first.")
            return
        if row["orphan"]:
            messagebox.showwarning(
                "Orphan Task",
                "This task's job file no longer exists.\n"
                "You can only remove it, not edit its schedule here.",
            )
            return

        dlg = ScheduleDialog(
            self,
            job_name=row["name"],
            job_path=row["path"],
            existing=row["info"],
        )
        self.wait_window(dlg)

        if dlg.result is None:
            return  # cancelled

        r = dlg.result
        ok, err = create_task(
            task_name=row["task_name"],
            job_path=str(row["path"]),
            schedule_type=r["schedule_type"],
            days=r["days"],
            start_time=r["start_time"],
        )
        if ok:
            self._status_var.set(f"Task '{row['task_name']}' created/updated.")
            self._refresh()
        else:
            messagebox.showerror("schtasks Error",
                                  f"Failed to create task:\n\n{err}")

    def _remove(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("No Selection", "Select a job row first.")
            return
        if not row["scheduled"]:
            messagebox.showinfo("Not Scheduled",
                                 f"'{row['name']}' has no scheduled task to remove.")
            return

        confirm = messagebox.askyesno(
            "Confirm Remove",
            f"Delete scheduled task:\n  {row['task_name']}\n\nAre you sure?",
        )
        if not confirm:
            return

        ok, err = delete_task(row["task_name"])
        if ok:
            self._status_var.set(f"Task '{row['task_name']}' removed.")
            self._refresh()
        else:
            messagebox.showerror("schtasks Error",
                                  f"Failed to delete task:\n\n{err}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = PADBScheduler()
    app.mainloop()


if __name__ == "__main__":
    main()
