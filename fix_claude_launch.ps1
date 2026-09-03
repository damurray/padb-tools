# Recovers the Claude desktop app when launching it throws "Another program is
# currently using this file" (the dialog's title bar is the WindowsApps package
# path, not "Claude" -- that's the tell that Windows' deployment layer is
# complaining, not the app).
#
# Cause: Claude installs as an MSIX package and auto-updates overnight. If any
# process from the old version is still alive when the new one is staged,
# Windows logs event 658 "marking package for deferred registration" and parks
# the swap. The next launch has to finalize that registration first, hits the
# locked package files, and fails with 0x80073D02 (ERROR_PACKAGES_IN_USE).
# Ending "Claude" in Task Manager does not clear it: the headless helpers sit
# under Background processes rather than in the app's tree, and the deferred
# registration only retries at logon -- hence the reboot-a-day habit.
#
# Run from a PLAIN PowerShell window, NOT a terminal inside Claude -- step 1
# kills the process hosting that terminal.
#
# Prevention: quit Claude from the tray icon (not just the window's X) before
# leaving for the day, so the overnight update can register cleanly.
$family = "Claude_pzs8sxrjxfjjc"

# 1. Reap every process from the package, including the detached helpers.
$procs = @(Get-Process claude -ErrorAction SilentlyContinue)
Write-Host "Stopping $($procs.Count) claude process(es)..."
$procs | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$left = @(Get-Process claude -ErrorAction SilentlyContinue)
if ($left.Count) { Write-Warning "$($left.Count) process(es) survived; re-run this script." }

# 2. Force the parked registration to complete. Per-user, no admin needed.
Write-Host "Re-registering $family ..."
try {
    Add-AppxPackage -RegisterByFamilyName -MainPackage $family -ErrorAction Stop
    Write-Host "OK -- registered version $((Get-AppxPackage -Name Claude).Version). Launch Claude normally."
} catch {
    Write-Warning "Re-register failed: $($_.Exception.Message)"
    Write-Warning "Sign out of Windows and back in -- a logon always clears a deferred registration."
}

# 3. Show what the deployment log actually recorded, for confirmation.
Write-Host "`nRecent package-update conflicts:"
Get-WinEvent -FilterHashtable @{
    LogName   = "Microsoft-Windows-AppXDeploymentServer/Operational"
    StartTime = (Get-Date).AddDays(-7)
} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match "Claude" -and $_.Id -in 658, 419 } |
    Select-Object TimeCreated, Id, @{n = "Message"; e = { $_.Message -replace "\s+", " " } } |
    Sort-Object TimeCreated | Format-Table -AutoSize -Wrap
