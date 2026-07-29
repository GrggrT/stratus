# STRATUS local development mode

Use this mode when the WinUI operator console or API code must reflect the current working tree immediately.

> [!WARNING]
> This workflow is **development-only and PAPER-only**. It is not an OS security
> boundary: the host API runs as the interactive Windows user, so arbitrary code
> execution in that process could read other files owned by the same user or
> control Docker Desktop. Never enable LIVE while the host API is running. The
> least-privilege production claim applies only to the containerized API,
> worker, Telegram, and Binance runtime services, which receive neither the
> owner credential nor the Docker socket. Migration, provisioning, and backup
> are deliberate owner-scoped exceptions. Use a
> separate restricted OS identity if host execution ever needs an equivalent
> isolation boundary.

The intended topology is:

- PostgreSQL in Docker, bound to `127.0.0.1:5432`.
- Optional owner-connected backup sidecar in Docker. Starting it early is a
  development convenience only; production cutovers use `-NoBackup` and start
  backup after runtime-boundary and continuity acceptance.
- `stratus-api` running on the host from `.venv` and the current checkout.
- Docker `api` and `worker` stopped, so a stale image cannot serve old endpoints or run the scheduler.

Start it from the repository root:

```powershell
New-Item -ItemType Directory -Force .secrets | Out-Null
$runtimePassword = [Convert]::ToHexString(
  [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).ToLowerInvariant()
Set-Content .secrets\stratus_runtime_password $runtimePassword -NoNewline
.\scripts\Start-StratusDevApi.ps1
```

Create the password file only when it is absent; replacing it rotates the
database-qualified runtime login and requires the provisioner to run again.

The script:

1. Stops Docker `api` and `worker`.
2. Starts Docker `postgres` and, unless `-NoBackup` is set, the owner-connected
   `backup` sidecar. Production migration/continuity drills must use
   `-NoBackup`.
3. Waits for Postgres health.
4. Runs Alembic migrations against `127.0.0.1:5432`.
5. Provisions the database-qualified least-privilege login from the ignored
   password file.
6. Starts host `stratus-api` on `127.0.0.1:8000`; the normal settings path
   ignores generic `DATABASE_URL` and constructs the restricted runtime
   connection. This does not make the host process an OS isolation boundary:
   arbitrary host code may still read owner-owned files.
7. Verifies `/healthz`, `/api/v1/live-summary`, and `/api/v1/live-equity`.

The development script does not create or activate an evidence-continuity
plan, clear a kill switch, authorize PAPER execution, or authorize LIVE.

The script deliberately refuses to kill an unknown process on port `8000`. It only stops an existing host `stratus-api` from this repository. If Docker Desktop or another process owns the port, the script fails closed and prints the owning PID and command line.

Useful options:

```powershell
.\scripts\Start-StratusDevApi.ps1 -Port 8001
.\scripts\Start-StratusDevApi.ps1 -SkipMigrations
.\scripts\Start-StratusDevApi.ps1 -NoBackup
```

Do not use plain `docker compose up -d` for this mode. That starts the compose `api` and `worker` services from the Docker image, which may be older than the working tree and can reintroduce stale endpoints or a running scheduler. Use one of these instead:

```powershell
docker compose up -d postgres backup
docker compose stop api worker
```

To return to an already accepted containerized PAPER release, stop the host API
first. Do not use `--build`: first verify that rendered Compose carries the
image digest bound to the active continuity epoch, keep the kill switch
engaged, and start API/worker under that kill. PAPER resume is a separate,
audited, venue-scoped command after health and continuity checks. Any newly
built image or production database must follow the mandatory cutover procedure
in `EVIDENCE_CONTINUITY_PROTOCOL.md`; this local-development shortcut is not a
release procedure.

```powershell
$ownerPid = (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess
$owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid"
if ($owner.CommandLine -like "*\.venv\Scripts\stratus-api.exe*") {
    Stop-Process -Id $ownerPid
} else {
    throw "Port 8000 is not owned by the host stratus-api: $($owner.CommandLine)"
}
docker compose up -d api worker
```

Logs from the host API are written to `api.out.log` and `api.err.log`; `*.log` files are gitignored.
