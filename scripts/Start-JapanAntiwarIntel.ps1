param([switch]$AutoStart)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Distro = "Ubuntu-OpenClaw"
$ProxyBridgePort = 17897
$LocalAppPort = 18090
$WslAppPort = 8190
$InstagramLoginHelperPort = 18190
$script:KeepAliveProcess = $null
$script:PortBridgeProcess = $null
$script:InstagramLoginHelperProcess = $null
$script:LastUrl = "http://127.0.0.1:$LocalAppPort"
$script:LogPath = Join-Path $ProjectRoot "launcher.log"

function Add-Log {
    param([string]$Text)
    $time = Get-Date -Format "HH:mm:ss"
    $log.AppendText("[$time] $Text`r`n")
    Add-Content -Path $script:LogPath -Value "[$time] $Text" -Encoding UTF8
    $log.SelectionStart = $log.Text.Length
    $log.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function To-WslPath {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $drive = $full.Substring(0, 1).ToLower()
    $rest = $full.Substring(2).Replace('\', '/')
    return "/mnt/$drive$rest"
}

function Run-WslScript {
    param([string]$Script, [int]$TimeoutSeconds = 300)
    $tmp = Join-Path $ProjectRoot (".launcher-task-$PID-$([guid]::NewGuid().ToString('N')).sh")
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, ($Script -replace "`r`n", "`n"), $utf8)
    $wslTmp = To-WslPath $tmp

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "wsl.exe"
    $psi.Arguments = "-d $Distro -u root -- bash " + '"' + $wslTmp + '"'
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
        try { $p.Kill() } catch {}
        throw "WSL task timed out"
    }
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $output = $stdout + $stderr
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) {
        throw $output
    }
    return $stdout
}

function Test-Port {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(300)
        if ($ok) { $client.EndConnect($iar) }
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

function Test-HttpHealth {
    param([string]$Url)
    try {
        $result = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 2
        return [bool]$result.ok
    } catch {
        return $false
    }
}

function Stop-StaleStartupProcesses {
    param([switch]$IncludeLaunchers)

    if ($IncludeLaunchers) {
        Get-CimInstance Win32_Process | Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -match "Start-JapanAntiwarIntel\.ps1" -and
            $_.CommandLine -match [regex]::Escape($ProjectRoot)
        } | ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
        }
    }

    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match "wsl\.exe" -and
        $_.CommandLine -match "japan-antiwar-intel" -and
        $_.CommandLine -match "\.launcher-task"
    } | ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }

    try {
        $wslProject = To-WslPath $ProjectRoot
        & wsl.exe -d $Distro -u root -- bash -lc @"
ps -eo pid=,args= | awk '/[d]ocker compose up -d --build/ {print `$1}' | xargs -r kill -9
ps -eo pid=,args= | awk '/[.]launcher-task(-[0-9a-fA-F-]+)?[.]sh/ {print `$1}' | xargs -r kill -9
find '$wslProject' -maxdepth 1 -name '.launcher-task*.sh' -mmin +10 -delete 2>/dev/null || true
"@ | Out-Null
    } catch {}
}

function Ensure-SingleLauncher {
    $existing = Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -match "Start-JapanAntiwarIntel\.ps1" -and
        $_.CommandLine -match [regex]::Escape($ProjectRoot)
    }
    if (-not $existing) { return }

    if (Test-HttpHealth $script:LastUrl) {
        Start-Process $script:LastUrl
        exit
    }

    [System.Windows.Forms.MessageBox]::Show(
        "A stale launcher is still starting. It will be cleaned up and restarted; no PC reboot is needed.",
        "Japan Antiwar Intel"
    ) | Out-Null
    Stop-StaleStartupProcesses -IncludeLaunchers
    Start-Sleep -Seconds 2
}

Ensure-SingleLauncher

function Start-ProxyBridge {
    if (-not (Test-Port $ProxyBridgePort)) {
        Add-Log "Starting local network bridge..."
        Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList @(
            "scripts\wsl_proxy_bridge.py",
            "--listen-host", "0.0.0.0",
            "--listen-port", "$ProxyBridgePort",
            "--target-host", "127.0.0.1",
            "--target-port", "7897"
        ) -WorkingDirectory $ProjectRoot
        Start-Sleep -Seconds 1
    }
}

function Configure-EnvProxy {
    $envPath = Join-Path $ProjectRoot ".env"
    $content = Get-Content $envPath -Raw
    if ($content -notmatch "(?m)^OUTBOUND_HTTP_PROXY=") {
        $content = $content.TrimEnd() + "`r`nOUTBOUND_HTTP_PROXY=http://172.19.240.1:$ProxyBridgePort`r`n"
    } else {
        $content = $content -replace "(?m)^OUTBOUND_HTTP_PROXY=.*", "OUTBOUND_HTTP_PROXY=http://172.19.240.1:$ProxyBridgePort"
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($envPath, $content, $utf8)
}

function Start-WslKeepAlive {
    if ($script:KeepAliveProcess -and -not $script:KeepAliveProcess.HasExited) { return }
    Add-Log "Keeping WSL awake while launcher is open..."
    $cmd = "while (`$true) { & wsl.exe -d $Distro -u root -- true | Out-Null; Start-Sleep -Seconds 5 }"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    $script:KeepAliveProcess = Start-Process -WindowStyle Hidden -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded
    ) -PassThru
    Start-Sleep -Seconds 2
}

