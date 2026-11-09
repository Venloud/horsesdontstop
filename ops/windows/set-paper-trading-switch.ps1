# EMERGENCY STOP / ARM for autonomous PAPER trading (owner credential).
#   -Stop : kill switch ON, submissions OFF  (takes effect on the next DB check;
#           reservation, policy authorization and dispatch all refuse at once)
#   -Arm  : kill switch OFF, submissions ON  (autonomous PAPER trading allowed)
# Existing positions keep their broker-side GTC protective stops either way.
param([switch]$Stop, [switch]$Arm)
$ErrorActionPreference = 'Stop'
if ($Stop -eq $Arm) { throw 'Specify exactly one of -Stop or -Arm.' }
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$secure = (Get-Content -LiteralPath (Join-Path $folder 'neon-owner-dsn.dpapi') -Raw).Trim() | ConvertTo-SecureString
$env:NEON_OWNER_DSN = [pscredential]::new('x', $secure).GetNetworkCredential().Password
try {
    $kill = if ($Stop) { 'true' } else { 'false' }
    $enabled = if ($Stop) { 'false' } else { 'true' }
    $code = @"
import os, psycopg
with psycopg.connect(os.environ['NEON_OWNER_DSN'], sslmode='require', connect_timeout=10) as c:
    c.execute("UPDATE public.paper_control SET kill_switch_active=$kill, submissions_enabled=$enabled, updated_at=now() WHERE singleton")
    c.execute("INSERT INTO public.paper_audit_events(client_order_id,kind,summary) VALUES (NULL,'control_changed','kill=$kill submissions=$enabled by owner switch')")
    print('PAPER controls now:', c.execute('SELECT kill_switch_active, submissions_enabled FROM public.paper_control').fetchone())
"@
    & (Join-Path $repo '.venv\Scripts\python.exe') -c $code
    exit $LASTEXITCODE
} finally { Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue }
