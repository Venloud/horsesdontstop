# Controlled deployment: apply pending ledgered SQL migrations as neondb_owner.
param([switch]$Plan)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$path = Join-Path $folder 'neon-owner-dsn.dpapi'
if (-not (Test-Path -LiteralPath $path)) { throw 'Run set-neon-owner-credential.ps1 once first.' }
try {
    $secure = (Get-Content -LiteralPath $path -Raw).Trim() | ConvertTo-SecureString
    $env:NEON_OWNER_DSN = [pscredential]::new('x', $secure).GetNetworkCredential().Password
    Push-Location $repo
    try {
        $extra = @(); if ($Plan) { $extra = @('--plan') }
        & (Join-Path $repo '.venv\Scripts\python.exe') -m bot.paper_migrations @extra
        exit $LASTEXITCODE
    } finally { Pop-Location }
} finally { Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue }
