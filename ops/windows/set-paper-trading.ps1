# EMERGENCY STOP / ARM for autonomous PAPER trading, via the restricted
# operator login (it can ONLY call paper_engage_kill_switch and
# paper_arm_autonomous_trading; it has no table or schema privileges).
#   -Stop : kill switch ON, submissions OFF. Policy authorization, reservation
#           and dispatch refuse immediately in Neon. Open positions keep their
#           broker-side GTC protective stops.
#   -Arm  : kill switch OFF, submissions ON (requires the Neon policy enabled).
#   -Status : show current controls and policy.
param([switch]$Stop, [switch]$Arm, [switch]$Status, [string]$Reason = 'operator')
$ErrorActionPreference = 'Stop'
if ((@($Stop, $Arm, $Status) | Where-Object { $_ }).Count -ne 1) { throw 'Specify exactly one of -Stop, -Arm or -Status.' }
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$secure = (Get-Content -LiteralPath (Join-Path $folder 'neon-operator-dsn.dpapi') -Raw).Trim() | ConvertTo-SecureString
$env:PAPER_OPERATOR_DSN = [pscredential]::new('x', $secure).GetNetworkCredential().Password
$env:PAPER_OPERATOR_REASON = ($Reason -replace '[^A-Za-z0-9 _:.,-]', '')
$env:PAPER_OPERATOR_ACTION = $(if ($Stop) { 'stop' } elseif ($Arm) { 'arm' } else { 'status' })
try {
    Push-Location $repo
    try { & (Join-Path $repo '.venv\Scripts\python.exe') -m bot.paper_operator; exit $LASTEXITCODE }
    finally { Pop-Location }
} finally {
    foreach ($n in 'PAPER_OPERATOR_DSN','PAPER_OPERATOR_REASON','PAPER_OPERATOR_ACTION') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}