function Configure-DockerProxy {
    Add-Log "Configuring Docker network bridge..."
    Run-WslScript @"
set -e
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/http-proxy.conf <<'EOF'
[Service]
Environment=HTTP_PROXY=http://172.19.240.1:$ProxyBridgePort
Environment=HTTPS_PROXY=http://172.19.240.1:$ProxyBridgePort
Environment=NO_PROXY=localhost,127.0.0.1,::1,postgres,n8n,freshrss,rsshub,redis,changedetection,qdrant,analysis-worker
EOF
systemctl daemon-reload
systemctl start docker
"@ | Out-Null
}

function Start-Compose {
    $wslProject = To-WslPath $ProjectRoot
    Add-Log "Starting Docker Compose services..."
    Run-WslScript @"
set -e
cd '$wslProject'
docker compose up -d --remove-orphans
"@ 1800 | Out-Null
}

function Get-WslIp {
    $result = Run-WslScript "ip -4 -o addr show eth0 | awk '{print `$4}' | cut -d/ -f1 | head -n1"
    $match = [regex]::Match($result, "\b(?:\d{1,3}\.){3}\d{1,3}\b")
    if (-not $match.Success) {
        throw "Could not detect WSL IPv4 address. Raw output: $result"
    }
    return $match.Value
}

function Stop-OldJapanPortBridge {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match "windows_port_bridge.py" -and
        (
            $_.CommandLine -match "8190:8090" -or
            $_.CommandLine -match "8190:8190" -or
            $_.CommandLine -match "18090:8190" -or
            $_.CommandLine -match "--ports .*8190"
        )
    } | ForEach-Object {
        try {
            Add-Log "Stopping stale Japan localhost bridge pid $($_.ProcessId)..."
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {}
    }
}

function Stop-StaleLocalPreview {
    Get-NetTCPConnection -LocalPort 8190 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $ownerPid = $_.OwningProcess
        if (-not $ownerPid) { return }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -match "uvicorn app\.main:app") {
            Add-Log "Stopping stale local preview backend pid $ownerPid..."
            Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-WindowsPortBridge {
    Stop-OldJapanPortBridge
    Stop-StaleLocalPreview
    $wslIp = Get-WslIp
    Add-Log "Starting Windows localhost bridge to WSL $wslIp..."
    if ($script:PortBridgeProcess -and -not $script:PortBridgeProcess.HasExited) {
        try { $script:PortBridgeProcess.Kill() } catch {}
    }
    $script:PortBridgeProcess = Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList @(
        "scripts\windows_port_bridge.py",
        "--listen-host", "127.0.0.1",
        "--target-host", $wslIp,
        "--ports", "1210", "15100", "6433", "5782",
        "--map", "$LocalAppPort`:$WslAppPort", "18181:8181"
    ) -WorkingDirectory $ProjectRoot -PassThru
    Start-Sleep -Seconds 1
}

function Start-InstagramLoginHelper {
    if (Test-Port $InstagramLoginHelperPort) {
        Add-Log "Instagram login helper is already running on $InstagramLoginHelperPort."
        return
    }
    Add-Log "Starting Instagram login helper..."
    $script:InstagramLoginHelperProcess = Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList @(
        "scripts\instagram_login_helper.py",
        "--port", "$InstagramLoginHelperPort"
    ) -WorkingDirectory $ProjectRoot -PassThru
    Start-Sleep -Seconds 1
}

function Wait-Health {
    Add-Log "Waiting for analysis service..."
    for ($i = 1; $i -le 45; $i++) {
        try {
            $result = Invoke-RestMethod -Uri "http://127.0.0.1:$LocalAppPort/health" -TimeoutSec 3
            if ($result.ok) { return "http://127.0.0.1:$LocalAppPort" }
        } catch {}
        Start-Sleep -Seconds 2
    }
    $wslIp = Get-WslIp
    return "http://$($wslIp):$WslAppPort"
}

function Stop-AllRelatedProcesses {
    Add-Log "Cleaning up previous instances..."
    Stop-StaleStartupProcesses
    $wslProject = To-WslPath $ProjectRoot
    try {
        Add-Log "Checking for stale startup tasks..."
        Run-WslScript @"
set -e
cd '$wslProject'
true
"@ 120 | Out-Null
    } catch {
        Add-Log "stale startup task check skipped or failed."
    }

    # 杀掉旧的端口桥接进程 (windows_port_bridge.py)
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match "windows_port_bridge\.py|wsl_proxy_bridge\.py|instagram_login_helper\.py"
    } | ForEach-Object {
        try {
            Add-Log "Killing stale bridge/helper process pid $($_.ProcessId): $($_.Name)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {}
    }

    # 杀掉旧的 uvicorn 后端进程
    Get-NetTCPConnection -LocalPort 8090,8190 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $ownerPid = $_.OwningProcess
        if (-not $ownerPid) { return }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -match "uvicorn") {
            Add-Log "Killing stale uvicorn process pid $ownerPid..."
            Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
        }
    }

    # 杀掉 WSL keep-alive 心跳进程
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match "wsl\.exe.*keep|wsl\.exe.*true.*Start-Sleep" -or
        $_.CommandLine -match "WSL awake|wsl.*-- true"
    } | ForEach-Object {
        try {
            Add-Log "Killing stale WSL keep-alive pid $($_.ProcessId)..."
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {}
    }

    # 清理全局变量引用
    if ($script:KeepAliveProcess -and -not $script:KeepAliveProcess.HasExited) {
        try { $script:KeepAliveProcess.Kill() } catch {}
    }
    if ($script:PortBridgeProcess -and -not $script:PortBridgeProcess.HasExited) {
        try { $script:PortBridgeProcess.Kill() } catch {}
    }
    if ($script:InstagramLoginHelperProcess -and -not $script:InstagramLoginHelperProcess.HasExited) {
        try { $script:InstagramLoginHelperProcess.Kill() } catch {}
    }

    Start-Sleep -Seconds 2
    Add-Log "Cleanup done."
}

