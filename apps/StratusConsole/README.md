# STRATUS Operator Console

Local Windows monitoring UI for STRATUS. The current Phase B build is deliberately
read-only and calls the health routes plus bounded `/api/v1/*` monitoring resources for
overview, markets, signals, positions, calibration, and job runs.

## Build and run

Prerequisites: .NET SDK 10, Windows App SDK tooling, and the STRATUS API listening on
loopback port 8000.

```powershell
dotnet build .\StratusConsole.csproj -c Debug -p:Platform=x64
dotnet run .\StratusConsole.csproj -c Debug -p:Platform=x64 --no-build
```

The official packaged WinUI template registers a debug package identity during `dotnet run`.
The application does not connect to PostgreSQL and cannot execute shell commands.

## Authentication

If the API is started with `OPERATOR_API_KEY` set, the console must send the
same value. It reads `STRATUS_OPERATOR_API_KEY` from its environment and sends
it as the `X-API-Key` header on every `/api/v1/*` request:

```powershell
$env:STRATUS_OPERATOR_API_KEY = "change-me"
dotnet run .\StratusConsole.csproj -c Debug -p:Platform=x64 --no-build
```

While the API listens on loopback only, the key is optional.
