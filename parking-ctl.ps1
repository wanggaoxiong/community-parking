param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status", "health", "logs", "foreground", "install-task", "uninstall-task", "docker-up", "docker-down", "docker-restart", "docker-logs")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ==============================================================================
# 邻里停 (Community Parking) Windows 一键运维脚本 (支持 x86_64 与 ARM64)
# 用法: .\parking-ctl.ps1 {start|stop|restart|status|health|logs|foreground|install-task|uninstall-task}
# ==============================================================================

$AppDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPy   = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    $VenvPy = "python"
}

$BindHost   = if ($env:PARKING_HOST) { $env:PARKING_HOST } else { "127.0.0.1" }
$Port       = if ($env:PARKING_PORT) { [int]$env:PARKING_PORT } else { 8790 }
$MobileHost = if ($env:PARKING_MOBILE_HOST) { $env:PARKING_MOBILE_HOST } else { "0.0.0.0" }
$MobilePort = if ($env:PARKING_MOBILE_PORT) { [int]$env:PARKING_MOBILE_PORT } else { 18790 }
$VoiceHost  = if ($env:PARKING_VOICE_HOST) { $env:PARKING_VOICE_HOST } else { "0.0.0.0" }
$VoicePort  = if ($env:PARKING_VOICE_PORT) { [int]$env:PARKING_VOICE_PORT } else { 18791 }
$FtpPort    = if ($env:PARKING_FTP_PORT) { [int]$env:PARKING_FTP_PORT } else { 2121 }
$ProbeHost  = if ($BindHost -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $BindHost }
$MobileProbeHost = if ($MobileHost -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $MobileHost }
$VoiceProbeHost  = if ($VoiceHost -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $VoiceHost }

$DataDir  = Join-Path $AppDir "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
}
$PidFile  = Join-Path $DataDir "parking-$Port.pid"
$LogFile  = Join-Path $DataDir "parking-$Port.log"
$ErrFile  = Join-Path $DataDir "parking-$Port.err.log"
$TaskName = "CommunityParking-$Port"

function Rotate-LogIfNeeded {
    foreach ($f in @($LogFile, $ErrFile)) {
        if (Test-Path $f) {
            $lines = Get-Content -Path $f -ErrorAction SilentlyContinue
            if ($lines -and $lines.Count -gt 2000) {
                $lines | Select-Object -Last 1000 | Set-Content -Path $f -Encoding UTF8
            }
        }
    }
}

function Get-RunningPid {
    if (Test-Path $PidFile) {
        $rawPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Out-String).Trim()
        if ($rawPid -match '^\d+$') {
            $proc = Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue
            if ($proc) { return [int]$rawPid }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn -and $conn.OwningProcess -gt 0) {
        Set-Content -Path $PidFile -Value $conn.OwningProcess -NoNewline
        return [int]$conn.OwningProcess
    }
    return $null
}

function Invoke-LocalGet([string]$Path, [string]$TargetHost = $ProbeHost, [int]$TargetPort = $Port) {
    $uri = "http://${TargetHost}:${TargetPort}${Path}"
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(3)
    try {
        return $client.GetStringAsync($uri).GetAwaiter().GetResult()
    } finally {
        $client.Dispose()
    }
}

function Wait-Healthy {
    for ($i = 1; $i -le 25; $i++) {
        try {
            $null = Invoke-LocalGet "/api/health" $ProbeHost $Port
            $null = Invoke-LocalGet "/api/health" $MobileProbeHost $MobilePort
            $null = Invoke-LocalGet "/api/health" $VoiceProbeHost $VoicePort
            return $true
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    return $false
}

function Show-Health {
    try {
        $mobOk = $false
        $voiceOk = $false
        try {
            $null = Invoke-LocalGet "/api/health" $MobileProbeHost $MobilePort
            $mobOk = $true
        } catch {}
        try {
            $null = Invoke-LocalGet "/api/health" $VoiceProbeHost $VoicePort
            $voiceOk = $true
        } catch {}
        $mobLabel = if ($mobOk) { "在线(:$MobilePort)" } else { "未响应(:$MobilePort)" }
        $voiceLabel = if ($voiceOk) { "在线(:$VoicePort)" } else { "未响应(:$VoicePort)" }
        $json = Invoke-LocalGet "/api/overview"
        $d = $json | ConvertFrom-Json
        $ps = $d.privacy_stats
        $usedKb = if ($null -ne $ps.cache_used_kb) { [double]$ps.cache_used_kb } else { 0.0 }
        $budgetMb = if ($null -ne $ps.cache_budget_mb) { [double]$ps.cache_budget_mb } else { 64.0 }
        $cacheText = if ($usedKb -ge 1024) { "{0:N2} MB" -f ($usedKb / 1024.0) } else { "{0:N1} KB" -f $usedKb }
        Write-Host ("   📊 概览: 总车位 {0} | 净余空位 {1} | 微信看图 {2} | Siri语音 {3} | 落盘 {4} | 瞬时销毁 {5} 帧 | 热快取 {6}/{7:N0}MB" -f `
            $d.total_capacity, $d.total_effective_free, $mobLabel, $voiceLabel, $ps.raw_images_written_to_disk, $ps.frames_processed_in_ram, $cacheText, $budgetMb)
    } catch {
        Write-Host "⚠️  健康检查未响应: http://${ProbeHost}:${Port}/api/health" -ForegroundColor Yellow
        exit 1
    }
}

function Start-ParkingService {
    $runningPid = Get-RunningPid
    if ($runningPid) {
        Write-Host "✅ 服务已在运行 (PID: $runningPid) -> 控制台 :${Port} | 微信看图 :${MobilePort} | Siri语音 :${VoicePort}" -ForegroundColor Green
        Show-Health
        return
    }
    Rotate-LogIfNeeded
    Write-Host "🚀 正在启动邻里停服务 (控制台 ${BindHost}:${Port}, 微信看图 ${MobileHost}:${MobilePort}, Siri语音 ${VoiceHost}:${VoicePort}, FTP:${FtpPort})..."
    $runPy = Join-Path $AppDir "run.py"
    $proc = Start-Process -FilePath $VenvPy `
        -ArgumentList @($runPy, "--host", $BindHost, "--port", "$Port", "--mobile-host", $MobileHost, "--mobile-port", "$MobilePort", "--voice-host", $VoiceHost, "--voice-port", "$VoicePort", "--ftp-port", "$FtpPort") `
        -WorkingDirectory $AppDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError $ErrFile `
        -PassThru
    Set-Content -Path $PidFile -Value $proc.Id -NoNewline

    if (Wait-Healthy) {
        Write-Host "✅ 启动成功! PID=$($proc.Id)" -ForegroundColor Green
        Write-Host "   🖥️  管理控制台 : http://${ProbeHost}:${Port}"
        Write-Host "   📱 微信看图端 : http://${MobileProbeHost}:${MobilePort}"
        Write-Host "   🎙️  Siri语音端 : http://${VoiceProbeHost}:${VoicePort}"
        Write-Host "   📡 内存 FTP   : ftp://${ProbeHost}:${FtpPort}"
        Write-Host "   📜 日志文件   : $LogFile"
    } else {
        Write-Host "❌ 启动超时，最近错误日志如下：" -ForegroundColor Red
        if (Test-Path $ErrFile) { Get-Content $ErrFile -Tail 30 }
        exit 1
    }
}

function Stop-ParkingService {
    $runningPid = Get-RunningPid
    if (-not $runningPid) {
        Write-Host "ℹ️  服务当前未运行 (端口 $Port 空闲)"
        return
    }
    Write-Host "🛑 正在停止服务 (PID: $runningPid)..."
    Stop-Process -Id $runningPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "✅ 服务已停止" -ForegroundColor Green
}

switch ($Action) {
    "start" { Start-ParkingService }
    "stop"  { Stop-ParkingService }
    "restart" {
        Stop-ParkingService
        Start-ParkingService
    }
    "status" {
        $runningPid = Get-RunningPid
        if ($runningPid) {
            $proc = Get-Process -Id $runningPid -ErrorAction SilentlyContinue
            $rssMb = if ($proc) { [math]::Round($proc.WorkingSet64 / 1MB, 1) } else { 0 }
            Write-Host "🟢 运行中 | PID: $runningPid | 物理内存(WorkingSet): $rssMb MB | 地址: http://${BindHost}:${Port}" -ForegroundColor Green
            Show-Health
        } else {
            Write-Host "⚪ 未运行 | 端口 $Port 未监听" -ForegroundColor Yellow
            exit 1
        }
    }
    "health" { Show-Health }
    "logs" {
        if (-not (Test-Path $LogFile)) { New-Item -ItemType File -Path $LogFile | Out-Null }
        Write-Host "📜 正在跟随日志 $LogFile (按 Ctrl+C 退出)..."
        Get-Content -Path $LogFile -Tail 50 -Wait
    }
    "foreground" {
        Rotate-LogIfNeeded
        Set-Content -Path $PidFile -Value $PID -NoNewline
        & $VenvPy (Join-Path $AppDir "run.py") --host $BindHost --port $Port --mobile-host $MobileHost --mobile-port $MobilePort --voice-host $VoiceHost --voice-port $VoicePort --ftp-port $FtpPort
    }
    "install-task" {
        $runPy = Join-Path $AppDir "run.py"
        $actionObj = New-ScheduledTaskAction -Execute $VenvPy -Argument "`"$runPy`" --host $BindHost --port $Port --mobile-host $MobileHost --mobile-port $MobilePort --voice-host $VoiceHost --voice-port $VoicePort --ftp-port $FtpPort" -WorkingDirectory $AppDir
        $triggerObj = New-ScheduledTaskTrigger -AtLogOn
        $settingsObj = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $TaskName -Action $actionObj -Trigger $triggerObj -Settings $settingsObj -Force | Out-Null
        Write-Host "✅ 已注册 Windows 登录自启与异常重启计划任务 ($TaskName)" -ForegroundColor Green
    }
    "uninstall-task" {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "✅ 已移除 Windows 自启计划任务 ($TaskName)" -ForegroundColor Green
    }
    "docker-up" {
        Push-Location $AppDir
        try {
            if ($env:CLOUDFLARE_TUNNEL_TOKEN) {
                docker compose --profile tunnel up -d --build
            } else {
                docker compose up -d --build
            }
        } finally { Pop-Location }
    }
    "docker-down" {
        Push-Location $AppDir
        try { docker compose --profile tunnel down } finally { Pop-Location }
    }
    "docker-restart" {
        Push-Location $AppDir
        try { docker compose restart } finally { Pop-Location }
    }
    "docker-logs" {
        Push-Location $AppDir
        try { docker compose logs -f --tail=80 } finally { Pop-Location }
    }
}
