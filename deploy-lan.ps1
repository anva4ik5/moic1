<# 
.SYNOPSIS
    Deploy MonitorAgent to PCs on the local network.

.DESCRIPTION
    1. Scans the local subnet for online PCs (ping sweep)
    2. Lists found PCs — you choose which ones to deploy to
    3. Copies MonitorAgent.exe via admin share (\\PC\C$)
    4. Creates a scheduled task on each PC to run the agent

.NOTES
    Requirements:
    - Run as Administrator
    - WinRM or admin shares (C$) must be accessible on target PCs
    - Target PCs must have no password OR you must provide credentials
    - MonitorAgent.exe must be in the same directory as this script

.EXAMPLE
    .\deploy-lan.ps1
    .\deploy-lan.ps1 -Subnet "192.168.1" -ExePath ".\dist\MonitorAgent.exe"
#>

param(
    [string]$Subnet = "",
    [string]$ExePath = "",
    [string]$RemoteDir = "C:\ProgramData\MonitorAgent",
    [string]$TaskName = "MonitorAgentAutoStart",
    [switch]$ScanOnly
)

$ErrorActionPreference = "Continue"

# ─── find EXE ───
if (-not $ExePath) {
    $candidates = @(
        (Join-Path $PSScriptRoot "dist\MonitorAgent.exe"),
        (Join-Path $PSScriptRoot "MonitorAgent.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $ExePath = $c; break }
    }
}
if (-not $ExePath -or -not (Test-Path $ExePath)) {
    Write-Host "ERROR: MonitorAgent.exe not found." -ForegroundColor Red
    Write-Host "Build it first:  pyinstaller build.spec --noconfirm"
    Write-Host "Or specify path: .\deploy-lan.ps1 -ExePath path\to\MonitorAgent.exe"
    exit 1
}
Write-Host "Using EXE: $ExePath" -ForegroundColor Cyan

# ─── detect subnet ───
if (-not $Subnet) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 | 
           Where-Object { $_.InterfaceAlias -notmatch "Loopback" -and $_.IPAddress -ne "127.0.0.1" } | 
           Select-Object -First 1).IPAddress
    if ($ip) {
        $parts = $ip.Split(".")
        $Subnet = "$($parts[0]).$($parts[1]).$($parts[2])"
        Write-Host "Detected subnet: $Subnet.0/24 (your IP: $ip)" -ForegroundColor Cyan
    } else {
        Write-Host "ERROR: Could not detect subnet. Use -Subnet parameter." -ForegroundColor Red
        exit 1
    }
}

# ─── scan subnet ───
Write-Host "`nScanning $Subnet.1-254 for online PCs..." -ForegroundColor Yellow

$jobs = @()
1..254 | ForEach-Object {
    $target = "$Subnet.$_"
    $jobs += Start-Job -ScriptBlock {
        param($t)
        $ping = Test-Connection -ComputerName $t -Count 1 -Quiet -TimeoutSeconds 1
        if ($ping) {
            try {
                $hostname = [System.Net.Dns]::GetHostEntry($t).HostName
            } catch {
                $hostname = $t
            }
            [PSCustomObject]@{ IP = $t; Hostname = $hostname; Online = $true }
        }
    } -ArgumentList $target
}

Write-Host "Waiting for scan to complete..."
$results = $jobs | Wait-Job -Timeout 30 | Receive-Job
$jobs | Remove-Job -Force -ErrorAction SilentlyContinue

$myHostname = $env:COMPUTERNAME
$found = @($results | Where-Object { $_.Online -and $_.Hostname -notmatch $myHostname })

if ($found.Count -eq 0) {
    Write-Host "No other PCs found on the network." -ForegroundColor Yellow
    exit 0
}

Write-Host "`nFound $($found.Count) PC(s):" -ForegroundColor Green
$found | ForEach-Object { $i = 0 } {
    $i++
    Write-Host "  [$i] $($_.Hostname) ($($_.IP))" -ForegroundColor White
}

if ($ScanOnly) {
    Write-Host "`nScan-only mode. Exiting." -ForegroundColor Yellow
    exit 0
}

# ─── select targets ───
Write-Host "`nEnter PC numbers to deploy (comma-separated, or 'all'):" -ForegroundColor Yellow
$selection = Read-Host "Selection"

$targets = @()
if ($selection -eq "all") {
    $targets = $found
} else {
    $nums = $selection -split "," | ForEach-Object { [int]$_.Trim() }
    foreach ($n in $nums) {
        if ($n -ge 1 -and $n -le $found.Count) {
            $targets += $found[$n - 1]
        }
    }
}

if ($targets.Count -eq 0) {
    Write-Host "No targets selected." -ForegroundColor Yellow
    exit 0
}

# ─── deploy ───
Write-Host "`nDeploying to $($targets.Count) PC(s)..." -ForegroundColor Green

foreach ($pc in $targets) {
    $hostname = $pc.Hostname.Split(".")[0]
    $ip = $pc.IP
    Write-Host "`n--- Deploying to $hostname ($ip) ---" -ForegroundColor Cyan

    # Test admin share access
    $share = "\\$ip\C`$"
    if (-not (Test-Path $share -ErrorAction SilentlyContinue)) {
        Write-Host "  SKIP: Cannot access $share (need admin share access)" -ForegroundColor Red
        continue
    }

    # Create remote directory
    $remoteFullPath = $share + ($RemoteDir -replace "^C:", "") 
    try {
        New-Item -ItemType Directory -Path $remoteFullPath -Force -ErrorAction Stop | Out-Null
        Write-Host "  Created directory: $RemoteDir" -ForegroundColor DarkGray
    } catch {
        Write-Host "  SKIP: Cannot create $remoteFullPath — $_" -ForegroundColor Red
        continue
    }

    # Copy EXE
    $remoteExe = Join-Path $remoteFullPath "MonitorAgent.exe"
    try {
        Copy-Item -Path $ExePath -Destination $remoteExe -Force -ErrorAction Stop
        Write-Host "  Copied MonitorAgent.exe" -ForegroundColor Green
    } catch {
        Write-Host "  SKIP: Copy failed — $_" -ForegroundColor Red
        continue
    }

    # Create scheduled task remotely via schtasks
    $remoteExePath = Join-Path $RemoteDir "MonitorAgent.exe"
    $taskCmd = "schtasks /Create /S `"$ip`" /TN `"$TaskName`" /TR `"`"$remoteExePath`"`" /SC ONLOGON /RL HIGHEST /F"
    try {
        $result = Invoke-Expression $taskCmd 2>&1
        Write-Host "  Scheduled task created" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Task creation failed — $_" -ForegroundColor Yellow
        Write-Host "  You may need to create the task manually on $hostname" -ForegroundColor Yellow
    }

    # Try to start it now
    try {
        $startCmd = "schtasks /Run /S `"$ip`" /TN `"$TaskName`""
        Invoke-Expression $startCmd 2>&1 | Out-Null
        Write-Host "  Agent started!" -ForegroundColor Green
    } catch {
        Write-Host "  Agent will start on next logon" -ForegroundColor Yellow
    }
}

Write-Host "`n=== Deployment complete ===" -ForegroundColor Green
Write-Host "Each PC will show the setup wizard on first run."
Write-Host "Tip: Pre-configure bot token + admin ID before deploying to skip the wizard."
