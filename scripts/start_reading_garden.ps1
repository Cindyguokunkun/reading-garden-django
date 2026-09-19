$ErrorActionPreference = 'Stop'

$projectDir = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
$managePath = Join-Path $projectDir 'manage.py'
$pidPath = Join-Path $projectDir '.runserver.pid'
$siteUrl = 'http://127.0.0.1:8000/'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('网站尚未安装，请先双击 setup.bat。', '阅见') | Out-Null
    exit 1
}

$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @($managePath, 'runserver', '0.0.0.0:8000', '--noreload') `
        -WorkingDirectory $projectDir `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
}

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri $siteUrl -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -lt 500) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $ready) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('网站启动失败，请检查项目配置。', '阅见') | Out-Null
    exit 1
}

Start-Process $siteUrl
