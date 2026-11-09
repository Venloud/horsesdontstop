# Operator controls for autonomous PAPER trading.
#   -Stop        : kill switch ON, submissions OFF. Uses the bot's own executor
#                  login (every bot role may PAUSE). Open positions keep their
#                  broker-side GTC protective stops.
#   -Arm         : kill switch OFF, submissions ON. Requires the human operator
#                  password, typed each time (hidden), NEVER stored on this host.
#   -Status      : read controls and policy with the read-only monitor login.
#   -Verify      : log in as the operator and prove it can only arm/pause.
#                  Changes nothing.
#   -NewLoginSql : choose an operator password (hidden, typed twice) and print
#                  CREATE ROLE SQL containing only its SCRAM-SHA-256 verifier,
#                  for the owner to run through the Neon integration. Nothing
#                  is saved and no database connection is made.
param([switch]$Stop, [switch]$Arm, [switch]$Status, [switch]$Verify,
      [switch]$NewLoginSql, [string]$Reason = 'operator')
$ErrorActionPreference = 'Stop'
if ((@($Stop, $Arm, $Status, $Verify, $NewLoginSql) | Where-Object { $_ }).Count -ne 1) {
    throw 'Specify exactly one of -Stop, -Arm, -Status, -Verify or -NewLoginSql.'
}
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dbHost = 'ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
function Read-Hidden([string]$prompt) {
    $s = Read-Host -AsSecureString $prompt
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$names = 'PAPER_OPERATOR_DSN','PAPER_OPERATOR_REASON','PAPER_OPERATOR_ACTION','PAPER_OPERATOR_NEW_PASSWORD'
try {
    if ($NewLoginSql) {
        $first = Read-Hidden 'New hds_paper_operator_login password (min 20 chars, not stored)'
        $second = Read-Hidden 'Repeat password'
        if ($first -cne $second) { throw 'Passwords do not match.' }
        $env:PAPER_OPERATOR_NEW_PASSWORD = $first
        Remove-Variable first, second
        $env:PAPER_OPERATOR_ACTION = 'verifier'
    } elseif ($Arm -or $Verify) {
        $pw = [Uri]::EscapeDataString((Read-Hidden 'hds_paper_operator_login password (not stored)'))
        $env:PAPER_OPERATOR_DSN = "postgresql://hds_paper_operator_login:${pw}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
        Remove-Variable pw
        $env:PAPER_OPERATOR_ACTION = $(if ($Arm) { 'arm' } else { 'verify' })
    } elseif ($Stop) {
        $env:PAPER_OPERATOR_DSN = Read-Dpapi 'neon-executor-dsn.dpapi'
        $env:PAPER_OPERATOR_ACTION = 'stop'
    } else {
        $pw = [Uri]::EscapeDataString((Read-Dpapi 'neon-monitor.dpapi'))
        $env:PAPER_OPERATOR_DSN = "postgresql://hds_paper_win_monitor:${pw}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
        Remove-Variable pw
        $env:PAPER_OPERATOR_ACTION = 'status'
    }
    $env:PAPER_OPERATOR_REASON = ($Reason -replace '[^A-Za-z0-9 _:.,-]', '')
    Push-Location $repo
    try { & (Join-Path $repo '.venv\Scripts\python.exe') -m bot.paper_operator; exit $LASTEXITCODE }
    finally { Pop-Location }
} finally {
    foreach ($n in $names) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}
