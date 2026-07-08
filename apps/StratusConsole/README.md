# STRATUS Operator Console

Local Windows operator UI for STRATUS. The Phase C build combines bounded `/api/v1/*`
monitoring with authenticated, audited commands that are executed asynchronously by the
worker queue. It never connects directly to PostgreSQL or starts Python jobs itself.

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
same value. Open **Settings**, choose **API key**, and save it in Windows
Credential Locker. As a development fallback, the console also reads
`STRATUS_OPERATOR_API_KEY` from its environment:

```powershell
$env:STRATUS_OPERATOR_API_KEY = "change-me"
dotnet run .\StratusConsole.csproj -c Debug -p:Platform=x64 --no-build
```

While the API listens on loopback only, the key is optional.
