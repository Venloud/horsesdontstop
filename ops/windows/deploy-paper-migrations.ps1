# PRIVILEGED, INTERACTIVE deployment. Run by the owner in their own terminal.
# The neondb_owner DSN is typed (hidden) for this run only: it is never
# written to disk, never stored for the bot and never passed on a command line.
#   -Plan              list pending migrations only
#   -ProvisionOperator create the restricted operator login (pause/arm only)
#                      and save its DSN DPAPI-encrypted for set-paper-trading.ps1
param([switch]$Plan, [switch]$ProvisionOperator)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
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
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        if ($ProvisionOperator -and -not $Plan) {
            $target = Join-Path $folder 'neon-operator-dsn.dpapi'
            $dsn = & $python -m bot.paper_migrations --provision-operator
            if ($LASTEXITCODE -ne 0 -or -not $dsn) { throw 'Operator provisioning failed.' }
            ($dsn | ConvertTo-SecureString -AsPlainText -Force) | ConvertFrom-SecureString |
                Set-Content -LiteralPath $target -NoNewline
            Remove-Variable dsn
            Write-Host 'Restricted operator login provisioned; DSN saved DPAPI-encrypted (pause/arm only).'
        }
        exit 0
    } finally { Pop-Location }
} finally { Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue }
