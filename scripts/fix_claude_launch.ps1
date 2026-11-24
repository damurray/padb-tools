# Recovers the Claude desktop app when launching it throws "Another program is
# currently using this file" (the dialog's title bar is the WindowsApps package
# path, not "Claude" -- that's the tell that Windows' deployment layer is
# complaining, not the app).
#
# Cause: Claude installs as an MSIX package and auto-updates overnight/midday. If
# ANY process from the old version is still alive when the new one is staged,
# Windows logs event 638 ("Packages were not updated because affected apps are
# still running") + 658 ("marking package for deferred registration") and parks
# the swap. The next launch has to finalize that registration first, hits the
# locked package files, and fails with 0x80073D02 (ERROR_PACKAGES_IN_USE).
#
# THE BLOCKER IS USUALLY THE PACKAGED SERVICE, NOT THE GUI (found 2026-09-08):
# recent Claude builds install `CoworkVMService` (DisplayName "Claude"), a
# LocalSystem, StartMode=Auto Windows service whose binary --
# `...\WindowsApps\Claude_<ver>\app\resources\cowork-svc.exe` -- runs from INSIDE
# the package folder and keeps running after you close the GUI window. It holds
# the package open, and `Get-Process claude` never sees it (different exe name),
# which is exactly why the previous version of this script (kill claude.exe only)
# did NOT remedy the issue and a reboot was needed. Its service ACL grants
# Authenticated Users SERVICE_STOP (`sc.exe sdshow CoworkVMService`), so stopping
# it needs NO admin.
#
# Run from a PLAIN PowerShell window, NOT a terminal inside Claude -- the steps
# below kill the process hosting that terminal and stop the cowork service.
#
# Prevention: closing the GUI window does NOT stop CoworkVMService (Auto/
# LocalSystem -- it restarts at logon regardless), so the overnight-update
# conflict is largely inherent now. This script (which stops the service) is the
# reliable morning remedy; a reboot/logon also works because it forces the
# deferred registration and restarts everything clean.
$family = "Claude_pzs8sxrjxfjjc"
$svc    = "CoworkVMService"

# 1. Stop the packaged background service FIRST -- the piece the old version
#    missed. Authenticated Users have SERVICE_STOP on it, so no admin needed.
$s = Get-Service -Name $svc -ErrorAction SilentlyContinue
if ($s) {
    Write-Host "Stopping service $svc (cowork-svc.exe) ..."
    try { Stop-Service -Name $svc -Force -ErrorAction Stop }
    catch {
        Write-Warning "Stop-Service failed ($($_.Exception.Message)); trying sc.exe stop ..."
        & sc.exe stop $svc | Out-Null
    }
    for ($i = 0; $i -lt 20 -and (Get-Service $svc -ErrorAction SilentlyContinue).Status -ne 'Stopped'; $i++) {
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  $svc status: $((Get-Service $svc -ErrorAction SilentlyContinue).Status)"
} else {
    Write-Host "Service $svc not present (older Claude build) -- skipping."
}
# Belt-and-suspenders: kill any stray cowork-svc.exe still holding the package.
Get-Process cowork-svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 2. Reap every claude.exe process, including the detached helpers.
$procs = @(Get-Process claude -ErrorAction SilentlyContinue)
Write-Host "Stopping $($procs.Count) claude process(es)..."
$procs | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$left = @(Get-Process claude -ErrorAction SilentlyContinue) + @(Get-Process cowork-svc -ErrorAction SilentlyContinue)
if ($left.Count) { Write-Warning "$($left.Count) package process(es) survived; re-run this script." }

# 3. Force the parked registration to complete. Per-user, no admin needed.
Write-Host "Re-registering $family ..."
try {
    Add-AppxPackage -RegisterByFamilyName -MainPackage $family -ErrorAction Stop
    Write-Host "OK -- registered version $((Get-AppxPackage -Name Claude).Version). Launch Claude normally."
} catch {
    Write-Warning "Re-register failed: $($_.Exception.Message)"
    Write-Warning "Sign out of Windows and back in -- a logon always clears a deferred registration."
}

# 4. Show what the deployment log actually recorded, for confirmation.
Write-Host "`nRecent package-update conflicts:"
Get-WinEvent -FilterHashtable @{
    LogName   = "Microsoft-Windows-AppXDeploymentServer/Operational"
    StartTime = (Get-Date).AddDays(-7)
} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match "Claude" -and $_.Id -in 638, 658, 419 } |
    Select-Object TimeCreated, Id, @{n = "Message"; e = { $_.Message -replace "\s+", " " } } |
    Sort-Object TimeCreated | Format-Table -AutoSize -Wrap
