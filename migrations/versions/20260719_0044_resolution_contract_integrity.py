"""Enforce immutable resolution-contract content at the database boundary.

Revision ID: 20260719_0044
Revises: 20260717_0043
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260719_0044"
down_revision: str | None = "20260717_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Freeze both sides of the cross-table invariant before validating the
    # 0043 inventory.  SHARE ROW EXCLUSIVE conflicts with ordinary writer
    # locks and is held until the migration transaction commits, so a writer
    # that started before 0044 is either visible to this scan or completes
    # only after both triggers have been installed.
    op.execute(
        """
        LOCK TABLE public.markets, public.resolution_contracts
        IN SHARE ROW EXCLUSIVE MODE
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.resolution_rules_market_date(rules_raw text)
        RETURNS date
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $function$
        DECLARE
            match_count integer;
            day_text text;
            month_text text;
            year_text text;
            month_number integer;
            parsed_date date;
        BEGIN
            SELECT
                count(*)::integer,
                min(parts[1]),
                min(parts[2]),
                min(parts[3])
            INTO match_count, day_text, month_text, year_text
            FROM regexp_matches(
                rules_raw,
                $pattern$(?<![A-Za-z0-9])([0-9]{1,2}) ([A-Za-z]{3}) '([0-9]{2})(?![0-9])$pattern$,
                'g'
            ) AS captures(parts);

            IF match_count <> 1 THEN
                RAISE EXCEPTION '%',
                    'resolution rules must contain exactly one market date token '
                    || 'in <day> <Mon> ''<yy> form'
                    USING ERRCODE = '23514';
            END IF;

            month_number := CASE month_text
                WHEN 'Jan' THEN 1
                WHEN 'Feb' THEN 2
                WHEN 'Mar' THEN 3
                WHEN 'Apr' THEN 4
                WHEN 'May' THEN 5
                WHEN 'Jun' THEN 6
                WHEN 'Jul' THEN 7
                WHEN 'Aug' THEN 8
                WHEN 'Sep' THEN 9
                WHEN 'Oct' THEN 10
                WHEN 'Nov' THEN 11
                WHEN 'Dec' THEN 12
                ELSE NULL
            END;
            IF month_number IS NULL THEN
                RAISE EXCEPTION 'resolution rules contain an invalid market month'
                    USING ERRCODE = '23514';
            END IF;

            BEGIN
                parsed_date := make_date(
                    2000 + year_text::integer,
                    month_number,
                    day_text::integer
                );
            EXCEPTION WHEN OTHERS THEN
                RAISE EXCEPTION 'resolution rules contain an invalid market date'
                    USING ERRCODE = '23514';
            END;
            RETURN parsed_date;
        END
        $function$
        """
    )
    # BEFORE triggers do not examine rows which already exist at migration
    # time.  Validate the complete 0043 inventory first so a malformed,
    # orphaned, or date-mismatched contract cannot be grandfathered into the
    # immutable 0044 boundary.  The migration transaction rolls the helper
    # function back as well when this proof fails.
    op.execute(
        """
        DO $validate_existing_contract_dates$
        DECLARE
            contract record;
            contract_date date;
            market_count bigint;
            mismatched_market_count bigint;
        BEGIN
            FOR contract IN
                SELECT id, event_id, rules_raw
                FROM public.resolution_contracts
                ORDER BY id
            LOOP
                BEGIN
                    contract_date := public.resolution_rules_market_date(
                        contract.rules_raw
                    );
                EXCEPTION WHEN check_violation THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = format(
                            'pre-existing resolution contract %s has malformed rules',
                            contract.id
                        ),
                        DETAIL = SQLERRM;
                END;

                SELECT
                    count(*),
                    count(*) FILTER (
                        WHERE market.market_date IS DISTINCT FROM contract_date
                    )
                INTO market_count, mismatched_market_count
                FROM public.markets AS market
                WHERE market.event_id = contract.event_id;

                IF market_count = 0 THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = format(
                            'pre-existing resolution contract %s has no markets',
                            contract.id
                        ),
                        DETAIL = format('event_id=%s', contract.event_id);
                END IF;
                IF mismatched_market_count <> 0 THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = format(
                            'pre-existing resolution contract %s has mismatched market dates',
                            contract.id
                        ),
                        DETAIL = format(
                            'event_id=%s rules_date=%s mismatched_markets=%s',
                            contract.event_id,
                            contract_date,
                            mismatched_market_count
                        );
                END IF;
            END LOOP;
        END
        $validate_existing_contract_dates$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_resolution_contract_market_date()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        DECLARE
            contract_date date;
        BEGIN
            -- Serialize both insertion orders for one event.  Hash collisions
            -- are correctness-safe: they only serialize unrelated events;
            -- equal event ids always acquire the same transaction lock.
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    'stratus:resolution-contract:event:' || NEW.event_id,
                    20260719
                )
            );
            contract_date := public.resolution_rules_market_date(NEW.rules_raw);
            IF EXISTS (
                SELECT 1
                FROM public.markets AS market
                WHERE market.event_id = NEW.event_id
                  AND market.market_date IS DISTINCT FROM contract_date
            ) THEN
                RAISE EXCEPTION
                    'resolution contract rules date does not match every market date for event %',
                    NEW.event_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_market_resolution_contract_date()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        DECLARE
            old_lock_key bigint;
            new_lock_key bigint;
        BEGIN
            -- An event-id move touches two invariant domains.  Lock them in a
            -- stable key order to avoid deadlocks with the reverse move.  A
            -- hash collision takes one lock and merely over-serializes the two
            -- unrelated events; it can never bypass the invariant.
            new_lock_key := hashtextextended(
                'stratus:resolution-contract:event:' || NEW.event_id,
                20260719
            );
            IF TG_OP = 'UPDATE'
               AND OLD.event_id IS DISTINCT FROM NEW.event_id THEN
                old_lock_key := hashtextextended(
                    'stratus:resolution-contract:event:' || OLD.event_id,
                    20260719
                );
                IF old_lock_key < new_lock_key THEN
                    PERFORM pg_advisory_xact_lock(old_lock_key);
                    PERFORM pg_advisory_xact_lock(new_lock_key);
                ELSIF old_lock_key > new_lock_key THEN
                    PERFORM pg_advisory_xact_lock(new_lock_key);
                    PERFORM pg_advisory_xact_lock(old_lock_key);
                ELSE
                    PERFORM pg_advisory_xact_lock(new_lock_key);
                END IF;
            ELSE
                PERFORM pg_advisory_xact_lock(new_lock_key);
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.resolution_contracts AS contract
                WHERE contract.event_id = NEW.event_id
                  AND public.resolution_rules_market_date(contract.rules_raw)
                      IS DISTINCT FROM NEW.market_date
            ) THEN
                RAISE EXCEPTION
                    'market date does not match resolution contract rules date for event %',
                    NEW.event_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_resolution_contract_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'resolution contract content is immutable';
            END IF;

            IF ROW(
                NEW.id,
                NEW.event_id,
                NEW.rules_hash,
                NEW.source_family,
                NEW.source_url,
                NEW.station_code,
                NEW.local_timezone,
                NEW.statistic,
                NEW.source_unit,
                NEW.decimal_places,
                NEW.finalization_rule,
                NEW.license_status,
                NEW.storage_permitted,
                NEW.license_url,
                NEW.rules_raw,
                NEW.discovered_at
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.event_id,
                OLD.rules_hash,
                OLD.source_family,
                OLD.source_url,
                OLD.station_code,
                OLD.local_timezone,
                OLD.statistic,
                OLD.source_unit,
                OLD.decimal_places,
                OLD.finalization_rule,
                OLD.license_status,
                OLD.storage_permitted,
                OLD.license_url,
                OLD.rules_raw,
                OLD.discovered_at
            ) THEN
                RAISE EXCEPTION 'resolution contract content is immutable';
            END IF;

            IF OLD.retired_at IS NULL AND NEW.retired_at IS NOT NULL THEN
                IF NEW.retired_at < OLD.discovered_at THEN
                    RAISE EXCEPTION 'resolution contract retirement predates discovery';
                END IF;
                RETURN NEW;
            END IF;

            RAISE EXCEPTION 'resolution contract retirement is irreversible';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_resolution_contract_immutable
        BEFORE UPDATE OR DELETE ON public.resolution_contracts
        FOR EACH ROW EXECUTE FUNCTION public.enforce_resolution_contract_immutability()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_resolution_contract_market_date
        BEFORE INSERT OR UPDATE ON public.resolution_contracts
        FOR EACH ROW EXECUTE FUNCTION public.enforce_resolution_contract_market_date()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_market_resolution_contract_date
        BEFORE INSERT OR UPDATE ON public.markets
        FOR EACH ROW EXECUTE FUNCTION public.enforce_market_resolution_contract_date()
        """
    )
    # Default privileges below are meaningful only when the migration runs as
    # the owner that creates application objects.  Refuse an accidental admin
    # or differently-owned deployment instead of installing a porous boundary.
    op.execute(
        """
        DO $owner_boundary$
        DECLARE
            database_owner name;
        BEGIN
            IF current_setting('server_version_num')::integer < 160000 THEN
                RAISE EXCEPTION
                    '0044 runtime-role boundary requires PostgreSQL 16+';
            END IF;
            SELECT pg_get_userbyid(datdba) INTO database_owner
            FROM pg_database
            WHERE datname = current_database();
            IF current_user <> 'stratus'
               OR session_user <> 'stratus'
               OR database_owner IS DISTINCT FROM 'stratus'::name THEN
                RAISE EXCEPTION USING
                    MESSAGE = '0044 must run as the stratus database owner',
                    DETAIL = format(
                        'current_user=%I session_user=%I database_owner=%I',
                        current_user,
                        session_user,
                        database_owner
                    );
            END IF;
        END
        $owner_boundary$;
        """
    )
    # PostgreSQL roles are cluster-global, so every database receives a
    # deterministic, database-qualified pair. A test migration can neither
    # inherit production ACLs nor rotate the production login password.
    op.execute(
        """
        DO $roles$
        DECLARE
            role_suffix text := md5(current_database());
            runtime_name name := ('stratus_runtime_' || role_suffix)::name;
            app_name name := ('stratus_app_' || role_suffix)::name;
            runtime_marker text := format(
                'STRATUS managed runtime group v2 database=%s', current_database()
            );
            app_marker text := format(
                'STRATUS managed application login v2 database=%s', current_database()
            );
        BEGIN
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_name) THEN
                    EXECUTE format(
                        'CREATE ROLE %I NOLOGIN NOSUPERUSER INHERIT '
                        'NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
                        runtime_name
                    );
                    EXECUTE format(
                        'COMMENT ON ROLE %I IS %L', runtime_name, runtime_marker
                    );
                END IF;
            EXCEPTION WHEN duplicate_object THEN
                NULL;
            END;
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_name) THEN
                    EXECUTE format(
                        'CREATE ROLE %I NOLOGIN NOSUPERUSER INHERIT '
                        'NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
                        app_name
                    );
                    EXECUTE format(
                        'COMMENT ON ROLE %I IS %L', app_name, app_marker
                    );
                END IF;
            EXCEPTION WHEN duplicate_object THEN
                NULL;
            END;
        END
        $roles$;
        """
    )
    op.execute(
        """
        DO $validate_roles$
        DECLARE
            runtime_name name := ('stratus_runtime_' || md5(current_database()))::name;
            app_name name := ('stratus_app_' || md5(current_database()))::name;
            runtime_marker text := format(
                'STRATUS managed runtime group v2 database=%s', current_database()
            );
            app_marker text := format(
                'STRATUS managed application login v2 database=%s', current_database()
            );
            runtime_role record;
            app_role record;
        BEGIN
            SELECT * INTO runtime_role FROM pg_roles WHERE rolname = runtime_name;
            SELECT * INTO app_role FROM pg_roles WHERE rolname = app_name;
            IF runtime_role.oid IS NULL OR app_role.oid IS NULL THEN
                RAISE EXCEPTION 'STRATUS database-scoped roles are missing';
            END IF;
            IF shobj_description(runtime_role.oid, 'pg_authid') IS DISTINCT FROM
                   runtime_marker
               OR shobj_description(app_role.oid, 'pg_authid') IS DISTINCT FROM
                   app_marker THEN
                RAISE EXCEPTION 'STRATUS database-scoped role provenance drifted';
            END IF;
            IF runtime_role.rolcanlogin OR runtime_role.rolsuper
               OR NOT runtime_role.rolinherit OR runtime_role.rolcreatedb
               OR runtime_role.rolcreaterole OR runtime_role.rolreplication
               OR runtime_role.rolbypassrls OR runtime_role.rolconfig IS NOT NULL
               OR app_role.rolsuper OR NOT app_role.rolinherit
               OR app_role.rolcreatedb OR app_role.rolcreaterole
               OR app_role.rolreplication OR app_role.rolbypassrls
               OR app_role.rolconfig IS NOT NULL THEN
                RAISE EXCEPTION 'STRATUS database-scoped role attributes are unsafe';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_db_role_setting
                WHERE setrole IN (runtime_role.oid, app_role.oid)
            ) OR has_parameter_privilege(
                app_role.oid, 'session_replication_role', 'SET'
            ) OR has_parameter_privilege(
                runtime_role.oid, 'session_replication_role', 'SET'
            ) THEN
                RAISE EXCEPTION 'STRATUS role parameter boundary is unsafe';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_auth_members membership
                WHERE membership.member = runtime_role.oid
                   OR (
                       membership.member = app_role.oid
                       AND membership.roleid <> runtime_role.oid
                   )
                   OR (
                       membership.roleid = runtime_role.oid
                       AND membership.member <> app_role.oid
                   )
                   OR membership.roleid = app_role.oid
                   OR (
                       membership.roleid = runtime_role.oid
                       AND membership.member = app_role.oid
                       AND (
                           membership.grantor <> 'stratus'::regrole
                           OR membership.admin_option
                           OR NOT membership.inherit_option
                           OR membership.set_option
                       )
                   )
            ) THEN
                RAISE EXCEPTION 'STRATUS database-scoped membership is unsafe';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_shdepend
                WHERE refclassid = 'pg_authid'::regclass
                  AND refobjid IN (runtime_role.oid, app_role.oid)
                  AND deptype = 'o'
            ) THEN
                RAISE EXCEPTION 'STRATUS database-scoped role owns cluster objects';
            END IF;
            EXECUTE format(
                'GRANT %I TO %I WITH ADMIN FALSE, INHERIT TRUE, SET FALSE '
                'GRANTED BY stratus',
                runtime_name,
                app_name
            );
        END
        $validate_roles$;
        """
    )
    op.execute(
        """
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
                'REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I, %I',
                runtime_name, app_name
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I, %I',
                runtime_name, app_name
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I, %I',
                runtime_name, app_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE stratus IN SCHEMA public '
                'REVOKE ALL PRIVILEGES ON TABLES FROM %I, %I',
                runtime_name, app_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE stratus IN SCHEMA public '
                'REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I, %I',
                runtime_name, app_name
            );
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO %I',
                current_database(), runtime_name
            );
            EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', runtime_name);
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE '
                'ON ALL TABLES IN SCHEMA public TO %I', runtime_name
            );
            EXECUTE format(
                'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
                'ON TABLE public.alembic_version FROM %I', runtime_name
            );
            EXECUTE format(
                'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I',
                runtime_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE stratus IN SCHEMA public '
                'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
                runtime_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE stratus IN SCHEMA public '
                'GRANT USAGE, SELECT ON SEQUENCES TO %I', runtime_name
            );
        END
        $database_acl$;
        """
    )
    # This invoker-rights assertion is installed on every physical runtime
    # connection, so a stale successful provisioner cannot hide later drift.
    op.execute(
        """
        CREATE FUNCTION public.assert_stratus_runtime_boundary(
            require_login boolean DEFAULT true
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $boundary$
        DECLARE
            runtime_name name := ('stratus_runtime_' || md5(current_database()))::name;
            app_name name := ('stratus_app_' || md5(current_database()))::name;
            runtime_role record;
            app_role record;
            database_owner name;
        BEGIN
            IF current_setting('server_version_num')::integer < 160000 THEN
                RAISE EXCEPTION 'STRATUS runtime boundary requires PostgreSQL 16+';
            END IF;
            IF require_login AND (
                session_user <> app_name OR current_user <> app_name
            ) THEN
                RAISE EXCEPTION
                    'runtime boundary must execute as the database-scoped app login';
            ELSIF NOT require_login AND (
                session_user <> 'stratus' OR current_user <> 'stratus'
            ) THEN
                RAISE EXCEPTION
                    'owner boundary must execute as the stratus owner';
            END IF;
            IF current_setting('session_replication_role') IS DISTINCT FROM 'origin' THEN
                RAISE EXCEPTION
                    'STRATUS runtime session_replication_role is not origin';
            END IF;
            SELECT pg_get_userbyid(datdba) INTO database_owner
            FROM pg_database
            WHERE datname = current_database();
            IF database_owner IS DISTINCT FROM 'stratus'::name THEN
                RAISE EXCEPTION 'STRATUS database owner drifted';
            END IF;

            SELECT * INTO runtime_role
            FROM pg_roles
            WHERE rolname = runtime_name;
            SELECT * INTO app_role
            FROM pg_roles
            WHERE rolname = app_name;
            IF runtime_role.oid IS NULL OR app_role.oid IS NULL THEN
                RAISE EXCEPTION 'STRATUS runtime role is missing';
            END IF;
            IF shobj_description(runtime_role.oid, 'pg_authid') IS DISTINCT FROM
                   format(
                       'STRATUS managed runtime group v2 database=%s',
                       current_database()
                   )
               OR shobj_description(app_role.oid, 'pg_authid') IS DISTINCT FROM
                   format(
                       'STRATUS managed application login v2 database=%s',
                       current_database()
                   ) THEN
                RAISE EXCEPTION 'STRATUS runtime role provenance drifted';
            END IF;
            IF runtime_role.rolcanlogin
               OR runtime_role.rolsuper
               OR NOT runtime_role.rolinherit
               OR runtime_role.rolcreatedb
               OR runtime_role.rolcreaterole
               OR runtime_role.rolreplication
               OR runtime_role.rolbypassrls
               OR runtime_role.rolconfig IS NOT NULL
               OR app_role.rolsuper
               OR NOT app_role.rolinherit
               OR app_role.rolcreatedb
               OR app_role.rolcreaterole
               OR app_role.rolreplication
               OR app_role.rolbypassrls
               OR app_role.rolconfig IS NOT NULL
               OR (require_login AND NOT app_role.rolcanlogin) THEN
                RAISE EXCEPTION 'STRATUS runtime role attributes drifted';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_db_role_setting
                WHERE setrole IN (runtime_role.oid, app_role.oid)
            ) OR has_parameter_privilege(
                app_role.oid, 'session_replication_role', 'SET'
            ) OR has_parameter_privilege(
                runtime_role.oid, 'session_replication_role', 'SET'
            ) THEN
                RAISE EXCEPTION 'STRATUS runtime parameter boundary drifted';
            END IF;
            IF (
                SELECT count(*) FROM pg_auth_members
                WHERE roleid = runtime_role.oid AND member = app_role.oid
            ) <> 1 OR NOT EXISTS (
                SELECT 1 FROM pg_auth_members
                WHERE roleid = runtime_role.oid
                  AND member = app_role.oid
                  AND grantor = 'stratus'::regrole
                  AND NOT admin_option
                  AND inherit_option
                  AND NOT set_option
            ) OR EXISTS (
                SELECT 1 FROM pg_auth_members membership
                WHERE membership.member = runtime_role.oid
                   OR (
                       membership.member = app_role.oid
                       AND membership.roleid <> runtime_role.oid
                   )
                   OR (
                       membership.roleid = runtime_role.oid
                       AND membership.member <> app_role.oid
                   )
                   OR membership.roleid = app_role.oid
            ) THEN
                RAISE EXCEPTION 'STRATUS runtime membership drifted';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_shdepend
                WHERE refclassid = 'pg_authid'::regclass
                  AND refobjid IN (runtime_role.oid, app_role.oid)
                  AND deptype = 'o'
            ) THEN
                RAISE EXCEPTION 'STRATUS runtime role owns cluster objects';
            END IF;

            IF NOT has_database_privilege(
                       app_role.oid, current_database(), 'CONNECT'
                   )
               OR has_database_privilege(
                       app_role.oid, current_database(), 'TEMPORARY'
                   )
               OR has_database_privilege(
                       app_role.oid, current_database(), 'CREATE'
                   )
               OR NOT has_schema_privilege(app_role.oid, 'public', 'USAGE')
               OR has_schema_privilege(app_role.oid, 'public', 'CREATE') THEN
                RAISE EXCEPTION 'STRATUS database or schema ACL drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_class relation
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p')
                  AND (
                      has_table_privilege(
                          app_role.oid, relation.oid, 'TRUNCATE'
                      )
                      OR has_table_privilege(
                          app_role.oid, relation.oid, 'REFERENCES'
                      )
                      OR has_table_privilege(
                          app_role.oid, relation.oid, 'TRIGGER'
                      )
                      OR (
                          relation.relname <> 'alembic_version'
                          AND NOT (
                              has_table_privilege(
                                  app_role.oid, relation.oid, 'SELECT'
                              )
                              AND has_table_privilege(
                                  app_role.oid, relation.oid, 'INSERT'
                              )
                              AND has_table_privilege(
                                  app_role.oid, relation.oid, 'UPDATE'
                              )
                              AND has_table_privilege(
                                  app_role.oid, relation.oid, 'DELETE'
                              )
                          )
                      )
                      OR (
                          relation.relname = 'alembic_version'
                          AND (
                              NOT has_table_privilege(
                                  app_role.oid, relation.oid, 'SELECT'
                              )
                              OR has_table_privilege(
                                  app_role.oid, relation.oid, 'INSERT'
                              )
                              OR has_table_privilege(
                                  app_role.oid, relation.oid, 'UPDATE'
                              )
                              OR has_table_privilege(
                                  app_role.oid, relation.oid, 'DELETE'
                              )
                          )
                      )
                  )
            ) THEN
                RAISE EXCEPTION 'STRATUS table ACL drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_class sequence
                JOIN pg_namespace namespace
                  ON namespace.oid = sequence.relnamespace
                WHERE namespace.nspname = 'public'
                  AND sequence.relkind = 'S'
                  AND (
                      NOT has_sequence_privilege(
                          app_role.oid, sequence.oid, 'USAGE'
                      )
                      OR NOT has_sequence_privilege(
                          app_role.oid, sequence.oid, 'SELECT'
                      )
                      OR has_sequence_privilege(
                          app_role.oid, sequence.oid, 'UPDATE'
                      )
                  )
            ) THEN
                RAISE EXCEPTION 'STRATUS sequence ACL drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'resolution_contracts'::text,
                            'trg_resolution_contract_immutable'::text,
                            'public.enforce_resolution_contract_immutability()'::text,
                            27::smallint
                        ),
                        (
                            'resolution_contracts'::text,
                            'trg_resolution_contract_market_date'::text,
                            'public.enforce_resolution_contract_market_date()'::text,
                            23::smallint
                        ),
                        (
                            'markets'::text,
                            'trg_market_resolution_contract_date'::text,
                            'public.enforce_market_resolution_contract_date()'::text,
                            23::smallint
                        )
                ) AS expected(
                    relation_name,
                    trigger_name,
                    function_signature,
                    trigger_type
                )
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM pg_trigger trigger_row
                    JOIN pg_class relation
                      ON relation.oid = trigger_row.tgrelid
                    JOIN pg_namespace namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relname = expected.relation_name
                      AND trigger_row.tgname = expected.trigger_name
                      AND NOT trigger_row.tgisinternal
                      AND trigger_row.tgenabled IN ('O', 'A')
                      AND trigger_row.tgfoid =
                          to_regprocedure(expected.function_signature)
                      AND trigger_row.tgtype = expected.trigger_type
                )
            ) THEN
                RAISE EXCEPTION 'STRATUS integrity trigger boundary drifted';
            END IF;
            RETURN true;
        END
        $boundary$;
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.assert_stratus_runtime_boundary(boolean) FROM PUBLIC"
    )
    op.execute(
        """
        DO $grant_boundary$
        DECLARE
            runtime_name name := ('stratus_runtime_' || md5(current_database()))::name;
        BEGIN
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'public.assert_stratus_runtime_boundary(boolean) TO %I',
                runtime_name
            );
        END
        $grant_boundary$;
        """
    )
    op.execute("SELECT public.assert_stratus_runtime_boundary(false)")


def downgrade() -> None:
    # Removing the trigger while immutable evidence exists would silently
    # weaken an already-used boundary.  Empty migration drills remain
    # reversible; production evidence requires a forward repair instead.
    op.execute(
        """
        DO $guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.resolution_contracts LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot downgrade 20260719_0044 with resolution contracts present';
            END IF;
        END
        $guard$;
        """
    )
    # Per-database and owner-default ACLs are reversible. Database-qualified
    # cluster roles and membership remain as provenance; downgrade never
    # mutates LOGIN or passwords.
    op.execute(
        """
        DO $revoke_runtime_acl$
        DECLARE
            runtime_name name := ('stratus_runtime_' || md5(current_database()))::name;
            app_name name := ('stratus_app_' || md5(current_database()))::name;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_name)
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_name) THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I, %I',
                    current_database(), runtime_name, app_name
                );
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I, %I',
                    runtime_name, app_name
                );
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public '
                    'FROM %I, %I', runtime_name, app_name
                );
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public '
                    'FROM %I, %I', runtime_name, app_name
                );
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE stratus IN SCHEMA public '
                    'REVOKE ALL PRIVILEGES ON TABLES FROM %I, %I',
                    runtime_name, app_name
                );
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE stratus IN SCHEMA public '
                    'REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I, %I',
                    runtime_name, app_name
                );
            ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_name)
               OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_name) THEN
                RAISE EXCEPTION 'STRATUS database-scoped role pair is incomplete';
            END IF;
            EXECUTE format(
                'GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database()
            );
        END
        $revoke_runtime_acl$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_market_resolution_contract_date "
        "ON public.markets"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_resolution_contract_market_date "
        "ON public.resolution_contracts"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_resolution_contract_immutable "
        "ON public.resolution_contracts"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "public.assert_stratus_runtime_boundary(boolean)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.enforce_resolution_contract_immutability()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.enforce_market_resolution_contract_date()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.enforce_resolution_contract_market_date()"
    )
    op.execute("DROP FUNCTION IF EXISTS public.resolution_rules_market_date(text)")
