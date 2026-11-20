# Developer tool: copies the plugin to a handheld over SSH and restarts Decky.
#   npm run deploy            (code only: fast)
#   npm run deploy -- -Full   (also bundled Python libraries and binaries)
# Your device goes in scripts/deploy.local.ps1 (not committed), e.g.:
#   $Target = "deck@192.168.1.50"; $Key = "$env:USERPROFILE\.ssh\id_ed25519"
param([switch]$Full, [string]$Target, [string]$Key)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$local = Join-Path $PSScriptRoot "deploy.local.ps1"
if (Test-Path $local) {
  $given = @{ Target = $Target; Key = $Key }
  . $local
  if ($given.Target) { $Target = $given.Target }
  if ($given.Key) { $Key = $given.Key }
}
if (-not $Target) { throw "Set your device in scripts/deploy.local.ps1 (see the top of this file) or pass -Target deck@<ip>." }
$key = if ($Key) { $Key } else { Join-Path $env:USERPROFILE ".ssh\id_ed25519" }
$dest = "/home/deck/homebrew/plugins/CuivarPulse"
$ssh = @("-i", $key, "-o", "BatchMode=yes")

Push-Location $root
try {
  if (-not (Test-Path "dist\index.js")) { throw "Run 'npm run build' first." }
  # plugin.json is not copied: Decky makes the plugin folder and plugin.json
  # root-owned on every start (it controls the plugin's permissions, so a
  # plugin must not be able to edit it). Changing it needs a manual sudo copy.
  $items = @("package.json", "main.py", "dist", "cuivar")
  if ($Full) { $items += @("py_modules", "bin") }

  ssh @ssh $Target "mkdir -p $dest" | Out-Null
  scp @ssh -q -r @items "${Target}:$dest/"
  if ($LASTEXITCODE -ne 0) { throw "Copy failed." }
  ssh @ssh $Target "chmod +x $dest/bin/* 2>/dev/null; sudo -n /usr/bin/systemctl restart plugin_loader"
  if ($LASTEXITCODE -ne 0) { throw "Decky restart failed." }
  Write-Output "Deployed $($items -join ', ') and restarted Decky."
} finally {
  Pop-Location
}
