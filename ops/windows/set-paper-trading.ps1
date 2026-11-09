# Operator controls for autonomous PAPER trading.
#   -Stop   : kill switch ON, submissions OFF. Uses the bot's own executor
#             login (every bot role may PAUSE). Open positions keep their
#             broker-side GTC protective stops.
#   -Arm    : kill switch OFF, submissions ON. Requires the human operator
#             password, typed each time (hidden). It is NEVER stored on this
#             host, so no unattended service can arm trading.
#   -Status : read controls and policy with the read-only monitor login.
param([switch]$Stop, [switch]$Arm, [switch]$Status, [string]$Reason = 'operator')
$ErrorActionPreference = 'Stop'
if ((@($Stop, $Arm, $Status) | Where-Object { $_ }).Count -ne 1) { throw 'Specify exactly one of -Stop, -Arm or -Status.' }
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dbHost = 'ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
try {
    if ($Arm) {
        $secure = Read-Host -AsSecureString 'hds_paper_operator_login password (not stored)'
        $pw = [Uri]::EscapeDataString([pscredential]::new('x', $secure).GetNetworkCredential().Password)
        $env:PAPER_OPERATOR_DSN = "postgresql://hds_paper_operator_login:${pw}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
        Remove-Variable pw
        $env:PAPER_OPERATOR_ACTION = 'arm'
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
    foreach ($n in 'PAPER_OPERATOR_DSN','PAPER_OPERATOR_REASON','PAPER_OPERATOR_ACTION') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}
