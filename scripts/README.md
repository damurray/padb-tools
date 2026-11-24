# Scripts

Developer / ops helpers (not part of the analysis pipeline):

- `build_help_pdfs.py` -- regenerate local PDF copies of the main guides (needs Playwright + Chromium):
  `py scripts/build_help_pdfs.py`.
- `fix_claude_launch.ps1` -- repair the Claude desktop launch failure ("Another program is currently using this file");
  run it from a plain PowerShell window. Background in `CLAUDE.md`.
- `backup_memory.ps1` -- back up the local Claude project memory folder.
