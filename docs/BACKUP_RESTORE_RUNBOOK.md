# STRATUS backup restore runbook

This runbook is the disaster-recovery contract for the atomic backup directories produced by the
Compose `backup` service. A readable custom archive is not sufficient evidence. Acceptance
requires marker-bound integrity checks, a complete restore, the database-scoped role/ACL
bootstrap, runtime-boundary assertions, and application-ledger reconciliation.

This procedure never authorizes LIVE. During a production cutover, execution remains behind the
kill switch until the restored snapshot and the separately built candidate have both passed their
gates.

## 1. Select an accepted restore point

Use only `backups/.stratus-backup-health`. Do not select the newest directory by name or
timestamp. Refuse the operation when any `.stratus_*.backup.partial` directory exists or when the
marker target is a symlink, is outside `backups/`, or does not match
`stratus_[A-Za-z0-9_.-]+.backup`.

The marker must be `stratus-backup-health-v1` and must bind the target to the SHA-256 of its
`manifest.txt`. The target must contain three regular, nonempty files:

- `stratus.dump`;
- `catalog.list`;
- `manifest.txt`.

Validate every `stratus-backup-manifest-v2` field independently:

- database and archive identity;
- schema head before and after the dump;
- start time after the required operational boundary;
- archive byte count and SHA-256;
- catalog byte count, entry count, and SHA-256;
- `catalog_verified=true`;
- `schema_stable_during_dump=true`;
- `full_restore_verified=false`.

`full_restore_verified=false` is correct producer behavior. Never edit the producer manifest
after a restore drill; record independent restore evidence separately.

Finally, run the same pinned PostgreSQL tool image used by the backup service:

```powershell
docker run --rm --network none `
  --mount "type=bind,source=$BackupDir,target=/restore,readonly" `
  postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416 `
  pg_restore --list /restore/stratus.dump
```

## 2. Create an isolated target

Create a uniquely named disposable volume and container. The container must use:

- `--network none`;
- no published port;
- fresh non-production owner and runtime passwords;
- the accepted backup directory mounted read-only;
- `ops/postgres/stratus-pg_hba.conf` mounted read-only;
- the pinned PostgreSQL image digest above.

Never use the production volume, owner password, runtime password, or Compose network. A failed
parallel restore is discarded as a whole; do not continue using its partially restored volume.

## 3. Bootstrap cluster roles

`pg_dump` does not archive cluster roles. Before `pg_restore`, create the two database-scoped
roles expected by revision 0044. Their names, attributes, provenance comments, grantor, and
membership options are part of the runtime security boundary:

```sql
DO $roles$
DECLARE
    suffix text := md5(current_database());
    runtime_name name := ('stratus_runtime_' || suffix)::name;
    app_name name := ('stratus_app_' || suffix)::name;
BEGIN
    EXECUTE format(
        'CREATE ROLE %I NOLOGIN NOSUPERUSER INHERIT NOCREATEDB NOCREATEROLE '
        'NOREPLICATION NOBYPASSRLS',
        runtime_name
    );
    EXECUTE format(
        'COMMENT ON ROLE %I IS %L',
        runtime_name,
        format('STRATUS managed runtime group v2 database=%s', current_database())
    );
    EXECUTE format(
        'CREATE ROLE %I NOLOGIN NOSUPERUSER INHERIT NOCREATEDB NOCREATEROLE '
        'NOREPLICATION NOBYPASSRLS',
        app_name
    );
    EXECUTE format(
        'COMMENT ON ROLE %I IS %L',
        app_name,
        format('STRATUS managed application login v2 database=%s', current_database())
    );
    EXECUTE format(
        'GRANT %I TO %I WITH ADMIN FALSE, INHERIT TRUE, SET FALSE GRANTED BY stratus',
        runtime_name,
        app_name
    );
END
$roles$;
```

Give the app role a fresh restore-only password and enable `LOGIN`. Do not place that password in
source, logs, shell history, or the backup directory.

## 4. Restore the complete archive

Restore owners and privileges; do not use `--no-owner` or `--no-privileges`:

```powershell
docker exec -e "PGPASSWORD=$OwnerPassword" $Container `
  pg_restore `
  --host=127.0.0.1 `
  --username=stratus `
  --dbname=stratus `
  --exit-on-error `
  --verbose `
  --jobs=2 `
  /restore/stratus.dump
```

Do not combine `--jobs` with `--single-transaction`. The target is disposable, and any nonzero
exit invalidates the whole drill.

## 5. Bootstrap the target-database ACL

Database ACL is also outside an ordinary database dump. A newly initialized target gives
`TEMPORARY` to `PUBLIC`; if left in place, the app role has more authority than production and
`assert_stratus_runtime_boundary` must fail.

After restore, apply the database-scope subset of the revision-0044 provisioner:

```sql
DO $database_acl$
DECLARE
    runtime_name name := ('stratus_runtime_' || md5(current_database()))::name;
    app_name name := ('stratus_app_' || md5(current_database()))::name;
BEGIN
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I, %I',
        current_database(), runtime_name, app_name
    );
    EXECUTE format(
        'REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC',
        current_database()
    );
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO %I',
        current_database(), runtime_name
    );
END
$database_acl$;
```

This is required environmental provisioning, not repair of application rows. Schema, table,
sequence, function, and default ACL are restored from the archive and are then checked by the
runtime-boundary function.

## 6. Acceptance checks

All of the following must pass:

1. exactly one Alembic row at the reviewed head;
2. expected PostgreSQL major/minor and `pgcrypto`;
3. zero invalid indexes, unvalidated constraints, and disabled user triggers;
4. valid HBA rules with zero parse errors;
5. `public.assert_stratus_runtime_boundary(false)` as the owner;
6. a real password login as the database-scoped app role followed by
   `public.assert_stratus_runtime_boundary(true)`;
7. expected global and per-venue fail-closed runtime state;
8. zero LIVE orders, positions, intents, fill legs, and prospective enrollments;
9. zero pending operator commands;
10. complete release epochs and exactly six continuity lanes per epoch;
11. expected application table counts and database size;
12. clean execution-cost reconciliation using
    `scripts/sql/execution_cost_incident_reconciliation.sql`.

The reconciliation blocks on unresolved-after-grace marks, projection mismatches, false missing
marks with an eligible snapshot, premature seals, duplicate marks/seals, invalid record hashes,
trigger/function drift, nonsealed obligations, or any LIVE evidence.

## 7. Record and clean up

Record, outside the producer manifest:

- marker target and manifest SHA-256;
- archive/catalog size and SHA-256;
- snapshot start/completion timestamps and schema head;
- PostgreSQL version;
- restore command and terminal exit status;
- boundary, schema, table-count, runtime, LIVE, epoch/lane, and reconciliation results;
- temporary container/volume names and proof that both were removed.

Always remove the disposable container and volume in a `finally` path. Retain only the
hash-bound acceptance log in the ignored release-artifact directory or the reviewed release
record.
