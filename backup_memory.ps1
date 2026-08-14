# Mirrors Claude Code's persistent memory notes for this project to OneDrive,
# so they survive a local drive failure (the memory folder itself has no
# built-in backup/sync -- it's just plain files under the user profile).
# Run daily via the "Claude_Memory_Backup" Windows Scheduled Task
# (powershell.exe -File, not "py" -- avoids the Store-alias-stub bug that
# broke padb_scheduler.py's own tasks; see CLAUDE.md).
$src = "C:\Users\damurray\.claude\projects\C--apps-padb-tools\memory"
$dst = "C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\claude_memory_backup"
robocopy $src $dst /MIR /NFL /NDL /NJH /NJS /R:2 /W:5
