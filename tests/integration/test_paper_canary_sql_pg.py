from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)


def _powershell_executable() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell.exe")
    if executable is None:
        pytest.skip("PowerShell is required to render the PAPER canary SQL")
    return executable


def _render_canary_sql(kind: str) -> str:
    collector = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "Invoke-StratusPaperCanary.ps1"
    )
    command = r"""
$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath $env:STRATUS_CANARY_COLLECTOR -Raw
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
if ($errors.Count -ne 0) {
    throw 'Collector parse failed.'
}
foreach ($name in @(
    'ConvertTo-DatabaseAddressArraySql',
    'Get-DatabaseSessionSql',
    'Get-GuardSql',
    'Get-FullSql'
)) {
    $functions = @(
        $ast.FindAll(
            {
                param($node)
                $node -is
                    [Management.Automation.Language.FunctionDefinitionAst] -and
                    $node.Name -eq $name
            },
            $true
        )
    )
    if ($functions.Count -ne 1) {
        throw "Expected exactly one $name definition."
    }
    Invoke-Expression $functions[0].Extent.Text
}
$ExpectedResumeRequestId = 'release-paper-sql-contract'
$stopRequestId = 'paper-stop-sql-contract'
$leaseId = 'paper-lease-sql-contract'
$leaseReleaseRequestId = 'paper-release-sql-contract'
$boundary = '2026-07-29T00:00:00Z'
$databaseSessionBoundary = [ordered]@{
    api_addresses = @('172.20.0.10')
    worker_addresses = @('172.20.0.11')
    safety_control_addresses = @('172.21.0.12')
}
if ($env:STRATUS_CANARY_SQL_KIND -eq 'guard') {
    Get-GuardSql -CommandBoundaryTime $boundary `
        -DatabaseSessionBoundary $databaseSessionBoundary
}
elseif ($env:STRATUS_CANARY_SQL_KIND -eq 'full') {
    Get-FullSql -AnchorDatabaseTime $boundary -CommandBoundaryTime $boundary `
        -DatabaseSessionBoundary $databaseSessionBoundary
}
else {
    throw 'Unknown canary SQL kind.'
}
"""
    environment = os.environ.copy()
    environment["STRATUS_CANARY_COLLECTOR"] = str(collector)
    environment["STRATUS_CANARY_SQL_KIND"] = kind
    result = subprocess.run(
        [
            _powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"PowerShell could not render {kind} SQL: {result.stderr}"
        )
    sql = result.stdout.strip()
    assert "__" not in sql
    assert sql.startswith(
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;"
    )
    assert sql.endswith("COMMIT;")
    return sql


async def _execute_read_only_snapshot(
    database_url: str,
    *,
    sql: str,
    timeout_literal: str,
) -> object:
    prefix = (
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
        f"SET LOCAL statement_timeout = '{timeout_literal}';"
    )
    assert sql.startswith(prefix)
    statement = sql.removeprefix(prefix).removesuffix("COMMIT;").strip()
    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        async with connection.transaction(
            isolation="repeatable_read",
            readonly=True,
        ):
            await connection.execute(
                f"SET LOCAL statement_timeout = '{timeout_literal}'"
            )
            return await connection.fetchval(statement)
    finally:
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "timeout_literal"),
    (("guard", "5s"), ("full", "8s")),
)
async def test_paper_canary_snapshot_sql_executes_on_current_schema(
    migrated_database_url: str,
    kind: str,
    timeout_literal: str,
) -> None:
    rendered = _render_canary_sql(kind)
    snapshot = await _execute_read_only_snapshot(
        migrated_database_url,
        sql=rendered,
        timeout_literal=timeout_literal,
    )
    assert snapshot is not None