function Start-System {
    try {
        $startButton.Enabled = $false
        Add-Log "Starting Japan Antiwar Intel..."
        Stop-AllRelatedProcesses
        Start-ProxyBridge
        Configure-EnvProxy
        Start-WslKeepAlive
        Configure-DockerProxy
        Start-Compose
        Start-WindowsPortBridge
        Start-InstagramLoginHelper
        $script:LastUrl = Wait-Health
        Add-Log "System is ready: $script:LastUrl"
        Start-Process $script:LastUrl
    } catch {
        Add-Log "Start failed: $($_.Exception.Message)"
        [System.Windows.Forms.MessageBox]::Show("Start failed: $($_.Exception.Message)", "Japan Antiwar Intel")
    } finally {
        $startButton.Enabled = $true
    }
}

function Stop-System {
    try {
        Add-Log "Stopping Compose services..."
        $wslProject = To-WslPath $ProjectRoot
        Run-WslScript @"
set -e
cd '$wslProject'
docker compose stop
"@ 300 | Out-Null
        if ($script:KeepAliveProcess -and -not $script:KeepAliveProcess.HasExited) {
            try { $script:KeepAliveProcess.Kill() } catch {}
        }
        if ($script:PortBridgeProcess -and -not $script:PortBridgeProcess.HasExited) {
            try { $script:PortBridgeProcess.Kill() } catch {}
        }
        if ($script:InstagramLoginHelperProcess -and -not $script:InstagramLoginHelperProcess.HasExited) {
            try { $script:InstagramLoginHelperProcess.Kill() } catch {}
        }
        Stop-OldJapanPortBridge
        Add-Log "Stopped."
    } catch {
        Add-Log "Stop failed: $($_.Exception.Message)"
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Japan Antiwar Intel Launcher"
$form.Size = New-Object System.Drawing.Size(700, 460)
$form.StartPosition = "CenterScreen"

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = "Start and Open Browser"
$startButton.Size = New-Object System.Drawing.Size(220, 42)
$startButton.Location = New-Object System.Drawing.Point(20, 20)
$startButton.Add_Click({ Start-System })

$openButton = New-Object System.Windows.Forms.Button
$openButton.Text = "Open Console"
$openButton.Size = New-Object System.Drawing.Size(140, 42)
$openButton.Location = New-Object System.Drawing.Point(255, 20)
$openButton.Add_Click({ Start-Process $script:LastUrl })

$stopButton = New-Object System.Windows.Forms.Button
$stopButton.Text = "Stop Services"
$stopButton.Size = New-Object System.Drawing.Size(120, 42)
$stopButton.Location = New-Object System.Drawing.Point(410, 20)
$stopButton.Add_Click({ Stop-System })

$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true
$log.ScrollBars = "Vertical"
$log.ReadOnly = $true
$log.Location = New-Object System.Drawing.Point(20, 80)
$log.Size = New-Object System.Drawing.Size(640, 330)
$log.Font = New-Object System.Drawing.Font("Consolas", 10)

$form.Controls.AddRange(@($startButton, $openButton, $stopButton, $log))
$form.Add_FormClosing({
    if ($script:KeepAliveProcess -and -not $script:KeepAliveProcess.HasExited) {
        $choice = [System.Windows.Forms.MessageBox]::Show(
            "If you close the launcher, WSL may sleep. Stop services now?",
            "Close launcher",
            [System.Windows.Forms.MessageBoxButtons]::YesNoCancel
        )
        if ($choice -eq [System.Windows.Forms.DialogResult]::Cancel) { $_.Cancel = $true; return }
        if ($choice -eq [System.Windows.Forms.DialogResult]::Yes) { Stop-System }
    }
})

Add-Log "Ready. Click Start and Open Browser."
$form.Add_Shown({
    if ($AutoStart) {
        Start-System
    }
})
[void]$form.ShowDialog()
