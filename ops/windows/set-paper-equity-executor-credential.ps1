# One-time current-user DPAPI setup. NEVER paste the DSN into a chat or command line.
$ErrorActionPreference = "Stop"
$folder = Join-Path $env:LOCALAPPDATA "horsesdontstop"
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$path = Join-Path $folder "neon-executor-dsn.dpapi"
if (Test-Path -LiteralPath $path) {
    throw "Executor DPAPI credential already exists. Refusing silent overwrite."
}
Write-Host "Enter restricted PAPER_EXECUTOR_DSN from Neon. Input is hidden."
Write-Host "Never use admin/owner/monitor/heartbeat DSNs."
$secret = Read-Host -AsSecureString "Restricted PAPER executor DSN"
$secret | ConvertFrom-SecureString | Set-Content -LiteralPath $path -NoNewline
Write-Host "Encrypted for the current Windows user. Credential contents not printed."
