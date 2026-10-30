# 打包成 exe（PyInstaller onedir，双击 PainterLive.exe 即可打开面板）
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$py = 'D:\AI\ComfyUI-aki-v3.2\python\python.exe'
if (-not (Test-Path $py)) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if (-not $cmd) { throw '找不到 Python' }
  $py = $cmd.Source
}

# 窗口进程会占用 dist 里的 edge-profile，先关掉
Get-Process PainterLive -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
  Where-Object { $_.CommandLine -like '*PainterLive*edge-profile*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host '[build] 安装打包依赖…'
& $py -m pip install -U aiohttp pywebview pyinstaller

Write-Host '[build] 开始打包…'
& $py -m PyInstaller --noconfirm --clean --onedir --noconsole `
  --name PainterLive `
  --add-data "web;web" `
  --add-data "overlay;overlay" `
  --add-data "templates;templates" `
  --add-data "prompts;prompts" `
  --add-data "docs;docs" `
  --add-data "tools;tools" `
  --add-data "_stub;_stub" `
  launcher.py

# Live2D 模型放在 exe 同级（RUNTIME/models），不跟着 _internal 走
if (Test-Path "$PSScriptRoot\dist\PainterLive\models") { Remove-Item "$PSScriptRoot\dist\PainterLive\models" -Recurse -Force }
Copy-Item "$PSScriptRoot\models" "$PSScriptRoot\dist\PainterLive\models" -Recurse -Force

Write-Host ''
Write-Host "[build] 完成：$PSScriptRoot\dist\PainterLive\PainterLive.exe"
Write-Host '[build] 首次运行会在 exe 同目录生成 data/ images/，配置在 data/config.json'