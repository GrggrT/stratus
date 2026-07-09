# STRATUS local development mode

Use this mode when the WinUI operator console or API code must reflect the current working tree immediately.

The intended topology is:

- PostgreSQL in Docker, bound to `127.0.0.1:5432`.
- Optional backup sidecar in Docker.
- `stratus-api` running on the host from `.venv` and the current checkout.
- Docker `api` and `worker` stopped, so a stale image cannot serve old endpoints or run the scheduler.

Start it from the repository root:

```powershell
.\scripts\Start-StratusDevApi.ps1
```

The script:

1. Stops Docker `api` and `worker`.
2. Starts Docker `postgres` and `backup`.
3. Waits for Postgres health.
4. Runs Alembic migrations against `127.0.0.1:5432`.
5. Starts host `stratus-api` on `127.0.0.1:8000`.
6. Verifies `/healthz`, `/api/v1/live-summary`, and `/api/v1/live-equity`.

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

To return to the containerized paper runtime, stop the host API first, then start the compose services explicitly:

```powershell
$ownerPid = (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess
$owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid"
if ($owner.CommandLine -like "*\.venv\Scripts\stratus-api.exe*") {
    Stop-Process -Id $ownerPid
} else {
    throw "Port 8000 is not owned by the host stratus-api: $($owner.CommandLine)"
}
docker compose up -d --build api worker
```

Logs from the host API are written to `api.out.log` and `api.err.log`; `*.log` files are gitignored.
