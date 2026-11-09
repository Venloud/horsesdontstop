# One-time, current-user DPAPI storage of the Neon OWNER DSN used ONLY by the
# controlled deployer (deploy-paper-migrations.ps1) and the emergency switch
# (set-paper-trading-switch.ps1). No scheduled trading task reads this file.
# NEVER paste the DSN into a chat or onto a command line.
param([switch]$Replace)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$path = Join-Path $folder 'neon-owner-dsn.dpapi'
if ((Test-Path -LiteralPath $path) -and -not $Replace) {
    throw 'Owner DPAPI credential already exists. Re-run with -Replace to rotate it.'
}
Write-Host 'Paste the neondb_owner connection string for project misty-shadow-43891652,'
Write-Host 'branch production, database neondb (Neon console > Connect). Input is hidden.'
$secret = Read-Host -AsSecureString 'Neon owner DSN'
$plain = [pscredential]::new('x', $secret).GetNetworkCredential().Password
if ($plain -notmatch '^postgres(ql)?://neondb_owner:[^@]+@ep-summer-salad-b5btnpj6[^/]*/neondb') {
    throw 'That is not the neondb_owner DSN for the production endpoint/database; nothing saved.'
}
Remove-Variable plain
$secret | ConvertFrom-SecureString | Set-Content -LiteralPath $path -NoNewline
Write-Host 'Encrypted for the current Windows user. Credential contents not printed.'
