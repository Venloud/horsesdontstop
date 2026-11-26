# PRIVILEGED, INTERACTIVE deployment. Run by the owner in their own terminal.
# The neondb_owner DSN is typed (hidden) for this run only: it is never
# written to disk, never stored for the bot and never passed on a command line.
#   -Plan              list pending migrations only
# Alternative to applying the reviewed files through the Neon integration.
param([switch]$Plan)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
Write-Host 'Paste the neondb_owner DSN (production branch, database neondb). Input is hidden; it is not saved.'
$secure = Read-Host -AsSecureString 'Neon owner DSN'
$env:NEON_OWNER_DSN = [pscredential]::new('x', $secure).GetNetworkCredential().Password
try {
    if ($env:NEON_OWNER_DSN -notmatch '^postgres(ql)?://neondb_owner:[^@]+@ep-summer-salad-b5btnpj6[^/]*/neondb') {
        throw 'Not the neondb_owner DSN for the production endpoint/database.'
    }
    Push-Location $repo
    try {
        $extra = @()
        if ($Plan) { $extra += '--plan' }
        & $python -m bot.paper_migrations @extra
        exit $LASTEXITCODE
    } finally { Pop-Location }
} finally { Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue }
