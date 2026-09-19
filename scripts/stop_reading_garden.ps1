$projectDir = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectDir '.runserver.pid'

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host '阅见网站当前没有由快捷方式启动的后台服务。'
    exit 0
}

$serverPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
if ($process -and $process.CommandLine -match 'manage\.py\s+runserver') {
    Stop-Process -Id $serverPid -Force
    Write-Host '阅见网站已停止。'
} else {
    Write-Host '没有找到对应的网站进程。'
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
