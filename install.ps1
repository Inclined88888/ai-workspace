# AI Workspace: one-command install for Windows 10/11 (macOS and Linux: install.sh).
#
#   From the downloaded folder (zip or clone), in PowerShell:
#       powershell -ExecutionPolicy Bypass -File .\install.ps1
#   Straight from GitHub:
#       powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.ps1 | iex"
#
# Then start everything with:   ai-workspace      (or Start menu > AI Workspace)
#
# Installs for your user only, no admin prompt, no Docker, no WSL:
#   ai-workspace    the app (native app when a published release has one, else its own Edge/Chrome app window)
#   rooks           Rooks Code, the terminal coding agent
#   rooks-browser   Rooks Browser, the AI browser agent (uses Edge, which every Windows has, or Chrome)
# Python 3.12 is fetched by uv (astral.sh) into your user folder.
#
# Options (env vars, because `irm | iex` cannot take arguments):
#   AI_WORKSPACE_NO_START=1   don't open the app at the end
#   AI_WORKSPACE_NATIVE=auto|yes|no   native installer from GitHub releases (default auto)
#   AI_WORKSPACE_REF (branch/tag to download, default main)   AI_WORKSPACE_REPO (owner/repo)
# Run it again to upgrade. Your chats, keys and settings are kept. Uninstall: QUICKSTART.md.

& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

  $repo = if ($env:AI_WORKSPACE_REPO) { $env:AI_WORKSPACE_REPO } else { 'Inclined88888/ai-workspace' }
  $ref = if ($env:AI_WORKSPACE_REF) { $env:AI_WORKSPACE_REF } else { 'main' }
  $bin = if ($env:AI_WORKSPACE_BIN) { $env:AI_WORKSPACE_BIN } else { Join-Path $env:USERPROFILE '.local\bin' }

  Write-Host 'AI Workspace installer' -ForegroundColor White

  # 1. The source: this folder when run from a zip or clone, else a download of $ref (no git needed).
  $src = $null
  if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'workbench\backend\requirements.txt')) -and (Test-Path (Join-Path $PSScriptRoot 'rooks-code'))) {
    $src = $PSScriptRoot
  } else {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("ai-workspace-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Write-Host "  Downloading $repo ($ref)..."
    $zip = Join-Path $tmp 'src.zip'
    Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/$repo/zip/$ref" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tmp
    $src = (Get-ChildItem -Directory $tmp | Select-Object -First 1).FullName
  }
  Write-Host "  Source: $src"

  # 2. uv: one small program that brings its own Python.
  New-Item -ItemType Directory -Force -Path $bin | Out-Null
  $env:AI_WORKSPACE_ORIG_PATH = [Environment]::GetEnvironmentVariable('Path', 'User')  # the helper adds $bin if missing
  $env:Path = "$bin;$env:Path"
  $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
  if (-not $uv) {
    Write-Host "  Installing uv (Python manager, astral.sh) into $bin..."
    $env:UV_INSTALL_DIR = $bin; $env:UV_NO_MODIFY_PATH = '1'; $env:INSTALLER_NO_MODIFY_PATH = '1'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
    $uv = Join-Path $bin 'uv.exe'
    if (-not (Test-Path $uv)) { throw 'Could not install uv. See https://docs.astral.sh/uv/getting-started/installation/' }
  }
  $env:UV = $uv
  $env:PYTHONUTF8 = '1'

  # 3. Everything else is the same on every OS: desktop\launcher\install_helper.py.
  $native = if ($env:AI_WORKSPACE_NATIVE) { $env:AI_WORKSPACE_NATIVE } else { 'auto' }
  & $uv run --quiet --no-project --python 3.12 (Join-Path $src 'desktop\launcher\install_helper.py') --src $src --bin $bin --native $native
  if ($LASTEXITCODE -ne 0) { throw "Install failed (exit $LASTEXITCODE). Scroll up for the reason." }

  if ($env:AI_WORKSPACE_NO_START -ne '1') {
    Write-Host "`nStarting AI Workspace..."
    & (Join-Path $bin 'ai-workspace.cmd')
  }
}
