# Install Windows Task Scheduler jobs for Quant Strategy Lab automation workers.
# Tasks are created DISABLED by default. Review and enable manually.
#
# IMPORTANT: Windows Task Scheduler uses the computer's LOCAL timezone.
# Target schedule is America/New_York (US Eastern):
#   After-close evaluation: weekdays 4:15 PM Eastern
#   Market-open execution:  weekdays 9:35 AM Eastern
#   Order sync:             every 5 min during market hours (9:30 AM - 4:00 PM Eastern)
#   Daily reconciliation:   weekdays 4:30 PM Eastern
#
# Convert these times to your local timezone before enabling tasks.
# Example: If your PC is US Central (UTC-6), 9:35 AM Eastern = 8:35 AM Central.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

$Tasks = @(
    @{
        Name = "QuantStrategyLab-AfterCloseEvaluation"
        Script = "run_after_close_evaluation.ps1"
        Schedule = "Daily"
        At = "16:15"
        Description = "After-close strategy evaluation (adjust At for your timezone vs Eastern 4:15 PM)"
    },
    @{
        Name = "QuantStrategyLab-MarketOpenExecution"
        Script = "run_market_open_execution.ps1"
        Schedule = "Daily"
        At = "09:35"
        Description = "Market-open automated order execution (adjust At for Eastern 9:35 AM)"
    },
    @{
        Name = "QuantStrategyLab-OrderSync"
        Script = "run_order_sync.ps1"
        Schedule = "Minute"
        Interval = 5
        Duration = "PT6H30M"
        At = "09:30"
        Description = "Order sync every 5 min during market window (adjust for Eastern hours)"
    },
    @{
        Name = "QuantStrategyLab-DailyReconciliation"
        Script = "run_daily_reconciliation.ps1"
        Schedule = "Daily"
        At = "16:30"
        Description = "Daily reconciliation after market close (adjust for Eastern 4:30 PM)"
    }
)

Write-Host "Project root: $ProjectRoot"
Write-Host ""
Write-Host "Creating DISABLED scheduled tasks..."
Write-Host "Workers use Alpaca clock/calendar internally to skip non-trading days."
Write-Host ""

foreach ($Task in $Tasks) {
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\$($Task.Script)`"" `
        -WorkingDirectory $ProjectRoot

    if ($Task.Schedule -eq "Minute") {
        $Trigger = New-ScheduledTaskTrigger -Once -At $Task.At -RepetitionInterval (New-TimeSpan -Minutes $Task.Interval) -RepetitionDuration $Task.Duration
    } else {
        $Trigger = New-ScheduledTaskTrigger -Daily -At $Task.At
    }

    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $Task.Name -Action $Action -Trigger $Trigger -Settings $Settings -Description $Task.Description -Force | Out-Null
    Disable-ScheduledTask -TaskName $Task.Name | Out-Null
    Write-Host "[CREATED DISABLED] $($Task.Name)"
}

Write-Host ""
Write-Host "Tasks created but NOT enabled."
Write-Host "1. Open Task Scheduler and review each task's trigger time in YOUR local timezone."
Write-Host "2. Convert from America/New_York as documented above."
Write-Host "3. Enable tasks only after enabling automation in the app and disengaging the kill switch."
Write-Host "4. Ensure .venv exists and Alpaca credentials are configured in .env."
