"""Add the DB-clock PAPER canary safety lease.

Revision ID: 20260730_0047
Revises: 20260730_0046
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0047"
down_revision: str | None = "20260730_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_canary_safety_leases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("lease_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("run_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "acquire_request_id",
            sa.String(length=64),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "resume_request_id",
            sa.String(length=64),
            nullable=False,
            unique=True,
        ),
        sa.Column("resume_actor", sa.String(length=64), nullable=False),
        sa.Column("resume_reason", sa.Text(), nullable=False),
        sa.Column(
            "watchdog_stop_request_id",
            sa.String(length=64),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "direct_stop_request_id",
            sa.String(length=64),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "release_request_id",
            sa.String(length=64),
            nullable=False,
            unique=True,
        ),
        sa.Column("nonce_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "watchdog_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stop_command_id",
            sa.BigInteger(),
            sa.ForeignKey("operator_commands.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "lease_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND acquire_request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND resume_request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND watchdog_stop_request_id ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND direct_stop_request_id ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND release_request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$'",
            name="ck_paper_canary_lease_safe_identifiers",
        ),
        sa.CheckConstraint(
            "nonce_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_paper_canary_lease_nonce_sha256",
        ),
        sa.CheckConstraint(
            "btrim(resume_actor) <> '' "
            "AND char_length(resume_actor) <= 64 "
            "AND btrim(resume_reason) <> '' "
            "AND char_length(resume_reason) <= 2000",
            name="ck_paper_canary_lease_resume_identity",
        ),
        sa.CheckConstraint(
            "acquire_request_id <> resume_request_id "
            "AND watchdog_stop_request_id NOT IN "
            "(acquire_request_id, resume_request_id) "
            "AND direct_stop_request_id NOT IN "
            "(acquire_request_id, resume_request_id, watchdog_stop_request_id) "
            "AND release_request_id NOT IN ("
            "acquire_request_id, resume_request_id, "
            "watchdog_stop_request_id, direct_stop_request_id)",
            name="ck_paper_canary_lease_distinct_requests",
        ),
        sa.CheckConstraint(
            "heartbeat_at >= acquired_at "
            "AND watchdog_at >= acquired_at "
            "AND expires_at > heartbeat_at "
            "AND expires_at <= heartbeat_at + interval '30 seconds'",
            name="ck_paper_canary_lease_bounded_time",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'released', 'expired_stopped', 'expired_safe', 'revoked_stopped')",
            name="ck_paper_canary_lease_state",
        ),
        sa.CheckConstraint(
            "(state = 'active' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NULL) "
            "OR (state = 'released' AND released_at IS NOT NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NOT NULL) "
            "OR (state = 'expired_stopped' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NOT NULL) "
            "OR (state = 'revoked_stopped' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NOT NULL) "
            "OR (state = 'expired_safe' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NULL)",
            name="ck_paper_canary_lease_terminal_shape",
        ),
    )
    op.create_index(
        "uq_paper_canary_single_active_lease",
        "paper_canary_safety_leases",
        ["state"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "ix_paper_canary_active_expiry",
        "paper_canary_safety_leases",
        ["expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("state = 'active'"),
    )
    # Emergency STOP cleanup is deliberately bounded.  These partial indexes
    # make its candidate windows independent of historical table size.
    op.create_index(
        "ix_signals_pending_stop_cleanup",
        "signals",
        ["id"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'::signal_status"),
    )
    op.create_index(
        "ix_orders_live_open_stop_cleanup",
        "orders",
        ["id"],
        unique=False,
        postgresql_where=sa.text("mode = 'live'::execution_mode AND status IN ('open', 'pending')"),
    )

    # A fresh schema historically started with PAPER execution open and
    # Binance disabled. Introducing a lease fence must never inherit that
    # permissive state: atomically force the complete release boundary closed
    # and leave one durable operator audit before any runtime authority is
    # granted.
    op.execute(
        """
        DO $paper_canary_bootstrap$
        DECLARE
            database_now timestamptz := clock_timestamp();
            affected integer;
            expired_count integer;
            cancel_count integer;
            audit_row public.operator_commands%ROWTYPE;
        BEGIN
            PERFORM 1
            FROM public.runtime_control
            WHERE id = 1
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'PAPER canary bootstrap requires runtime_control id=1';
            END IF;
            PERFORM 1
            FROM public.venue_runtime_control
            WHERE venue::text IN ('polymarket', 'binance_spot')
            ORDER BY venue::text
            FOR UPDATE;
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 2 THEN
                RAISE EXCEPTION
                    'PAPER canary bootstrap requires both venue controls';
            END IF;

            UPDATE public.runtime_control
            SET mode = 'paper'::execution_mode,
                kill_switch = true,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE id = 1;
            UPDATE public.venue_runtime_control
            SET enabled = true,
                mode = 'paper'::venue_mode,
                kill_switch = true,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE venue::text = 'polymarket';
            UPDATE public.venue_runtime_control
            SET enabled = true,
                mode = 'backtest'::venue_mode,
                kill_switch = true,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE venue::text = 'binance_spot';

            UPDATE public.signals
            SET status = 'expired',
                skip_reason = 'paper_canary_migration_fail_closed'
            WHERE status::text = 'pending';
            GET DIAGNOSTICS expired_count = ROW_COUNT;
            UPDATE public.orders
            SET status = 'cancel_requested'
            WHERE mode::text = 'live'
              AND status IN ('open', 'pending');
            GET DIAGNOSTICS cancel_count = ROW_COUNT;

            INSERT INTO public.operator_commands(
                request_id,
                command,
                payload,
                actor,
                reason,
                status,
                detail,
                executed_at
            ) VALUES (
                'paper-canary-bootstrap-stop-0047',
                'stop',
                '{"venue":"all"}'::jsonb,
                'migration-0047',
                'Fail closed before DB-clock PAPER canary authority.',
                'succeeded',
                format(
                    'Migration 0047 forced global/Polymarket PAPER kills ON '
                    'and Binance enabled+backtest+kill ON; expired signals: '
                    '%s; cancel-requested LIVE orders: %s.',
                    expired_count,
                    cancel_count
                ),
                database_now
            );
            SELECT * INTO audit_row
            FROM public.operator_commands
            WHERE request_id = 'paper-canary-bootstrap-stop-0047'
            FOR UPDATE;
            IF NOT FOUND
               OR audit_row.command <> 'stop'
               OR audit_row.payload <> '{"venue":"all"}'::jsonb
               OR audit_row.actor <> 'migration-0047'
               OR audit_row.reason <>
                  'Fail closed before DB-clock PAPER canary authority.'
               OR audit_row.status <> 'succeeded'
               OR audit_row.executed_at IS NULL THEN
                RAISE EXCEPTION
                    'PAPER canary bootstrap audit identity collision';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.runtime_control
                WHERE id = 1
                  AND mode::text = 'paper'
                  AND kill_switch
                  AND live_confirmation_until IS NULL
            ) OR NOT EXISTS (
                SELECT 1
                FROM public.venue_runtime_control
                WHERE venue::text = 'polymarket'
                  AND enabled
                  AND mode::text = 'paper'
                  AND kill_switch
                  AND live_confirmation_until IS NULL
            ) OR NOT EXISTS (
                SELECT 1
                FROM public.venue_runtime_control
                WHERE venue::text = 'binance_spot'
                  AND enabled
                  AND mode::text = 'backtest'
                  AND kill_switch
                  AND live_confirmation_until IS NULL
            ) THEN
                RAISE EXCEPTION
                    'PAPER canary bootstrap failed closed-state assertion';
            END IF;
        END
        $paper_canary_bootstrap$;
        """
    )
    op.create_check_constraint(
        "ck_runtime_control_singleton_id",
        "runtime_control",
        "id = 1",
    )
    op.create_check_constraint(
        "ck_operator_commands_lifecycle",
        "operator_commands",
        "status IN ('pending', 'succeeded', 'rejected', 'failed') "
        "AND ((status = 'pending' AND detail IS NULL "
        "AND executed_at IS NULL) OR (status IN ("
        "'succeeded', 'rejected', 'failed') AND detail IS NOT NULL "
        "AND btrim(detail) <> '' AND executed_at IS NOT NULL "
        "AND executed_at >= created_at))",
    )

    # Safety-control uses a database-qualified login that is deliberately not
    # a member of the broad application runtime role. PostgreSQL roles are
    # cluster-global, so the database hash prevents test/release databases
    # from inheriting one another's credentials or grants.
    op.execute(
        """
        DO $safety_roles$
        DECLARE
            role_suffix text := md5(current_database());
            safety_name name := ('stratus_safety_' || role_suffix)::name;
            safety_app_name name := ('stratus_safety_app_' || role_suffix)::name;
            safety_marker text := format(
                'STRATUS managed safety group v1 database=%s',
                current_database()
            );
            safety_app_marker text := format(
                'STRATUS managed safety login v1 database=%s',
                current_database()
            );
            safety_role record;
            safety_app_role record;
        BEGIN
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = safety_name
                ) THEN
                    EXECUTE format(
                        'CREATE ROLE %I NOLOGIN NOSUPERUSER INHERIT '
                        'NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
                        safety_name
                    );
                    EXECUTE format(
                        'COMMENT ON ROLE %I IS %L',
                        safety_name,
                        safety_marker
                    );
                END IF;
            EXCEPTION WHEN duplicate_object THEN
                NULL;
            END;
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = safety_app_name
                ) THEN
                    EXECUTE format(
                        'CREATE ROLE %I NOLOGIN NOSUPERUSER INHERIT '
                        'NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
                        safety_app_name
                    );
                    EXECUTE format(
                        'COMMENT ON ROLE %I IS %L',
                        safety_app_name,
                        safety_app_marker
                    );
                END IF;
            EXCEPTION WHEN duplicate_object THEN
                NULL;
            END;

            SELECT * INTO safety_role
            FROM pg_roles WHERE rolname = safety_name;
            SELECT * INTO safety_app_role
            FROM pg_roles WHERE rolname = safety_app_name;
            IF safety_role.oid IS NULL OR safety_app_role.oid IS NULL
               OR shobj_description(
                   safety_role.oid, 'pg_authid'
               ) IS DISTINCT FROM safety_marker
               OR shobj_description(
                   safety_app_role.oid, 'pg_authid'
               ) IS DISTINCT FROM safety_app_marker
               OR safety_role.rolcanlogin
               OR safety_role.rolsuper
               OR NOT safety_role.rolinherit
               OR safety_role.rolcreatedb
               OR safety_role.rolcreaterole
               OR safety_role.rolreplication
               OR safety_role.rolbypassrls
               OR safety_role.rolconfig IS NOT NULL
               OR safety_app_role.rolsuper
               OR NOT safety_app_role.rolinherit
               OR safety_app_role.rolcreatedb
               OR safety_app_role.rolcreaterole
               OR safety_app_role.rolreplication
               OR safety_app_role.rolbypassrls
               OR safety_app_role.rolconfig IS NOT NULL THEN
                RAISE EXCEPTION
                    'STRATUS safety role provenance or attributes drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_auth_members membership
                WHERE membership.member = safety_role.oid
                   OR (
                       membership.member = safety_app_role.oid
                       AND membership.roleid <> safety_role.oid
                   )
                   OR (
                       membership.roleid = safety_role.oid
                       AND membership.member <> safety_app_role.oid
                   )
                   OR membership.roleid = safety_app_role.oid
            ) THEN
                RAISE EXCEPTION 'STRATUS safety role membership drifted';
            END IF;
            EXECUTE format(
                'GRANT %I TO %I WITH ADMIN FALSE, INHERIT TRUE, SET FALSE '
                'GRANTED BY stratus',
                safety_name,
                safety_app_name
            );
        END
        $safety_roles$;
        """
    )
    op.execute(
        """
        DO $safety_acl$
        DECLARE
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO %I',
                current_database(),
                safety_name
            );
            EXECUTE format(
                'GRANT USAGE ON SCHEMA public TO %I',
                safety_name
            );
            EXECUTE format(
                'GRANT SELECT ON TABLE '
                'public.paper_canary_safety_leases TO %I',
                safety_name
            );
        END
        $safety_acl$;
        """
    )
    op.execute("ALTER TABLE public.paper_canary_safety_leases ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $safety_policy$
        DECLARE
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
        BEGIN
            EXECUTE format(
                'CREATE POLICY paper_canary_safety_role_select '
                'ON public.paper_canary_safety_leases '
                'FOR SELECT TO %I USING (true)',
                safety_name
            );
        END
        $safety_policy$;
        """
    )

    # The PAPER-only fence is latched by installing this migration, not by the
    # first lease row. Runtime code may learn only that immutable fact,
    # whether an active row exists, and whether execution currently has a
    # valid observed lease. Lease identities, nonce hashes, and clocks remain
    # hidden behind owner-executed boolean functions.
    op.execute(
        """
        CREATE FUNCTION public.paper_canary_fence_armed()
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $authority$
            SELECT true
        $authority$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.paper_canary_active_lease_exists()
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $authority$
            SELECT EXISTS (
                SELECT 1
                FROM public.paper_canary_safety_leases
                WHERE state = 'active'
            )
        $authority$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.paper_canary_execution_authorized()
        RETURNS boolean
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $authority$
            SELECT EXISTS (
                SELECT 1
                FROM public.paper_canary_safety_leases
                WHERE state = 'active'
                  AND expires_at > clock_timestamp()
                  AND watchdog_at > acquired_at
                  AND watchdog_at >=
                      clock_timestamp() - interval '7 seconds'
            )
        $authority$;
        """
    )
    for signature in (
        "public.paper_canary_fence_armed()",
        "public.paper_canary_active_lease_exists()",
        "public.paper_canary_execution_authorized()",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC")

    op.execute(
        """
        CREATE FUNCTION public.enforce_paper_canary_lease_audit()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $lease_audit$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'PAPER canary safety leases are append-only audit rows';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.state <> 'active'
                   OR NEW.released_at IS NOT NULL
                   OR NEW.stop_command_id IS NOT NULL THEN
                    RAISE EXCEPTION 'new PAPER canary lease must be active';
                END IF;
                RETURN NEW;
            END IF;
            IF (
                NEW.lease_id,
                NEW.run_id,
                NEW.acquire_request_id,
                NEW.resume_request_id,
                NEW.resume_actor,
                NEW.resume_reason,
                NEW.release_request_id,
                NEW.watchdog_stop_request_id,
                NEW.direct_stop_request_id,
                NEW.nonce_sha256,
                NEW.acquired_at
            ) IS DISTINCT FROM (
                OLD.lease_id,
                OLD.run_id,
                OLD.acquire_request_id,
                OLD.resume_request_id,
                OLD.resume_actor,
                OLD.resume_reason,
                OLD.release_request_id,
                OLD.watchdog_stop_request_id,
                OLD.direct_stop_request_id,
                OLD.nonce_sha256,
                OLD.acquired_at
            ) THEN
                RAISE EXCEPTION 'PAPER canary lease identity is immutable';
            END IF;
            IF OLD.state <> 'active' AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'terminal PAPER canary lease is immutable';
            END IF;
            IF OLD.state = 'active' THEN
                IF NEW.state NOT IN (
                    'active', 'released', 'expired_stopped', 'expired_safe',
                    'revoked_stopped'
                ) THEN
                    RAISE EXCEPTION 'invalid PAPER canary lease transition';
                END IF;
                IF NEW.heartbeat_at < OLD.heartbeat_at
                   OR NEW.watchdog_at < OLD.watchdog_at
                   OR NEW.expires_at < OLD.expires_at THEN
                    RAISE EXCEPTION 'PAPER canary lease clock cannot move backwards';
                END IF;
                IF NEW.state <> 'active'
                   AND (
                       NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
                       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   ) THEN
                    RAISE EXCEPTION 'terminal transition cannot extend a lease';
                END IF;
            END IF;
            RETURN NEW;
        END
        $lease_audit$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_lease_audit
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.paper_canary_safety_leases
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_lease_audit()
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.enforce_paper_canary_lease_audit() FROM PUBLIC"
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_paper_canary_runtime_fence()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $runtime_fence$
        DECLARE
            runtime_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF current_user <> 'stratus'::name THEN
                    RAISE EXCEPTION
                        'runtime control deletion is owner-only';
                END IF;
                RETURN OLD;
            ELSIF TG_OP = 'INSERT' THEN
                IF current_user <> 'stratus'::name THEN
                    RAISE EXCEPTION
                        'runtime control insertion is owner-only';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id THEN
                RAISE EXCEPTION
                    'runtime control singleton identity is immutable';
            END IF;
            IF session_user = runtime_name
               AND current_user = runtime_name
               AND NOT pg_try_advisory_xact_lock(6004519027736253267) THEN
                RAISE EXCEPTION
                    'PAPER execution authority serialization is busy'
                    USING ERRCODE = '55P03';
            END IF;
            IF OLD.halted_reason IS NOT NULL
               AND NEW.halted_reason IS DISTINCT FROM OLD.halted_reason
               AND current_user <> 'stratus'::name THEN
                RAISE EXCEPTION
                    'runtime halt reason clearing or rewriting is owner-only';
            END IF;
            IF public.paper_canary_fence_armed() THEN
                IF (
                    NEW.mode::text <> 'paper'
                    AND NEW.mode IS DISTINCT FROM OLD.mode
                ) OR (
                    NEW.live_confirmation_until IS NOT NULL
                    AND NEW.live_confirmation_until
                        IS DISTINCT FROM OLD.live_confirmation_until
                ) THEN
                    RAISE EXCEPTION
                        'PAPER canary history forbids non-PAPER runtime state';
                END IF;
            END IF;
            IF OLD.kill_switch
               AND NOT NEW.kill_switch
               AND current_user <> 'stratus'::name THEN
                RAISE EXCEPTION
                    'runtime kill-off lacks exact PAPER canary authority';
            END IF;
            RETURN NEW;
        END
        $runtime_fence$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_runtime_fence
        BEFORE INSERT OR UPDATE OR DELETE ON public.runtime_control
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_runtime_fence()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_paper_canary_venue_fence()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $venue_fence$
        DECLARE
            runtime_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF current_user <> 'stratus'::name THEN
                    RAISE EXCEPTION
                        'venue runtime control deletion is owner-only';
                END IF;
                RETURN OLD;
            ELSIF TG_OP = 'INSERT' THEN
                IF current_user <> 'stratus'::name THEN
                    RAISE EXCEPTION
                        'venue runtime control insertion is owner-only';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.venue IS DISTINCT FROM OLD.venue THEN
                RAISE EXCEPTION
                    'venue runtime control identity is immutable';
            END IF;
            IF NEW.venue::text = 'polymarket'
               AND session_user = runtime_name
               AND current_user = runtime_name
               AND NOT pg_try_advisory_xact_lock(6004519027736253267) THEN
                RAISE EXCEPTION
                    'PAPER venue authority serialization is busy'
                    USING ERRCODE = '55P03';
            END IF;
            IF OLD.halted_reason IS NOT NULL
               AND NEW.halted_reason IS DISTINCT FROM OLD.halted_reason
               AND current_user <> 'stratus'::name THEN
                RAISE EXCEPTION
                    'venue halt reason clearing or rewriting is owner-only';
            END IF;
            IF NEW.venue::text = 'polymarket' THEN
                IF NOT NEW.enabled
                   AND public.paper_canary_active_lease_exists() THEN
                    RAISE EXCEPTION
                        'active PAPER canary lease forbids unsafe Polymarket state';
                END IF;
                IF public.paper_canary_fence_armed() AND (
                    (
                        NEW.mode::text <> 'paper'
                        AND NEW.mode IS DISTINCT FROM OLD.mode
                    )
                    OR (
                        NEW.live_confirmation_until IS NOT NULL
                        AND NEW.live_confirmation_until
                            IS DISTINCT FROM OLD.live_confirmation_until
                    )
                ) THEN
                    RAISE EXCEPTION
                        'PAPER canary history forbids unsafe Polymarket state';
                END IF;
                IF OLD.kill_switch
                   AND NOT NEW.kill_switch
                   AND current_user <> 'stratus'::name THEN
                    RAISE EXCEPTION
                        'Polymarket kill-off lacks exact PAPER canary authority';
                END IF;
            ELSIF NEW.venue::text = 'binance_spot'
                  AND current_user <> 'stratus'::name
                  AND (
                      NOT NEW.enabled
                      OR NEW.mode::text <> 'backtest'
                      OR NOT NEW.kill_switch
                      OR NEW.live_confirmation_until IS NOT NULL
                  ) THEN
                RAISE EXCEPTION
                    'Binance Spot is sealed at '
                    'enabled+backtest+kill_switch=true';
            END IF;
            RETURN NEW;
        END
        $venue_fence$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_venue_fence
        BEFORE INSERT OR UPDATE OR DELETE ON public.venue_runtime_control
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_venue_fence()
        """
    )
    # Execution authorization must still be true at the transaction's commit
    # edge, not merely when the worker first reads runtime_control.  These
    # deferred constraint triggers serialize exposure-bearing PAPER writes
    # against the same exclusive advisory lock used by emergency STOP.
    op.execute(
        """
        CREATE FUNCTION public.enforce_paper_canary_execution_commit_fence()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $execution_commit_fence$
        DECLARE
            runtime_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
            exposure_bearing boolean := false;
            projected_position public.positions%ROWTYPE;
            projected_position_count bigint;
            projected_leg_count bigint;
            projected_shares numeric;
            projected_notional numeric;
            projected_fee numeric;
            projected_avg_price numeric;
        BEGIN
            -- Owner bootstrap, migration, and deterministic test-data writes
            -- are outside the runtime execution authority boundary.
            IF session_user <> runtime_name THEN
                RETURN NEW;
            END IF;
            IF current_user <> runtime_name THEN
                -- The exact runtime STOP entry point is SECURITY DEFINER so
                -- that controls, cleanup, lease revocation, and command
                -- terminalization commit atomically.  Admit only the two
                -- risk-reducing row shapes that primitive owns; every other
                -- owner-context execution mutation remains forbidden.
                IF current_user = 'stratus'::name
                   AND TG_OP = 'UPDATE'
                   AND TG_TABLE_NAME = 'signals'
                   AND OLD.status::text = 'pending'
                   AND NEW.status::text = 'expired'
                   AND NEW.skip_reason = 'kill_switch'
                   AND (
                       NEW.id,
                       NEW.market_id,
                       NEW.ts,
                       NEW.side,
                       NEW.model_prob,
                       NEW.market_price,
                       NEW.edge,
                       NEW.kelly_frac,
                       NEW.size_usd,
                       NEW.confidence,
                       NEW.clob_snapshot_id,
                       NEW.station_reliability_snapshot_id,
                       NEW.token_id,
                       NEW.benchmark_protocol
                   ) IS NOT DISTINCT FROM (
                       OLD.id,
                       OLD.market_id,
                       OLD.ts,
                       OLD.side,
                       OLD.model_prob,
                       OLD.market_price,
                       OLD.edge,
                       OLD.kelly_frac,
                       OLD.size_usd,
                       OLD.confidence,
                       OLD.clob_snapshot_id,
                       OLD.station_reliability_snapshot_id,
                       OLD.token_id,
                       OLD.benchmark_protocol
                   ) THEN
                    RETURN NEW;
                ELSIF current_user = 'stratus'::name
                      AND TG_OP = 'UPDATE'
                      AND TG_TABLE_NAME = 'orders'
                      AND OLD.mode::text = 'live'
                      AND OLD.status IN ('open', 'pending')
                      AND NEW.status = 'cancel_requested'
                      AND (
                          NEW.id,
                          NEW.signal_id,
                          NEW.mode,
                          NEW.clob_order_id,
                          NEW.token_id,
                          NEW.side,
                          NEW.price,
                          NEW.size_usd,
                          NEW.filled_usd,
                          NEW.fee_usd,
                          NEW.created_at,
                          NEW.filled_at
                      ) IS NOT DISTINCT FROM (
                          OLD.id,
                          OLD.signal_id,
                          OLD.mode,
                          OLD.clob_order_id,
                          OLD.token_id,
                          OLD.side,
                          OLD.price,
                          OLD.size_usd,
                          OLD.filled_usd,
                          OLD.fee_usd,
                          OLD.created_at,
                          OLD.filled_at
                      ) THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION
                    'PAPER execution commit requires the exact runtime login';
            END IF;

            -- This release is permanently PAPER-latched.  A runtime login
            -- may still reconcile a pre-existing LIVE fill/position, but it
            -- may never originate a LIVE order or intent nor grow a LIVE
            -- order's requested exposure.
            IF TG_TABLE_NAME <> 'signals' THEN
                IF NEW.mode::text = 'live' THEN
                    CASE TG_TABLE_NAME
                        WHEN 'orders' THEN
                            IF TG_OP = 'INSERT' THEN
                                RAISE EXCEPTION
                                    'PAPER canary history forbids LIVE order exposure';
                            ELSIF NEW.size_usd > OLD.size_usd
                                  OR NEW.mode IS DISTINCT FROM OLD.mode
                                  OR (
                                      NEW.status IN (
                                          'submitting', 'open', 'pending', 'live'
                                      )
                                      AND NEW.status IS DISTINCT FROM OLD.status
                                  ) THEN
                                RAISE EXCEPTION
                                    'PAPER canary history forbids LIVE order exposure';
                            END IF;
                            IF NEW.status IN (
                                'filled', 'partially_filled', 'canceled',
                                'submit_failed', 'rejected', 'expired'
                            ) AND (
                                NOT EXISTS (
                                    SELECT 1
                                    FROM public.execution_order_intents AS intent
                                    JOIN public.execution_reconciliation_observations AS observation
                                      ON observation.order_id = intent.order_id
                                    JOIN public.execution_cost_obligations AS obligation
                                      ON obligation.intent_id = intent.id
                                     AND obligation.order_id = intent.order_id
                                     AND obligation.terminal_status = NEW.status
                                    JOIN public.execution_cost_seals AS seal
                                      ON seal.intent_id = intent.id
                                     AND seal.order_id = intent.order_id
                                    WHERE intent.order_id = NEW.id
                                      AND intent.mode::text = 'live'
                                )
                                OR (
                                    NEW.status IN ('filled', 'partially_filled')
                                    AND NOT EXISTS (
                                        SELECT 1
                                        FROM public.execution_fill_legs AS leg
                                        JOIN public.execution_reconciliation_observations
                                          AS observation
                                          ON observation.id =
                                             leg.reconciliation_observation_id
                                         AND observation.order_id = leg.order_id
                                        WHERE leg.order_id = NEW.id
                                          AND leg.mode::text = 'live'
                                    )
                                )
                            ) THEN
                                RAISE EXCEPTION
                                    'LIVE terminal reconciliation lacks immutable venue evidence';
                            END IF;
                            RETURN NEW;
                        WHEN 'execution_order_intents' THEN
                            RAISE EXCEPTION
                                'PAPER canary history forbids LIVE order intents';
                        WHEN 'execution_fill_legs' THEN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM public.orders
                                WHERE id = NEW.order_id
                                  AND mode::text = 'live'
                            ) THEN
                                RAISE EXCEPTION
                                    'LIVE fill reconciliation lacks a LIVE order';
                            END IF;
                            RETURN NEW;
                        WHEN 'positions' THEN
                            IF NEW.order_id IS NULL OR NOT EXISTS (
                                SELECT 1
                                FROM public.orders
                                WHERE id = NEW.order_id
                                  AND mode::text = 'live'
                            ) THEN
                                RAISE EXCEPTION
                                    'LIVE position reconciliation lacks a LIVE order';
                            END IF;
                            SELECT count(*),
                                   COALESCE(sum(leg.shares), 0),
                                   COALESCE(sum(leg.notional_usd), 0),
                                   COALESCE(sum(COALESCE(leg.fee_usd, 0)), 0),
                                   CASE WHEN COALESCE(sum(leg.shares), 0) > 0
                                        THEN sum(leg.shares * leg.price) /
                                             sum(leg.shares)
                                        ELSE NULL END
                            INTO projected_leg_count,
                                 projected_shares,
                                 projected_notional,
                                 projected_fee,
                                 projected_avg_price
                            FROM public.execution_fill_legs AS leg
                            JOIN public.execution_reconciliation_observations AS observation
                              ON observation.id = leg.reconciliation_observation_id
                             AND observation.order_id = leg.order_id
                            WHERE leg.order_id = NEW.order_id
                              AND leg.mode::text = 'live';
                            IF projected_leg_count = 0
                               OR NEW.size_shares IS DISTINCT FROM
                                  round(projected_shares, 6)
                               OR NEW.size_usd IS DISTINCT FROM
                                  round(projected_notional, 4)
                               OR (
                                   NEW.fee_usd IS NOT NULL
                                   AND NEW.fee_usd IS DISTINCT FROM
                                       round(projected_fee, 5)
                               )
                               OR abs(NEW.avg_price - projected_avg_price) >
                                  0.000001 THEN
                                RAISE EXCEPTION
                                    'LIVE position reconciliation disagrees '
                                    'with immutable venue evidence';
                            END IF;
                            RETURN NEW;
                        ELSE
                            RAISE EXCEPTION
                                'unexpected LIVE execution fence relation: %',
                                TG_TABLE_NAME;
                    END CASE;
                END IF;
            END IF;

            CASE TG_TABLE_NAME
                WHEN 'signals' THEN
                    IF NEW.status::text = 'executed' THEN
                        IF EXISTS (
                            SELECT 1
                            FROM public.orders
                            WHERE signal_id = NEW.id
                              AND mode::text = 'live'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM public.orders
                            WHERE signal_id = NEW.id
                              AND mode::text = 'paper'
                        ) THEN
                            -- Risk-reducing reconciliation of a pre-existing
                            -- LIVE order may finish its legacy projection, but
                            -- only from immutable fill evidence.
                            IF NOT EXISTS (
                                SELECT 1
                                FROM public.orders AS order_row
                                WHERE order_row.signal_id = NEW.id
                                  AND order_row.mode::text = 'live'
                                  AND order_row.status IN (
                                      'filled', 'partially_filled'
                                  )
                                  AND (
                                      EXISTS (
                                          SELECT 1
                                          FROM public.execution_fill_legs AS leg
                                          WHERE leg.order_id = order_row.id
                                            AND leg.mode::text = 'live'
                                      )
                                      OR EXISTS (
                                          SELECT 1
                                          FROM public.positions AS position_row
                                          WHERE position_row.order_id = order_row.id
                                            AND position_row.mode::text = 'live'
                                            AND position_row.size_shares > 0
                                      )
                                  )
                            ) THEN
                                RAISE EXCEPTION
                                    'runtime LIVE executed signal lacks immutable fill projection';
                            END IF;
                            RETURN NEW;
                        ELSIF NOT EXISTS (
                            SELECT 1
                            FROM public.orders AS order_row
                            WHERE order_row.signal_id = NEW.id
                              AND order_row.mode::text = 'paper'
                              AND order_row.status IN (
                                  'filled', 'partially_filled'
                              )
                              AND EXISTS (
                                  SELECT 1
                                  FROM public.execution_fill_legs AS leg
                                  WHERE leg.order_id = order_row.id
                                    AND leg.mode::text = 'paper'
                              )
                        ) THEN
                            RAISE EXCEPTION
                                'runtime PAPER executed signal lacks immutable fill projection';
                        END IF;
                    ELSIF NEW.status::text IN ('expired', 'skipped')
                          AND EXISTS (
                              SELECT 1
                              FROM public.orders
                              WHERE signal_id = NEW.id
                                AND status NOT IN (
                                    'filled', 'partially_filled', 'canceled',
                                    'unfilled_maker', 'submit_failed',
                                    'rejected', 'expired'
                                )
                          ) THEN
                        RAISE EXCEPTION
                            'runtime signal cannot expire with nonterminal order inventory';
                    END IF;
                    exposure_bearing := (
                        TG_OP = 'INSERT'
                        AND NEW.status::text IN ('pending', 'executed')
                    ) OR (
                        TG_OP = 'UPDATE'
                        AND NEW.status::text IN ('pending', 'executed')
                        AND NEW.status IS DISTINCT FROM OLD.status
                    );
                WHEN 'orders' THEN
                    IF TG_OP = 'INSERT'
                       AND NEW.mode::text = 'paper'
                       AND (
                           SELECT count(*)
                           FROM public.execution_order_intents
                           WHERE order_id = NEW.id
                             AND signal_id = NEW.signal_id
                             AND mode::text = 'paper'
                       ) <> 1 THEN
                        RAISE EXCEPTION
                            'runtime PAPER order commit requires exactly one immutable intent';
                    END IF;
                    IF NEW.status IN (
                        'filled', 'partially_filled', 'canceled',
                        'unfilled_maker', 'submit_failed', 'rejected', 'expired'
                    ) THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM public.execution_cost_obligations
                            WHERE order_id = NEW.id
                              AND terminal_status = NEW.status
                        ) THEN
                            RAISE EXCEPTION
                                'runtime terminal order commit lacks its immutable obligation';
                        END IF;
                        IF NEW.status IN ('filled', 'partially_filled') THEN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM public.execution_fill_legs
                                WHERE order_id = NEW.id
                            ) THEN
                                RAISE EXCEPTION
                                    'runtime filled order commit lacks immutable fill legs';
                            END IF;
                        ELSIF NOT EXISTS (
                            SELECT 1
                            FROM public.execution_cost_seals
                            WHERE order_id = NEW.id
                        ) THEN
                            RAISE EXCEPTION
                                'runtime no-fill order commit lacks its immutable seal';
                        END IF;
                    END IF;
                    exposure_bearing := NEW.mode::text = 'paper' AND (
                        (
                            TG_OP = 'INSERT'
                        )
                        OR (
                            TG_OP = 'UPDATE'
                            AND (
                                NEW.size_usd > OLD.size_usd
                                OR NEW.filled_usd > OLD.filled_usd
                                OR (
                                    NEW.mode::text = 'paper'
                                    AND OLD.mode::text <> 'paper'
                                    AND NEW.size_usd > 0
                                )
                                OR (
                                    NEW.status IN (
                                        'paper_pending',
                                        'paper_resting',
                                        'partially_filled',
                                        'filled'
                                    )
                                    AND NEW.status IS DISTINCT FROM OLD.status
                                )
                            )
                        )
                    );
                WHEN 'execution_order_intents' THEN
                    exposure_bearing :=
                        TG_OP = 'INSERT' AND NEW.mode::text = 'paper';
                WHEN 'execution_fill_legs' THEN
                    exposure_bearing :=
                        TG_OP = 'INSERT' AND NEW.mode::text = 'paper';
                WHEN 'positions' THEN
                    SELECT * INTO projected_position
                    FROM public.positions
                    WHERE id = NEW.id;
                    IF projected_position.id IS NOT NULL
                       AND projected_position.mode::text = 'paper' THEN
                        IF TG_OP = 'INSERT' AND (
                            NEW.status::text <> 'open'
                            OR projected_position.status::text <> 'open'
                        ) THEN
                            RAISE EXCEPTION
                                'runtime PAPER position insert must remain '
                                'an open fill projection through commit';
                        END IF;
                        SELECT count(*) INTO projected_position_count
                        FROM public.positions AS position_row
                        WHERE position_row.market_id = projected_position.market_id
                          AND position_row.mode::text = 'paper'
                          AND position_row.side = projected_position.side
                          AND position_row.status::text = 'open'
                          AND position_row.benchmark_protocol =
                              projected_position.benchmark_protocol
                          AND position_row.token_id IS NOT DISTINCT FROM
                              projected_position.token_id;
                        SELECT count(*),
                               COALESCE(sum(leg.shares), 0),
                               COALESCE(sum(leg.notional_usd), 0),
                               COALESCE(sum(COALESCE(leg.fee_usd, 0)), 0),
                               CASE WHEN COALESCE(sum(leg.shares), 0) > 0
                                    THEN sum(leg.shares * leg.price) /
                                         sum(leg.shares)
                                    ELSE NULL END
                        INTO projected_leg_count,
                             projected_shares,
                             projected_notional,
                             projected_fee,
                             projected_avg_price
                        FROM public.execution_fill_legs AS leg
                        JOIN public.signals AS signal
                          ON signal.id = leg.signal_id
                        WHERE leg.market_id = projected_position.market_id
                          AND leg.mode::text = 'paper'
                          AND leg.side = projected_position.side
                          AND leg.token_id IS NOT DISTINCT FROM
                              projected_position.token_id
                          AND signal.benchmark_protocol =
                              projected_position.benchmark_protocol;
                        IF (
                               projected_position.status::text = 'open'
                               AND projected_position_count <> 1
                           )
                           OR projected_leg_count = 0
                           OR projected_position.size_shares IS DISTINCT FROM
                              round(projected_shares, 6)
                           OR projected_position.size_usd IS DISTINCT FROM
                              round(projected_notional + projected_fee, 4)
                           OR projected_position.fee_usd IS DISTINCT FROM
                              round(projected_fee, 5)
                           OR abs(
                               projected_position.avg_price
                               - projected_avg_price
                           ) > 0.000001 THEN
                            RAISE EXCEPTION
                                'runtime PAPER position commit disagrees '
                                'with immutable fill-leg projection';
                        END IF;
                    END IF;
                    exposure_bearing := NEW.mode::text = 'paper' AND (
                        TG_OP = 'INSERT'
                        OR (
                            TG_OP = 'UPDATE'
                            AND (
                                NEW.size_shares > OLD.size_shares
                                OR NEW.size_usd > OLD.size_usd
                                OR (
                                    NEW.mode::text = 'paper'
                                    AND OLD.mode::text <> 'paper'
                                    AND (
                                        NEW.size_shares > 0
                                        OR NEW.size_usd > 0
                                    )
                                )
                                OR (
                                    NEW.status::text = 'open'
                                    AND OLD.status::text <> 'open'
                                    AND (
                                        NEW.size_shares > 0
                                        OR NEW.size_usd > 0
                                    )
                                )
                            )
                        )
                    );
                ELSE
                    RAISE EXCEPTION
                        'unexpected PAPER execution commit fence relation: %',
                        TG_TABLE_NAME;
            END CASE;

            IF NOT exposure_bearing THEN
                RETURN NEW;
            END IF;

            IF current_setting('transaction_isolation') <> 'read committed' THEN
                RAISE EXCEPTION
                    'PAPER execution commit requires READ COMMITTED isolation';
            END IF;
            PERFORM pg_advisory_xact_lock_shared(6004519027736253267);
            IF NOT public.paper_canary_execution_authorized()
               OR NOT EXISTS (
                   SELECT 1
                   FROM public.runtime_control
                   WHERE id = 1
                     AND mode::text = 'paper'
                     AND NOT kill_switch
                     AND halted_reason IS NULL
                     AND live_confirmation_until IS NULL
               )
               OR NOT EXISTS (
                   SELECT 1
                   FROM public.venue_runtime_control
                   WHERE venue::text = 'polymarket'
                     AND enabled
                     AND mode::text = 'paper'
                     AND NOT kill_switch
                     AND halted_reason IS NULL
                     AND live_confirmation_until IS NULL
               )
               OR NOT EXISTS (
                   SELECT 1
                   FROM public.venue_runtime_control
                   WHERE venue::text = 'binance_spot'
                     AND enabled
                     AND mode::text = 'backtest'
                     AND kill_switch
                     AND live_confirmation_until IS NULL
               ) THEN
                RAISE EXCEPTION
                    'PAPER execution authority expired before commit';
            END IF;
            RETURN NEW;
        END
        $execution_commit_fence$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_paper_canary_signal_commit_fence
        AFTER INSERT OR UPDATE ON public.signals
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_commit_fence()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_paper_canary_order_commit_fence
        AFTER INSERT OR UPDATE ON public.orders
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_commit_fence()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_paper_canary_intent_commit_fence
        AFTER INSERT ON public.execution_order_intents
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_commit_fence()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_paper_canary_fill_leg_commit_fence
        AFTER INSERT ON public.execution_fill_legs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_commit_fence()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_paper_canary_position_commit_fence
        AFTER INSERT OR UPDATE ON public.positions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_commit_fence()
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.enforce_paper_canary_execution_commit_fence() FROM PUBLIC"
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_paper_canary_execution_identity_fence()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $execution_identity_fence$
        DECLARE
            runtime_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
            terminal_statuses constant text[] := ARRAY[
                'filled', 'partially_filled', 'canceled', 'unfilled_maker',
                'submit_failed', 'rejected', 'expired'
            ]::text[];
            resolved_yes boolean;
            attested_closed_at timestamptz;
            expected_pnl numeric;
            manifest_initial_bankroll numeric;
            expected_bankroll numeric;
        BEGIN
            IF session_user <> runtime_name THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            IF current_user <> runtime_name THEN
                -- Mirror the deferred commit fence's exact owner STOP shapes.
                -- Direct runtime DML still runs as runtime_name and therefore
                -- receives the complete identity/lifecycle validation below.
                IF current_user = 'stratus'::name
                   AND TG_OP = 'UPDATE'
                   AND TG_TABLE_NAME = 'signals'
                   AND OLD.status::text = 'pending'
                   AND NEW.status::text = 'expired'
                   AND NEW.skip_reason = 'kill_switch'
                   AND (
                       NEW.id,
                       NEW.market_id,
                       NEW.ts,
                       NEW.side,
                       NEW.model_prob,
                       NEW.market_price,
                       NEW.edge,
                       NEW.kelly_frac,
                       NEW.size_usd,
                       NEW.confidence,
                       NEW.clob_snapshot_id,
                       NEW.station_reliability_snapshot_id,
                       NEW.token_id,
                       NEW.benchmark_protocol
                   ) IS NOT DISTINCT FROM (
                       OLD.id,
                       OLD.market_id,
                       OLD.ts,
                       OLD.side,
                       OLD.model_prob,
                       OLD.market_price,
                       OLD.edge,
                       OLD.kelly_frac,
                       OLD.size_usd,
                       OLD.confidence,
                       OLD.clob_snapshot_id,
                       OLD.station_reliability_snapshot_id,
                       OLD.token_id,
                       OLD.benchmark_protocol
                   ) THEN
                    RETURN NEW;
                ELSIF current_user = 'stratus'::name
                      AND TG_OP = 'UPDATE'
                      AND TG_TABLE_NAME = 'orders'
                      AND OLD.mode::text = 'live'
                      AND OLD.status IN ('open', 'pending')
                      AND NEW.status = 'cancel_requested'
                      AND (
                          NEW.id,
                          NEW.signal_id,
                          NEW.mode,
                          NEW.clob_order_id,
                          NEW.token_id,
                          NEW.side,
                          NEW.price,
                          NEW.size_usd,
                          NEW.filled_usd,
                          NEW.fee_usd,
                          NEW.created_at,
                          NEW.filled_at
                      ) IS NOT DISTINCT FROM (
                          OLD.id,
                          OLD.signal_id,
                          OLD.mode,
                          OLD.clob_order_id,
                          OLD.token_id,
                          OLD.side,
                          OLD.price,
                          OLD.size_usd,
                          OLD.filled_usd,
                          OLD.fee_usd,
                          OLD.created_at,
                          OLD.filled_at
                      ) THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION
                    'execution identity mutation requires the exact runtime login';
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF TG_TABLE_NAME = 'signals' THEN
                    IF OLD.status::text IN ('skipped', 'expired')
                       AND NOT EXISTS (
                           SELECT 1 FROM public.orders
                           WHERE signal_id = OLD.id
                       ) THEN
                        RETURN OLD;
                    END IF;
                END IF;
                RAISE EXCEPTION
                    'runtime deletion of execution inventory is forbidden';
            END IF;
            CASE TG_TABLE_NAME
                WHEN 'signals' THEN
                    IF (
                        NEW.id,
                        NEW.market_id,
                        NEW.ts,
                        NEW.side,
                        NEW.model_prob,
                        NEW.market_price,
                        NEW.edge,
                        NEW.kelly_frac,
                        NEW.size_usd,
                        NEW.confidence,
                        NEW.clob_snapshot_id,
                        NEW.station_reliability_snapshot_id,
                        NEW.token_id,
                        NEW.benchmark_protocol
                    ) IS DISTINCT FROM (
                        OLD.id,
                        OLD.market_id,
                        OLD.ts,
                        OLD.side,
                        OLD.model_prob,
                        OLD.market_price,
                        OLD.edge,
                        OLD.kelly_frac,
                        OLD.size_usd,
                        OLD.confidence,
                        OLD.clob_snapshot_id,
                        OLD.station_reliability_snapshot_id,
                        OLD.token_id,
                        OLD.benchmark_protocol
                    ) THEN
                        RAISE EXCEPTION
                            'runtime signal decision or provenance is immutable';
                    END IF;
                    IF OLD.status::text <> 'pending'
                       AND NEW IS DISTINCT FROM OLD THEN
                        RAISE EXCEPTION
                            'runtime terminal signal is immutable';
                    END IF;
                    IF NEW.status IS DISTINCT FROM OLD.status THEN
                        IF NEW.status::text NOT IN (
                            'executed', 'expired', 'skipped'
                        ) THEN
                            RAISE EXCEPTION
                                'runtime signal lifecycle transition is invalid';
                        END IF;
                    END IF;
                WHEN 'orders' THEN
                    IF (
                        NEW.id,
                        NEW.signal_id,
                        NEW.mode,
                        NEW.token_id,
                        NEW.side,
                        NEW.price,
                        NEW.size_usd,
                        NEW.created_at
                    ) IS DISTINCT FROM (
                        OLD.id,
                        OLD.signal_id,
                        OLD.mode,
                        OLD.token_id,
                        OLD.side,
                        OLD.price,
                        OLD.size_usd,
                        OLD.created_at
                    ) THEN
                        RAISE EXCEPTION
                            'runtime order identity is immutable';
                    END IF;
                    IF OLD.clob_order_id IS NOT NULL
                       AND NEW.clob_order_id IS DISTINCT FROM OLD.clob_order_id THEN
                        RAISE EXCEPTION
                            'runtime order venue identity is immutable once observed';
                    END IF;
                    IF NEW.mode::text = 'paper' THEN
                        IF NEW.status NOT IN (
                            'paper_pending', 'paper_resting', 'filled',
                            'partially_filled', 'unfilled_maker',
                            'rejected', 'expired'
                        ) THEN
                            RAISE EXCEPTION
                                'runtime PAPER order status is invalid';
                        END IF;
                        IF NEW.status IS DISTINCT FROM OLD.status
                           AND NOT (
                               OLD.status IN ('paper_pending', 'paper_resting')
                               AND NEW.status IN (
                                   'filled', 'partially_filled',
                                   'unfilled_maker', 'rejected', 'expired'
                               )
                           ) THEN
                            RAISE EXCEPTION
                                'runtime PAPER order lifecycle transition is invalid';
                        END IF;
                    ELSIF NEW.status NOT IN (
                        'submitting', 'open', 'pending', 'live',
                        'submit_unconfirmed', 'cancel_requested',
                        'filled', 'partially_filled', 'canceled',
                        'submit_failed', 'rejected', 'expired'
                    ) THEN
                        RAISE EXCEPTION
                            'runtime LIVE order status is invalid';
                    END IF;
                    IF NEW.filled_usd < OLD.filled_usd
                       OR (
                           OLD.fee_usd IS NOT NULL
                           AND (
                               NEW.fee_usd IS NULL
                               OR NEW.fee_usd < OLD.fee_usd
                           )
                       ) THEN
                        RAISE EXCEPTION
                            'runtime order cumulative amounts cannot decrease';
                    END IF;
                    IF OLD.status = ANY(terminal_statuses)
                       AND NEW IS DISTINCT FROM OLD THEN
                        RAISE EXCEPTION
                            'runtime terminal order outcome is immutable';
                    END IF;
                    IF NEW.status = ANY(terminal_statuses)
                       AND NEW.status IS DISTINCT FROM OLD.status THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM public.execution_order_intents
                            WHERE order_id = OLD.id
                        ) THEN
                            RAISE EXCEPTION
                                'runtime order terminalization requires immutable execution intent';
                        END IF;
                        IF NEW.status IN ('filled', 'partially_filled') THEN
                            IF NEW.filled_usd <= 0 OR NEW.filled_at IS NULL THEN
                                RAISE EXCEPTION
                                    'runtime filled order terminalization lacks fill facts';
                            END IF;
                        ELSIF NEW.filled_usd <> 0 OR NEW.filled_at IS NOT NULL THEN
                            RAISE EXCEPTION
                                'runtime no-fill order terminalization claims fill facts';
                        END IF;
                    END IF;
                WHEN 'positions' THEN
                    IF (
                        NEW.id,
                        NEW.market_id,
                        NEW.order_id,
                        NEW.mode,
                        NEW.side,
                        NEW.token_id,
                        NEW.benchmark_protocol
                    ) IS DISTINCT FROM (
                        OLD.id,
                        OLD.market_id,
                        OLD.order_id,
                        OLD.mode,
                        OLD.side,
                        OLD.token_id,
                        OLD.benchmark_protocol
                    ) THEN
                        RAISE EXCEPTION
                            'runtime position identity is immutable';
                    END IF;
                    IF NEW.size_shares < OLD.size_shares
                       OR NEW.size_usd < OLD.size_usd
                       OR (
                           OLD.fee_usd IS NOT NULL
                           AND (
                               NEW.fee_usd IS NULL
                               OR NEW.fee_usd < OLD.fee_usd
                           )
                       ) THEN
                        RAISE EXCEPTION
                            'runtime position cumulative amounts cannot decrease';
                    END IF;
                    IF OLD.status::text = 'closed'
                       AND NEW IS DISTINCT FROM OLD THEN
                        RAISE EXCEPTION
                            'runtime closed position is immutable';
                    END IF;
                    IF NEW.status IS DISTINCT FROM OLD.status THEN
                        IF OLD.status::text <> 'open'
                           OR NEW.status::text <> 'closed'
                           OR NEW.pnl_usd IS NULL
                           OR NEW.closed_at IS NULL
                           OR NEW.market_closed_at IS NULL
                           OR NEW.closed_at < NEW.market_closed_at
                           OR (
                               SELECT count(*)
                               FROM public.markets AS market
                               JOIN public.gamma_outcome_events AS event
                                 ON event.id = market.resolution_event_id
                                AND event.event_id = market.event_id
                               CROSS JOIN LATERAL jsonb_array_elements(
                                   event.outcome_vector
                               ) AS item
                               WHERE market.id = OLD.market_id
                                 AND market.status::text = 'resolved'
                                 AND market.resolved_outcome IS NOT NULL
                                 AND (item->>'market_id')::bigint = market.id
                                 AND (item->>'outcome_yes')::boolean =
                                     market.resolved_outcome
                           ) <> 1 THEN
                            RAISE EXCEPTION
                                'runtime position close lacks resolved-market settlement evidence';
                        END IF;
                        SELECT market.resolved_outcome,
                               (item->>'closed_at')::timestamptz
                        INTO STRICT resolved_yes, attested_closed_at
                        FROM public.markets AS market
                        JOIN public.gamma_outcome_events AS event
                          ON event.id = market.resolution_event_id
                         AND event.event_id = market.event_id
                        CROSS JOIN LATERAL jsonb_array_elements(
                            event.outcome_vector
                        ) AS item
                        WHERE market.id = OLD.market_id
                          AND (item->>'market_id')::bigint = market.id;
                        expected_pnl := round(
                            (
                                CASE
                                    WHEN (
                                        OLD.side::text = 'buy_yes'
                                        AND resolved_yes
                                    ) OR (
                                        OLD.side::text = 'buy_no'
                                        AND NOT resolved_yes
                                    ) THEN OLD.size_shares
                                    ELSE 0
                                END
                            ) - OLD.size_usd,
                            4
                        );
                        IF NEW.pnl_usd IS DISTINCT FROM expected_pnl
                           OR NEW.market_closed_at IS DISTINCT FROM
                              attested_closed_at
                           OR NEW.closed_at < attested_closed_at THEN
                            RAISE EXCEPTION
                                'runtime position close disagrees with '
                                'immutable settlement projection';
                        END IF;
                    END IF;
                WHEN 'bankroll_log' THEN
                    IF TG_OP <> 'INSERT' THEN
                        RAISE EXCEPTION
                            'runtime bankroll history is append-only';
                    END IF;
                    IF NEW.mode::text <> 'paper'
                       OR NEW.event !~ '^settled [1-9][0-9]* paper position\\(s\\)$' THEN
                        RAISE EXCEPTION
                            'runtime bankroll append lacks PAPER settlement identity';
                    END IF;
                    SELECT (
                        lane->'manifest'->'config'->>
                        'initial_bankroll_usd'
                    )::numeric
                    INTO manifest_initial_bankroll
                    FROM public.probability_pipeline_release_epochs AS epoch
                    JOIN public.probability_pipeline_release_plans AS plan
                      ON plan.id = epoch.plan_id
                    CROSS JOIN LATERAL jsonb_array_elements(
                        plan.lane_plans
                    ) AS lane
                    WHERE lane->>'lane_name' = 'operational_legacy'
                    ORDER BY epoch.id DESC
                    LIMIT 1;
                    SELECT round(
                        manifest_initial_bankroll
                        + COALESCE(sum(position_row.pnl_usd), 0),
                        4
                    )
                    INTO expected_bankroll
                    FROM public.positions AS position_row
                    WHERE position_row.mode::text = 'paper'
                      AND position_row.status::text = 'closed';
                    IF manifest_initial_bankroll IS NULL
                       OR NEW.balance_usd IS DISTINCT FROM expected_bankroll
                       OR EXISTS (
                           SELECT 1
                           FROM public.positions AS position_row
                           WHERE position_row.mode::text = 'paper'
                             AND position_row.status::text = 'closed'
                             AND (
                                 position_row.pnl_usd IS NULL
                                 OR position_row.closed_at IS NULL
                                 OR position_row.closed_at > NEW.ts
                             )
                       ) THEN
                        RAISE EXCEPTION
                            'runtime bankroll append disagrees with '
                            'immutable settlement projection';
                    END IF;
                ELSE
                    RAISE EXCEPTION
                        'unexpected execution identity fence relation: %',
                        TG_TABLE_NAME;
            END CASE;
            RETURN NEW;
        END
        $execution_identity_fence$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_bankroll_identity_fence
        BEFORE INSERT OR UPDATE OR DELETE ON public.bankroll_log
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_identity_fence()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_signal_identity_fence
        BEFORE UPDATE OR DELETE ON public.signals
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_identity_fence()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_order_identity_fence
        BEFORE UPDATE OR DELETE ON public.orders
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_identity_fence()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_position_identity_fence
        BEFORE UPDATE OR DELETE ON public.positions
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_execution_identity_fence()
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.enforce_paper_canary_execution_identity_fence() FROM PUBLIC"
    )
    op.execute(
        """
        CREATE FUNCTION public.paper_canary_stop_request_reserved(
            requested_id text
        )
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $reservation$
            SELECT EXISTS (
                SELECT 1
                FROM public.paper_canary_safety_leases
                WHERE requested_id IN (
                      acquire_request_id,
                      resume_request_id,
                      release_request_id,
                      watchdog_stop_request_id,
                      direct_stop_request_id
                  )
            )
        $reservation$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.paper_canary_resume_request_reserved(
            requested_id text
        )
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $reservation$
            SELECT EXISTS (
                SELECT 1
                FROM public.paper_canary_safety_leases
                WHERE resume_request_id = requested_id
            )
        $reservation$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.paper_canary_resume_command_authorized(
            requested_id text,
            requested_actor text,
            requested_reason text
        )
        RETURNS boolean
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $reservation$
            SELECT EXISTS (
                SELECT 1
                FROM public.paper_canary_safety_leases
                WHERE state = 'active'
                  AND resume_request_id = requested_id
                  AND resume_actor = requested_actor
                  AND resume_reason = requested_reason
                  AND expires_at > clock_timestamp()
                  AND watchdog_at > acquired_at
            )
        $reservation$;
        """
    )
    for signature in (
        "public.paper_canary_stop_request_reserved(text)",
        "public.paper_canary_resume_request_reserved(text)",
        "public.paper_canary_resume_command_authorized(text, text, text)",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        """
        CREATE FUNCTION public.enforce_paper_canary_request_reservation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $request_reservation$
        DECLARE
            app_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            stop_effect_exact boolean := false;
        BEGIN
            IF current_user = safety_app_name THEN
                RAISE EXCEPTION
                    'safety login cannot enqueue or mutate operator commands';
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF current_user <> 'stratus'::name THEN
                    RAISE EXCEPTION
                        'operator command audit rows cannot be deleted';
                END IF;
                RETURN OLD;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.request_id IS DISTINCT FROM OLD.request_id
                   OR NEW.command IS DISTINCT FROM OLD.command
                   OR NEW.payload IS DISTINCT FROM OLD.payload
                   OR NEW.actor IS DISTINCT FROM OLD.actor
                   OR NEW.reason IS DISTINCT FROM OLD.reason
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION
                        'operator command audit identity is immutable';
                END IF;
                IF OLD.command = 'stop'
                   AND OLD.status = 'pending'
                   AND session_user = app_name THEN
                    IF NEW.status <> 'succeeded' THEN
                        RAISE EXCEPTION
                            'runtime pending STOP may only transition to succeeded';
                    END IF;
                    -- Binance is the runtime-owned seal. Polymarket and all
                    -- must terminalize inside the owner SECURITY DEFINER
                    -- primitive so cleanup and lease revocation stay atomic.
                    IF OLD.payload = '{"venue":"binance_spot"}'::jsonb THEN
                        stop_effect_exact := current_user = app_name AND EXISTS (
                            SELECT 1
                            FROM public.venue_runtime_control AS binance
                            WHERE binance.venue::text = 'binance_spot'
                              AND binance.enabled
                              AND binance.mode::text = 'backtest'
                              AND binance.kill_switch
                              AND binance.live_confirmation_until IS NULL
                              AND binance.xmin = pg_current_xact_id()::xid
                        );
                    ELSIF OLD.payload = '{"venue":"polymarket"}'::jsonb THEN
                        stop_effect_exact := current_user = 'stratus'::name AND EXISTS (
                            SELECT 1
                            FROM public.runtime_control AS runtime
                            WHERE runtime.id = 1
                              AND runtime.mode::text = 'paper'
                              AND runtime.kill_switch
                              AND runtime.live_confirmation_until IS NULL
                              AND runtime.xmin = pg_current_xact_id()::xid
                        ) AND EXISTS (
                            SELECT 1
                            FROM public.venue_runtime_control AS polymarket
                            WHERE polymarket.venue::text = 'polymarket'
                              AND polymarket.enabled
                              AND polymarket.mode::text = 'paper'
                              AND polymarket.kill_switch
                              AND polymarket.live_confirmation_until IS NULL
                              AND polymarket.xmin = pg_current_xact_id()::xid
                        );
                    ELSIF OLD.payload IN (
                        '{}'::jsonb,
                        '{"venue":"all"}'::jsonb
                    ) THEN
                        stop_effect_exact := current_user = 'stratus'::name AND EXISTS (
                            SELECT 1
                            FROM public.runtime_control AS runtime
                            WHERE runtime.id = 1
                              AND runtime.mode::text = 'paper'
                              AND runtime.kill_switch
                              AND runtime.live_confirmation_until IS NULL
                              AND runtime.xmin = pg_current_xact_id()::xid
                        ) AND EXISTS (
                            SELECT 1
                            FROM public.venue_runtime_control AS polymarket
                            WHERE polymarket.venue::text = 'polymarket'
                              AND polymarket.enabled
                              AND polymarket.mode::text = 'paper'
                              AND polymarket.kill_switch
                              AND polymarket.live_confirmation_until IS NULL
                              AND polymarket.xmin = pg_current_xact_id()::xid
                        ) AND EXISTS (
                            SELECT 1
                            FROM public.venue_runtime_control AS binance
                            WHERE binance.venue::text = 'binance_spot'
                              AND binance.enabled
                              AND binance.mode::text = 'backtest'
                              AND binance.kill_switch
                              AND binance.live_confirmation_until IS NULL
                              AND binance.xmin = pg_current_xact_id()::xid
                        );
                    END IF;
                    IF NOT stop_effect_exact THEN
                        RAISE EXCEPTION
                            'runtime STOP succeeded before its exact database control effect';
                    END IF;
                END IF;
                IF OLD.status <> 'pending' THEN
                    RAISE EXCEPTION
                        'terminal operator command audit is immutable';
                END IF;
                IF NEW.status NOT IN (
                    'succeeded', 'rejected', 'failed'
                )
                   OR NEW.detail IS NULL
                   OR btrim(NEW.detail) = ''
                   OR NEW.executed_at IS NULL
                   OR NEW.executed_at < NEW.created_at THEN
                    RAISE EXCEPTION
                        'operator command lifecycle transition is invalid';
                END IF;
                IF current_user <> 'stratus'::name THEN
                    NEW.executed_at := clock_timestamp();
                END IF;
                RETURN NEW;
            END IF;

            IF current_user <> 'stratus'::name THEN
                IF NEW.status <> 'pending'
                   OR NEW.detail IS NOT NULL
                   OR NEW.executed_at IS NOT NULL THEN
                    RAISE EXCEPTION
                        'new runtime operator command must be pending';
                END IF;
                NEW.created_at := clock_timestamp();
            END IF;

            IF public.paper_canary_stop_request_reserved(NEW.request_id)
               AND current_user <> 'stratus'::name
               AND NOT (
                   current_user <> safety_app_name
                   AND NEW.command = 'resume'
                   AND NEW.payload = '{"venue":"polymarket"}'::jsonb
                   AND public.paper_canary_resume_command_authorized(
                       NEW.request_id,
                       NEW.actor,
                       NEW.reason
                   )
               ) THEN
                RAISE EXCEPTION
                    'operator command collides with reserved canary request identity';
            END IF;
            IF public.paper_canary_resume_request_reserved(NEW.request_id)
               AND NOT (
                   current_user <> safety_app_name
                   AND NEW.command = 'resume'
                   AND NEW.payload = '{"venue":"polymarket"}'::jsonb
                   AND public.paper_canary_resume_command_authorized(
                       NEW.request_id,
                       NEW.actor,
                       NEW.reason
                   )
               ) THEN
                RAISE EXCEPTION
                    'operator command collides with reserved canary request identity';
            END IF;
            IF NEW.command = 'resume'
               AND NEW.payload = '{"venue":"polymarket"}'::jsonb THEN
                IF current_user = safety_app_name THEN
                    RAISE EXCEPTION
                        'safety login cannot enqueue Polymarket PAPER resume';
                END IF;
                IF NOT public.paper_canary_resume_command_authorized(
                    NEW.request_id,
                    NEW.actor,
                    NEW.reason
                ) THEN
                    RAISE EXCEPTION
                        'Polymarket PAPER resume is not reserved by a live observed lease';
                END IF;
            END IF;
            RETURN NEW;
        END
        $request_reservation$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_canary_request_reservation
        BEFORE INSERT OR UPDATE OR DELETE ON public.operator_commands
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_paper_canary_request_reservation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.engage_paper_canary_runtime_stop(
            requested_id text,
            requested_actor text,
            requested_reason text
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $runtime_stop$
        DECLARE
            app_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            lease_row public.paper_canary_safety_leases%ROWTYPE;
            caller_command public.operator_commands%ROWTYPE;
            database_now timestamptz := clock_timestamp();
            expired_count integer := 0;
            affected integer;
        BEGIN
            IF session_user NOT IN (
                'stratus'::name, app_name, safety_app_name
            ) THEN
                RAISE EXCEPTION
                    'PAPER stop requires a STRATUS control-plane login';
            END IF;
            PERFORM pg_advisory_xact_lock(6004519027736253267);
            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE state = 'active'
            FOR UPDATE;

            IF public.paper_canary_stop_request_reserved(requested_id) THEN
                RAISE EXCEPTION
                    'PAPER stop audit identity collision with reserved canary request identity';
            END IF;

            INSERT INTO public.operator_commands(
                request_id,
                command,
                payload,
                actor,
                reason
            ) VALUES (
                requested_id,
                'stop',
                '{"venue":"polymarket"}'::jsonb,
                requested_actor,
                requested_reason
            )
            ON CONFLICT (request_id) DO NOTHING;
            SELECT * INTO caller_command
            FROM public.operator_commands
            WHERE request_id = requested_id
            FOR UPDATE;
            IF NOT FOUND
               OR caller_command.command <> 'stop'
               OR caller_command.payload NOT IN (
                   '{}'::jsonb,
                   '{"venue":"polymarket"}'::jsonb,
                   '{"venue":"all"}'::jsonb
               )
               OR caller_command.actor IS DISTINCT FROM requested_actor
               OR caller_command.reason IS DISTINCT FROM requested_reason
               OR caller_command.status NOT IN ('pending', 'succeeded') THEN
                RAISE EXCEPTION 'PAPER stop audit identity collision';
            END IF;
            IF lease_row.id IS NOT NULL
               AND (
                   caller_command.created_at < lease_row.acquired_at
                   OR (
                       caller_command.status = 'succeeded'
                       AND (
                           caller_command.executed_at IS NULL
                           OR caller_command.executed_at < lease_row.acquired_at
                       )
                   )
                ) THEN
                RAISE EXCEPTION
                    'PAPER stop audit identity collision: audit predates the active canary lease';
            END IF;

            UPDATE public.runtime_control
            SET kill_switch = true,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE id = 1;
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 1 THEN
                RAISE EXCEPTION 'PAPER stop runtime control update failed';
            END IF;
            UPDATE public.venue_runtime_control
            SET kill_switch = true,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE venue::text = 'polymarket';
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 1 THEN
                RAISE EXCEPTION 'PAPER stop venue control update failed';
            END IF;
            -- Cleanup is best effort inside the critical STOP transaction.
            -- A bounded candidate window caps work; SKIP LOCKED ensures that
            -- application row locks can never delay controls, audit, or lease
            -- terminalization.  The return count is rows claimed by this
            -- invocation, and an exact idempotent replay converges leftovers.
            WITH claimed AS MATERIALIZED (
                SELECT candidate_signal.id
                FROM public.signals AS candidate_signal
                WHERE candidate_signal.status = 'pending'::signal_status
                ORDER BY candidate_signal.id
                FOR UPDATE OF candidate_signal SKIP LOCKED
                LIMIT 512
            )
            UPDATE public.signals AS target
            SET status = 'expired'::signal_status,
                skip_reason = 'kill_switch'
            FROM claimed
            WHERE target.id = claimed.id;
            GET DIAGNOSTICS expired_count = ROW_COUNT;
            WITH claimed AS MATERIALIZED (
                SELECT candidate_order.id
                FROM public.orders AS candidate_order
                WHERE candidate_order.mode = 'live'::execution_mode
                  AND candidate_order.status IN ('open', 'pending')
                ORDER BY candidate_order.id
                FOR UPDATE OF candidate_order SKIP LOCKED
                LIMIT 512
            )
            UPDATE public.orders AS target
            SET status = 'cancel_requested'
            FROM claimed
            WHERE target.id = claimed.id;

            IF lease_row.id IS NOT NULL THEN
                UPDATE public.operator_commands
                SET status = 'rejected',
                    detail = (
                        'Polymarket PAPER resume rejected because its safety '
                        'lease was revoked by an emergency stop.'
                    ),
                    executed_at = database_now
                WHERE request_id = lease_row.resume_request_id
                  AND status = 'pending';

                UPDATE public.paper_canary_safety_leases
                SET state = 'revoked_stopped',
                    stop_command_id = caller_command.id
                WHERE id = lease_row.id
                  AND state = 'active';
                GET DIAGNOSTICS affected = ROW_COUNT;
                IF affected <> 1 THEN
                    RAISE EXCEPTION
                        'PAPER stop lease revocation failed';
                END IF;
            END IF;
            IF caller_command.status = 'pending' THEN
                UPDATE public.operator_commands
                SET status = 'succeeded',
                    detail = (
                        'Atomic runtime Polymarket emergency stop succeeded.'
                    ),
                    executed_at = database_now
                WHERE id = caller_command.id;
            END IF;
            RETURN expired_count;
        END
        $runtime_stop$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.resume_paper_canary_exact(
            requested_command_id bigint
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $exact_resume$
        DECLARE
            app_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
            lease_row public.paper_canary_safety_leases%ROWTYPE;
            command_row public.operator_commands%ROWTYPE;
            runtime_row public.runtime_control%ROWTYPE;
            venue_row public.venue_runtime_control%ROWTYPE;
            database_now timestamptz;
            affected integer;
        BEGIN
            IF session_user NOT IN ('stratus'::name, app_name) THEN
                RAISE EXCEPTION
                    'exact PAPER resume requires the STRATUS runtime login';
            END IF;
            PERFORM pg_advisory_xact_lock(6004519027736253267);
            database_now := clock_timestamp();
            SELECT * INTO command_row
            FROM public.operator_commands
            WHERE id = requested_command_id
            FOR UPDATE;
            IF NOT FOUND
               OR command_row.command <> 'resume'
               OR command_row.payload <>
                   '{"venue":"polymarket"}'::jsonb
               OR command_row.status <> 'pending' THEN
                RETURN false;
            END IF;

            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE state = 'active'
              AND resume_request_id = command_row.request_id
            FOR UPDATE;
            IF NOT FOUND
               OR lease_row.resume_actor IS DISTINCT FROM command_row.actor
               OR lease_row.resume_reason IS DISTINCT FROM command_row.reason
               OR lease_row.expires_at <= database_now
               OR lease_row.watchdog_at <= lease_row.acquired_at
               OR lease_row.watchdog_at < (
                   database_now
                   - interval '7 seconds'
               ) THEN
                RETURN false;
            END IF;

            SELECT * INTO runtime_row
            FROM public.runtime_control
            WHERE id = 1
            FOR UPDATE;
            SELECT * INTO venue_row
            FROM public.venue_runtime_control
            WHERE venue::text = 'polymarket'
            FOR UPDATE;
            IF runtime_row.id IS NULL
               OR venue_row.venue IS NULL
               OR runtime_row.mode::text <> 'paper'
               OR runtime_row.halted_reason IS NOT NULL
               OR runtime_row.live_confirmation_until IS NOT NULL
               OR NOT runtime_row.kill_switch
               OR NOT venue_row.enabled
               OR venue_row.mode::text <> 'paper'
               OR NOT venue_row.kill_switch
               OR venue_row.halted_reason IS NOT NULL
               OR venue_row.live_confirmation_until IS NOT NULL
               OR EXISTS (
                   SELECT 1
                   FROM public.signals
                   WHERE status = 'pending'::signal_status
               )
               OR EXISTS (
                   SELECT 1
                   FROM public.orders
                   WHERE mode::text = 'live'
               )
               OR EXISTS (
                   SELECT 1
                   FROM public.positions
                   WHERE mode::text = 'live'
               )
               OR EXISTS (
                   SELECT 1
                   FROM public.execution_order_intents
                   WHERE mode::text = 'live'
               ) THEN
                RETURN false;
            END IF;

            UPDATE public.runtime_control
            SET kill_switch = false,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE id = 1;
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 1 THEN
                RAISE EXCEPTION
                    'exact PAPER resume runtime control update failed';
            END IF;
            UPDATE public.venue_runtime_control
            SET kill_switch = false,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE venue::text = 'polymarket';
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 1 THEN
                RAISE EXCEPTION
                    'exact PAPER resume venue control update failed';
            END IF;
            UPDATE public.operator_commands
            SET status = 'succeeded',
                detail = (
                    'Polymarket PAPER kill switches disabled by exact '
                    'database lease authority.'
                ),
                executed_at = database_now
            WHERE id = command_row.id
              AND status = 'pending';
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 1 THEN
                RAISE EXCEPTION
                    'exact PAPER resume command consumption failed';
            END IF;
            RETURN true;
        END
        $exact_resume$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.paper_canary_stop_core(
            requested_id text,
            requested_actor text,
            requested_reason text,
            lease_terminal_state text
        )
        RETURNS TABLE(
            command_id bigint,
            result_created boolean,
            result_idempotent boolean,
            expired_signals integer,
            cancel_requested_live_orders integer,
            rejected_pending_resume integer
        )
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $stop_core$
        DECLARE
            lease_row public.paper_canary_safety_leases%ROWTYPE;
            historical_lease public.paper_canary_safety_leases%ROWTYPE;
            historical_stop public.operator_commands%ROWTYPE;
            caller_command public.operator_commands%ROWTYPE;
            database_now timestamptz := clock_timestamp();
            inserted_id bigint;
            affected integer;
            expected_request_id text;
        BEGIN
            IF current_user <> 'stratus'::name THEN
                RAISE EXCEPTION
                    'PAPER stop core is owner-internal';
            END IF;
            IF lease_terminal_state NOT IN (
                'released',
                'expired_stopped',
                'revoked_stopped'
            ) THEN
                RAISE EXCEPTION
                    'invalid PAPER stop lease terminal state';
            END IF;
            PERFORM pg_advisory_xact_lock(6004519027736253267);
            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE state = 'active'
            FOR UPDATE;

            rejected_pending_resume := 0;
            IF lease_row.id IS NOT NULL THEN
                expected_request_id := CASE lease_terminal_state
                    WHEN 'released' THEN lease_row.release_request_id
                    WHEN 'expired_stopped' THEN
                        lease_row.watchdog_stop_request_id
                    ELSE NULL
                END;
                IF expected_request_id IS NOT NULL
                   AND requested_id IS DISTINCT FROM expected_request_id THEN
                    RAISE EXCEPTION
                        'PAPER stop request does not match the reserved lease identity';
                END IF;
                IF lease_terminal_state = 'revoked_stopped'
                   AND requested_id IS DISTINCT FROM
                       lease_row.direct_stop_request_id
                   AND public.paper_canary_stop_request_reserved(requested_id)
                THEN
                    RAISE EXCEPTION
                        'PAPER stop audit identity collision with reserved canary request identity';
                END IF;
                UPDATE public.operator_commands
                SET status = 'rejected',
                    detail = (
                        'Polymarket PAPER resume rejected because its safety '
                        'lease was terminalized by an authenticated safety stop.'
                    ),
                    executed_at = database_now
                WHERE request_id = lease_row.resume_request_id
                  AND status = 'pending';
                GET DIAGNOSTICS rejected_pending_resume = ROW_COUNT;
            ELSIF public.paper_canary_stop_request_reserved(requested_id) THEN
                SELECT * INTO historical_lease
                FROM public.paper_canary_safety_leases
                WHERE state IN (
                    'released',
                    'expired_stopped',
                    'expired_safe',
                    'revoked_stopped'
                )
                  AND direct_stop_request_id = requested_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'reserved PAPER stop audit identity collision';
                END IF;
                IF historical_lease.state = 'expired_safe' THEN
                    IF historical_lease.stop_command_id IS NOT NULL THEN
                        RAISE EXCEPTION
                            'reserved PAPER stop audit identity collision';
                    END IF;
                ELSE
                    SELECT * INTO historical_stop
                    FROM public.operator_commands
                    WHERE id = historical_lease.stop_command_id
                    FOR UPDATE;
                    IF NOT FOUND
                       OR historical_stop.command <> 'stop'
                       OR historical_stop.payload <>
                          '{"venue":"polymarket"}'::jsonb
                       OR historical_stop.status <> 'succeeded'
                       OR historical_stop.created_at < historical_lease.acquired_at
                       OR historical_stop.executed_at IS NULL
                       OR historical_stop.executed_at < historical_lease.acquired_at
                       OR (
                           historical_lease.state = 'released'
                           AND historical_stop.request_id IS DISTINCT FROM
                               historical_lease.release_request_id
                       )
                       OR (
                           historical_lease.state = 'expired_stopped'
                           AND historical_stop.request_id IS DISTINCT FROM
                               historical_lease.watchdog_stop_request_id
                       )
                       OR (
                           historical_stop.request_id = requested_id
                           AND (
                               historical_stop.actor IS DISTINCT FROM requested_actor
                               OR historical_stop.reason IS DISTINCT FROM requested_reason
                           )
                    ) THEN
                        RAISE EXCEPTION
                            'reserved PAPER stop audit identity collision';
                    END IF;
                END IF;
            END IF;

            inserted_id := NULL;
            INSERT INTO public.operator_commands(
                request_id,
                command,
                payload,
                actor,
                reason
            ) VALUES (
                requested_id,
                'stop',
                '{"venue":"polymarket"}'::jsonb,
                requested_actor,
                requested_reason
            )
            ON CONFLICT (request_id) DO NOTHING
            RETURNING id INTO inserted_id;
            result_created := inserted_id IS NOT NULL;

            SELECT * INTO caller_command
            FROM public.operator_commands
            WHERE request_id = requested_id
            FOR UPDATE;
            IF NOT FOUND
               OR caller_command.command <> 'stop'
               OR caller_command.payload <>
                  '{"venue":"polymarket"}'::jsonb
               OR caller_command.actor IS DISTINCT FROM requested_actor
               OR caller_command.reason IS DISTINCT FROM requested_reason
               OR caller_command.status NOT IN ('pending', 'succeeded') THEN
                RAISE EXCEPTION 'PAPER stop audit identity collision';
            END IF;
            IF lease_row.id IS NOT NULL
               AND (
                   caller_command.created_at < lease_row.acquired_at
                   OR (
                       caller_command.status = 'succeeded'
                       AND (
                           caller_command.executed_at IS NULL
                           OR caller_command.executed_at < lease_row.acquired_at
                       )
                   )
                ) THEN
                RAISE EXCEPTION
                    'PAPER stop audit identity collision: audit predates the active canary lease';
            END IF;
            result_idempotent := (
                NOT result_created
                AND caller_command.status = 'succeeded'
            );

            UPDATE public.runtime_control
            SET kill_switch = true,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE id = 1;
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 1 THEN
                RAISE EXCEPTION 'PAPER stop runtime control update failed';
            END IF;
            UPDATE public.venue_runtime_control
            SET kill_switch = true,
                live_confirmation_until = NULL,
                updated_at = database_now
            WHERE venue::text = 'polymarket';
            GET DIAGNOSTICS affected = ROW_COUNT;
            IF affected <> 1 THEN
                RAISE EXCEPTION 'PAPER stop venue control update failed';
            END IF;
            -- Counts are rows claimed during this bounded attempt, not the
            -- total historical cleanup.  Exact succeeded STOP replay runs the
            -- same nonblocking batches again and therefore converges.
            WITH claimed AS MATERIALIZED (
                SELECT candidate_signal.id
                FROM public.signals AS candidate_signal
                WHERE candidate_signal.status = 'pending'::signal_status
                ORDER BY candidate_signal.id
                FOR UPDATE OF candidate_signal SKIP LOCKED
                LIMIT 512
            )
            UPDATE public.signals AS target
            SET status = 'expired'::signal_status,
                skip_reason = 'kill_switch'
            FROM claimed
            WHERE target.id = claimed.id;
            GET DIAGNOSTICS expired_signals = ROW_COUNT;
            WITH claimed AS MATERIALIZED (
                SELECT candidate_order.id
                FROM public.orders AS candidate_order
                WHERE candidate_order.mode = 'live'::execution_mode
                  AND candidate_order.status IN ('open', 'pending')
                ORDER BY candidate_order.id
                FOR UPDATE OF candidate_order SKIP LOCKED
                LIMIT 512
            )
            UPDATE public.orders AS target
            SET status = 'cancel_requested'
            FROM claimed
            WHERE target.id = claimed.id;
            GET DIAGNOSTICS cancel_requested_live_orders = ROW_COUNT;

            IF lease_row.id IS NOT NULL THEN
                UPDATE public.paper_canary_safety_leases
                SET state = lease_terminal_state,
                    released_at = CASE
                        WHEN lease_terminal_state = 'released'
                        THEN database_now
                        ELSE NULL
                    END,
                    stop_command_id = caller_command.id
                WHERE id = lease_row.id
                  AND state = 'active';
                GET DIAGNOSTICS affected = ROW_COUNT;
                IF affected <> 1 THEN
                    RAISE EXCEPTION
                        'PAPER stop lease terminalization failed';
                END IF;
            END IF;
            IF caller_command.status = 'pending' THEN
                UPDATE public.operator_commands
                SET status = 'succeeded',
                    detail = (
                        'Atomic Polymarket safety stop succeeded; global and '
                        'Polymarket kill switches ON.'
                    ),
                    executed_at = database_now
                WHERE id = caller_command.id;
            END IF;
            command_id := caller_command.id;
            RETURN NEXT;
        END
        $stop_core$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.safety_paper_canary_stop(
            requested_id text,
            requested_actor text,
            requested_reason text
        )
        RETURNS TABLE(
            command_id bigint,
            command_status text,
            command_detail text,
            command_created_at timestamptz,
            command_executed_at timestamptz,
            result_created boolean,
            result_idempotent boolean,
            expired_signals integer,
            cancel_requested_live_orders integer,
            rejected_pending_resume integer
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $safety_stop$
        DECLARE
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            core_result record;
            command_row public.operator_commands%ROWTYPE;
        BEGIN
            IF session_user <> safety_app_name THEN
                RAISE EXCEPTION
                    'safety PAPER stop requires the exact safety login';
            END IF;
            SELECT * INTO core_result
            FROM public.paper_canary_stop_core(
                requested_id,
                requested_actor,
                requested_reason,
                'revoked_stopped'
            );
            SELECT * INTO command_row
            FROM public.operator_commands
            WHERE id = core_result.command_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'safety PAPER stop audit row is missing';
            END IF;
            command_id := command_row.id;
            command_status := command_row.status;
            command_detail := command_row.detail;
            command_created_at := command_row.created_at;
            command_executed_at := command_row.executed_at;
            result_created := core_result.result_created;
            result_idempotent := core_result.result_idempotent;
            expired_signals := core_result.expired_signals;
            cancel_requested_live_orders :=
                core_result.cancel_requested_live_orders;
            rejected_pending_resume :=
                core_result.rejected_pending_resume;
            RETURN NEXT;
        END
        $safety_stop$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.safety_paper_canary_ready()
        RETURNS boolean
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $safety_ready$
        DECLARE
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
        BEGIN
            IF session_user <> safety_app_name THEN
                RAISE EXCEPTION
                    'safety readiness requires the exact safety login';
            END IF;
            RETURN EXISTS (
                SELECT 1
                FROM public.runtime_control runtime
                JOIN public.venue_runtime_control polymarket
                  ON polymarket.venue::text = 'polymarket'
                JOIN public.venue_runtime_control binance
                  ON binance.venue::text = 'binance_spot'
                WHERE runtime.id = 1
                  AND runtime.mode::text = 'paper'
                  AND runtime.live_confirmation_until IS NULL
                  AND polymarket.enabled
                  AND polymarket.mode::text = 'paper'
                  AND polymarket.live_confirmation_until IS NULL
                  AND (
                      (
                          runtime.kill_switch
                          AND polymarket.kill_switch
                      )
                      OR (
                          NOT runtime.kill_switch
                          AND NOT polymarket.kill_switch
                          AND public.paper_canary_execution_authorized()
                      )
                  )
                  AND binance.enabled
                  AND binance.mode::text = 'backtest'
                  AND binance.kill_switch
                  AND binance.live_confirmation_until IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.signals
                      WHERE status = 'pending'::signal_status
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.orders
                      WHERE mode::text = 'live'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.positions
                      WHERE mode::text = 'live'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.execution_order_intents
                      WHERE mode::text = 'live'
                  )
            );
        END
        $safety_ready$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.safety_paper_canary_acquire(
            input_lease_id text,
            input_run_id text,
            input_acquire_request_id text,
            input_resume_request_id text,
            input_direct_stop_request_id text,
            input_release_request_id text,
            input_resume_actor text,
            input_resume_reason text,
            input_nonce_sha256 text,
            input_ttl_seconds integer
        )
        RETURNS TABLE(
            lease_pk bigint,
            result_created boolean,
            result_idempotent boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $safety_acquire$
        DECLARE
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            lease_row public.paper_canary_safety_leases%ROWTYPE;
            database_now timestamptz := clock_timestamp();
            watchdog_request_id text;
            watchdog_attempt integer;
        BEGIN
            IF session_user <> safety_app_name THEN
                RAISE EXCEPTION
                    'PAPER lease acquisition requires the exact safety login';
            END IF;
            IF input_ttl_seconds < 1 OR input_ttl_seconds > 30 THEN
                RAISE EXCEPTION
                    'PAPER canary lease TTL must be between 1 and 30 seconds';
            END IF;
            PERFORM pg_advisory_xact_lock(6004519027736253267);
            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE state = 'active'
            FOR UPDATE;
            IF FOUND THEN
                IF lease_row.lease_id IS DISTINCT FROM input_lease_id
                   OR lease_row.run_id IS DISTINCT FROM input_run_id
                   OR lease_row.acquire_request_id IS DISTINCT FROM
                      input_acquire_request_id
                   OR lease_row.resume_request_id IS DISTINCT FROM
                      input_resume_request_id
                   OR lease_row.direct_stop_request_id IS DISTINCT FROM
                      input_direct_stop_request_id
                   OR lease_row.release_request_id IS DISTINCT FROM
                      input_release_request_id
                   OR lease_row.resume_actor IS DISTINCT FROM
                      input_resume_actor
                   OR lease_row.resume_reason IS DISTINCT FROM
                      input_resume_reason
                   OR lease_row.nonce_sha256 IS DISTINCT FROM
                      input_nonce_sha256 THEN
                    RAISE EXCEPTION
                        'another active PAPER canary lease already exists';
                END IF;
                IF lease_row.expires_at <= database_now THEN
                    RAISE EXCEPTION
                        'exact PAPER canary lease is expired';
                END IF;
                lease_pk := lease_row.id;
                result_created := false;
                result_idempotent := true;
                RETURN NEXT;
                RETURN;
            END IF;

            -- Serialize identity reservation against every operator-command
            -- insert.  NOWAIT is fail-closed: an uncommitted command cannot be
            -- missed and later collide with a newly committed lease.
            LOCK TABLE public.operator_commands
                IN SHARE ROW EXCLUSIVE MODE NOWAIT;

            IF NOT EXISTS (
                SELECT 1
                FROM public.runtime_control
                WHERE id = 1
                  AND mode::text = 'paper'
                  AND kill_switch
                  AND live_confirmation_until IS NULL
            ) OR NOT EXISTS (
                SELECT 1
                FROM public.venue_runtime_control
                WHERE venue::text = 'polymarket'
                  AND enabled
                  AND mode::text = 'paper'
                  AND kill_switch
                  AND live_confirmation_until IS NULL
            ) OR NOT EXISTS (
                SELECT 1
                FROM public.venue_runtime_control
                WHERE venue::text = 'binance_spot'
                  AND enabled
                  AND mode::text = 'backtest'
                  AND kill_switch
                  AND live_confirmation_until IS NULL
            ) OR EXISTS (
                SELECT 1
                FROM public.signals
                WHERE status = 'pending'::signal_status
            ) OR EXISTS (
                SELECT 1
                FROM public.orders
                WHERE mode::text = 'live'
            ) OR EXISTS (
                SELECT 1
                FROM public.positions
                WHERE mode::text = 'live'
            ) OR EXISTS (
                SELECT 1
                FROM public.execution_order_intents
                WHERE mode::text = 'live'
            ) THEN
                RAISE EXCEPTION
                    'PAPER canary lease acquisition requires exact '
                    'fail-closed controls, zero pending Signals, and zero LIVE inventory';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.paper_canary_safety_leases
                WHERE lease_id = input_lease_id
                   OR run_id = input_run_id
                   OR input_acquire_request_id IN (
                       acquire_request_id,
                       resume_request_id,
                       watchdog_stop_request_id,
                       direct_stop_request_id,
                       release_request_id
                   )
                   OR input_resume_request_id IN (
                       acquire_request_id,
                       resume_request_id,
                       watchdog_stop_request_id,
                       direct_stop_request_id,
                       release_request_id
                   )
                   OR input_direct_stop_request_id IN (
                       acquire_request_id,
                       resume_request_id,
                       watchdog_stop_request_id,
                       direct_stop_request_id,
                       release_request_id
                   )
                   OR input_release_request_id IN (
                       acquire_request_id,
                       resume_request_id,
                       watchdog_stop_request_id,
                       direct_stop_request_id,
                       release_request_id
                   )
            ) THEN
                RAISE EXCEPTION
                    'PAPER canary lease identity already exists';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.operator_commands
                WHERE request_id IN (
                    input_acquire_request_id,
                    input_resume_request_id,
                    input_direct_stop_request_id,
                    input_release_request_id
                )
            ) THEN
                RAISE EXCEPTION
                    'a reserved PAPER canary request identity already exists';
            END IF;

            watchdog_request_id := NULL;
            FOR watchdog_attempt IN 1..8 LOOP
                watchdog_request_id := (
                    'paper-watchdog-'
                    || md5(
                        input_lease_id
                        || input_run_id
                        || input_acquire_request_id
                        || database_now::text
                        || watchdog_attempt::text
                        || random()::text
                    )
                );
                IF watchdog_request_id NOT IN (
                       input_acquire_request_id,
                       input_resume_request_id,
                       input_direct_stop_request_id,
                       input_release_request_id
                   )
                   AND NOT public.paper_canary_stop_request_reserved(
                       watchdog_request_id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                       FROM public.operator_commands
                       WHERE request_id = watchdog_request_id
                   ) THEN
                    EXIT;
                END IF;
                watchdog_request_id := NULL;
            END LOOP;
            IF watchdog_request_id IS NULL THEN
                RAISE EXCEPTION
                    'could not reserve a unique PAPER watchdog request identity';
            END IF;
            INSERT INTO public.paper_canary_safety_leases(
                lease_id,
                run_id,
                acquire_request_id,
                resume_request_id,
                resume_actor,
                resume_reason,
                watchdog_stop_request_id,
                direct_stop_request_id,
                release_request_id,
                nonce_sha256,
                state,
                acquired_at,
                heartbeat_at,
                watchdog_at,
                expires_at
            ) VALUES (
                input_lease_id,
                input_run_id,
                input_acquire_request_id,
                input_resume_request_id,
                input_resume_actor,
                input_resume_reason,
                watchdog_request_id,
                input_direct_stop_request_id,
                input_release_request_id,
                input_nonce_sha256,
                'active',
                database_now,
                database_now,
                database_now,
                database_now
                    + make_interval(secs => input_ttl_seconds)
            )
            RETURNING * INTO lease_row;
            lease_pk := lease_row.id;
            result_created := true;
            result_idempotent := false;
            RETURN NEXT;
        END
        $safety_acquire$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.safety_paper_canary_heartbeat(
            input_lease_id text,
            input_run_id text,
            input_nonce_sha256 text,
            input_ttl_seconds integer
        )
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $safety_heartbeat$
        DECLARE
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            lease_row public.paper_canary_safety_leases%ROWTYPE;
            database_now timestamptz := clock_timestamp();
        BEGIN
            IF session_user <> safety_app_name THEN
                RAISE EXCEPTION
                    'PAPER lease heartbeat requires the exact safety login';
            END IF;
            IF input_ttl_seconds < 1 OR input_ttl_seconds > 30 THEN
                RAISE EXCEPTION
                    'PAPER canary lease TTL must be between 1 and 30 seconds';
            END IF;
            PERFORM pg_advisory_xact_lock(6004519027736253267);
            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE lease_id = input_lease_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'PAPER canary lease not found';
            END IF;
            IF lease_row.run_id IS DISTINCT FROM input_run_id
               OR lease_row.nonce_sha256 IS DISTINCT FROM
                  input_nonce_sha256 THEN
                RAISE EXCEPTION
                    'PAPER canary lease run or nonce does not match';
            END IF;
            IF lease_row.state <> 'active'
               OR lease_row.expires_at <= database_now THEN
                RAISE EXCEPTION
                    'PAPER canary lease is not active or is expired';
            END IF;
            IF lease_row.watchdog_at <= lease_row.acquired_at
               OR lease_row.watchdog_at <
                  database_now - interval '7 seconds' THEN
                RAISE EXCEPTION
                    'PAPER canary heartbeat requires a fresh independent watchdog observation';
            END IF;
            UPDATE public.paper_canary_safety_leases
            SET heartbeat_at = database_now,
                expires_at = GREATEST(
                    expires_at,
                    database_now
                        + make_interval(secs => input_ttl_seconds)
                )
            WHERE id = lease_row.id;
            RETURN lease_row.id;
        END
        $safety_heartbeat$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.safety_paper_canary_release(
            input_lease_id text,
            input_run_id text,
            input_nonce_sha256 text,
            input_release_request_id text
        )
        RETURNS TABLE(
            lease_pk bigint,
            result_created boolean,
            result_idempotent boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $safety_release$
        DECLARE
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            lease_row public.paper_canary_safety_leases%ROWTYPE;
            core_result record;
            database_now timestamptz := clock_timestamp();
        BEGIN
            IF session_user <> safety_app_name THEN
                RAISE EXCEPTION
                    'PAPER lease release requires the exact safety login';
            END IF;
            PERFORM pg_advisory_xact_lock(6004519027736253267);
            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE lease_id = input_lease_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'PAPER canary lease not found';
            END IF;
            IF lease_row.run_id IS DISTINCT FROM input_run_id
               OR lease_row.nonce_sha256 IS DISTINCT FROM
                  input_nonce_sha256 THEN
                RAISE EXCEPTION
                    'PAPER canary lease run or nonce does not match';
            END IF;
            IF lease_row.release_request_id IS DISTINCT FROM
               input_release_request_id THEN
                RAISE EXCEPTION
                    'PAPER canary release request identity does not match';
            END IF;
            IF lease_row.state = 'released' THEN
                lease_pk := lease_row.id;
                result_created := false;
                result_idempotent := true;
                RETURN NEXT;
                RETURN;
            END IF;
            IF lease_row.state <> 'active'
               OR lease_row.expires_at <= database_now THEN
                RAISE EXCEPTION
                    'PAPER canary lease is not active or is expired';
            END IF;
            SELECT * INTO core_result
            FROM public.paper_canary_stop_core(
                input_release_request_id,
                'stratus-safety-release',
                format(
                    'Successful PAPER canary %s completed.',
                    lease_row.lease_id
                ),
                'released'
            );
            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE id = lease_row.id;
            IF lease_row.state <> 'released'
               OR lease_row.stop_command_id IS DISTINCT FROM
                  core_result.command_id THEN
                RAISE EXCEPTION
                    'PAPER canary release terminalization failed';
            END IF;
            lease_pk := lease_row.id;
            result_created := true;
            result_idempotent := false;
            RETURN NEXT;
        END
        $safety_release$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.safety_paper_canary_watchdog_tick()
        RETURNS TABLE(
            lease_pk bigint,
            terminalized boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $safety_watchdog$
        DECLARE
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            lease_row public.paper_canary_safety_leases%ROWTYPE;
            core_result record;
            database_now timestamptz := clock_timestamp();
            controls_safe boolean;
        BEGIN
            IF session_user <> safety_app_name THEN
                RAISE EXCEPTION
                    'PAPER watchdog requires the exact safety login';
            END IF;
            PERFORM pg_advisory_xact_lock(6004519027736253267);
            SELECT * INTO lease_row
            FROM public.paper_canary_safety_leases
            WHERE state = 'active'
            FOR UPDATE;
            IF NOT FOUND THEN
                lease_pk := NULL;
                terminalized := false;
                RETURN NEXT;
                RETURN;
            END IF;

            UPDATE public.paper_canary_safety_leases
            SET watchdog_at = database_now
            WHERE id = lease_row.id;
            lease_pk := lease_row.id;
            IF lease_row.expires_at > database_now THEN
                terminalized := false;
                RETURN NEXT;
                RETURN;
            END IF;

            SELECT (
                EXISTS (
                    SELECT 1
                    FROM public.runtime_control
                    WHERE id = 1
                      AND mode::text = 'paper'
                      AND kill_switch
                      AND live_confirmation_until IS NULL
                )
                AND EXISTS (
                    SELECT 1
                    FROM public.venue_runtime_control
                    WHERE venue::text = 'polymarket'
                      AND enabled
                      AND mode::text = 'paper'
                      AND kill_switch
                      AND live_confirmation_until IS NULL
                )
            ) INTO controls_safe;
            IF controls_safe THEN
                UPDATE public.operator_commands
                SET status = 'rejected',
                    detail = (
                        'Polymarket PAPER resume rejected because its safety '
                        'lease expired while controls were fail-closed.'
                    ),
                    executed_at = database_now
                WHERE request_id = lease_row.resume_request_id
                  AND status = 'pending';
                UPDATE public.paper_canary_safety_leases
                SET state = 'expired_safe'
                WHERE id = lease_row.id
                  AND state = 'active';
            ELSE
                SELECT * INTO core_result
                FROM public.paper_canary_stop_core(
                    lease_row.watchdog_stop_request_id,
                    'stratus-safety-watchdog',
                    format(
                        'PAPER canary safety lease %s expired.',
                        lease_row.lease_id
                    ),
                    'expired_stopped'
                );
                IF core_result.command_id IS NULL THEN
                    RAISE EXCEPTION
                        'watchdog stop audit row is missing';
                END IF;
            END IF;
            terminalized := true;
            RETURN NEXT;
        END
        $safety_watchdog$;
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.enforce_paper_canary_runtime_fence() FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.enforce_paper_canary_venue_fence() FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.enforce_paper_canary_request_reservation() FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.engage_paper_canary_runtime_stop(text, text, text) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.resume_paper_canary_exact(bigint) FROM PUBLIC"
    )
    for signature in (
        "public.paper_canary_stop_core(text, text, text, text)",
        "public.safety_paper_canary_stop(text, text, text)",
        "public.safety_paper_canary_ready()",
        "public.safety_paper_canary_acquire("
        "text, text, text, text, text, text, text, text, text, integer)",
        "public.safety_paper_canary_heartbeat(text, text, text, integer)",
        "public.safety_paper_canary_release(text, text, text, text)",
        "public.safety_paper_canary_watchdog_tick()",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC")

    # Revision 0044 requires runtime table ACLs on every application relation.
    # Lease-row isolation is therefore enforced by RLS, while these explicit
    # grants keep the generic runtime boundary proof honest.
    op.execute(
        """
        DO $lease_acl$
        DECLARE
            runtime_name name := ('stratus_runtime_' || md5(current_database()))::name;
            safety_name name := ('stratus_safety_' || md5(current_database()))::name;
        BEGIN
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE '
                'ON TABLE public.paper_canary_safety_leases TO %I',
                runtime_name
            );
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE '
                'public.paper_canary_safety_leases_id_seq TO %I',
                runtime_name
            );
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'public.paper_canary_fence_armed(), '
                'public.paper_canary_active_lease_exists(), '
                'public.paper_canary_execution_authorized(), '
                'public.paper_canary_stop_request_reserved(text), '
                'public.paper_canary_resume_request_reserved(text), '
                'public.paper_canary_resume_command_authorized('
                'text, text, text), '
                'public.engage_paper_canary_runtime_stop('
                'text, text, text), '
                'public.resume_paper_canary_exact('
                'bigint) TO %I',
                runtime_name
            );
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'public.safety_paper_canary_stop(text, text, text), '
                'public.safety_paper_canary_ready(), '
                'public.safety_paper_canary_acquire('
                'text, text, text, text, text, text, text, text, text, integer), '
                'public.safety_paper_canary_heartbeat('
                'text, text, text, integer), '
                'public.safety_paper_canary_release('
                'text, text, text, text), '
                'public.safety_paper_canary_watchdog_tick() TO %I',
                safety_name
            );
        END
        $lease_acl$;
        """
    )
    op.execute(
        """
        DO $safety_boundary$
        DECLARE
            runtime_name name := (
                'stratus_runtime_' || md5(current_database())
            )::name;
            app_name name := ('stratus_app_' || md5(current_database()))::name;
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
        BEGIN
            IF NOT (
                SELECT relrowsecurity
                FROM pg_class
                WHERE oid = 'public.paper_canary_safety_leases'::regclass
            ) OR NOT EXISTS (
                SELECT 1
                FROM pg_policy
                WHERE polrelid =
                    'public.paper_canary_safety_leases'::regclass
                  AND polname = 'paper_canary_safety_role_select'
                  AND polroles = ARRAY[safety_name::regrole::oid]
                  AND polcmd = 'r'
            ) THEN
                RAISE EXCEPTION
                    'PAPER canary safety RLS boundary is missing';
            END IF;
            IF pg_has_role(app_name, safety_name, 'MEMBER')
               OR pg_has_role(safety_app_name, runtime_name, 'MEMBER')
               OR NOT pg_has_role(safety_app_name, safety_name, 'MEMBER')
               OR has_function_privilege(
                   safety_app_name,
                   'public.resume_paper_canary_exact(bigint)',
                   'EXECUTE'
               )
               OR NOT has_function_privilege(
                   app_name,
                   'public.resume_paper_canary_exact(bigint)',
                   'EXECUTE'
               ) THEN
                RAISE EXCEPTION
                    'PAPER canary safety role boundary drifted';
            END IF;
        END
        $safety_boundary$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.assert_stratus_safety_boundary(
            require_login boolean DEFAULT true
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $safety_boundary$
        DECLARE
            runtime_name name := (
                'stratus_runtime_' || md5(current_database())
            )::name;
            app_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            safety_role record;
            safety_app_role record;
            database_owner name;
        BEGIN
            IF current_setting('server_version_num')::integer < 160000 THEN
                RAISE EXCEPTION
                    'STRATUS safety boundary requires PostgreSQL 16+';
            END IF;
            IF require_login AND (
                session_user <> safety_app_name
                OR current_user <> safety_app_name
            ) THEN
                RAISE EXCEPTION
                    'safety boundary requires the database-scoped safety login';
            ELSIF NOT require_login AND (
                session_user <> 'stratus'::name
                OR current_user <> 'stratus'::name
            ) THEN
                RAISE EXCEPTION
                    'owner safety boundary must execute as stratus';
            END IF;
            IF current_setting('session_replication_role')
               IS DISTINCT FROM 'origin' THEN
                RAISE EXCEPTION
                    'STRATUS safety session_replication_role is not origin';
            END IF;
            SELECT pg_get_userbyid(datdba) INTO database_owner
            FROM pg_database
            WHERE datname = current_database();
            IF database_owner IS DISTINCT FROM 'stratus'::name THEN
                RAISE EXCEPTION 'STRATUS database owner drifted';
            END IF;

            SELECT * INTO safety_role
            FROM pg_roles WHERE rolname = safety_name;
            SELECT * INTO safety_app_role
            FROM pg_roles WHERE rolname = safety_app_name;
            IF safety_role.oid IS NULL OR safety_app_role.oid IS NULL
               OR shobj_description(
                   safety_role.oid, 'pg_authid'
               ) IS DISTINCT FROM format(
                   'STRATUS managed safety group v1 database=%s',
                   current_database()
               )
               OR shobj_description(
                   safety_app_role.oid, 'pg_authid'
               ) IS DISTINCT FROM format(
                   'STRATUS managed safety login v1 database=%s',
                   current_database()
               )
               OR safety_role.rolcanlogin
               OR safety_role.rolsuper
               OR NOT safety_role.rolinherit
               OR safety_role.rolcreatedb
               OR safety_role.rolcreaterole
               OR safety_role.rolreplication
               OR safety_role.rolbypassrls
               OR safety_role.rolconfig IS NOT NULL
               OR safety_app_role.rolsuper
               OR NOT safety_app_role.rolinherit
               OR safety_app_role.rolcreatedb
               OR safety_app_role.rolcreaterole
               OR safety_app_role.rolreplication
               OR safety_app_role.rolbypassrls
               OR safety_app_role.rolconfig IS NOT NULL
               OR (require_login AND NOT safety_app_role.rolcanlogin) THEN
                RAISE EXCEPTION
                    'STRATUS safety role provenance or attributes drifted';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_db_role_setting
                WHERE setrole IN (
                    safety_role.oid, safety_app_role.oid
                )
            ) OR has_parameter_privilege(
                safety_role.oid, 'session_replication_role', 'SET'
            ) OR has_parameter_privilege(
                safety_app_role.oid, 'session_replication_role', 'SET'
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety parameter boundary drifted';
            END IF;
            IF (
                SELECT count(*) FROM pg_auth_members
                WHERE roleid = safety_role.oid
                  AND member = safety_app_role.oid
            ) <> 1 OR NOT EXISTS (
                SELECT 1 FROM pg_auth_members
                WHERE roleid = safety_role.oid
                  AND member = safety_app_role.oid
                  AND grantor = 'stratus'::regrole
                  AND NOT admin_option
                  AND inherit_option
                  AND NOT set_option
            ) OR EXISTS (
                SELECT 1
                FROM pg_auth_members membership
                WHERE membership.member = safety_role.oid
                   OR (
                       membership.member = safety_app_role.oid
                       AND membership.roleid <> safety_role.oid
                   )
                   OR (
                       membership.roleid = safety_role.oid
                       AND membership.member <> safety_app_role.oid
                   )
                   OR membership.roleid = safety_app_role.oid
            ) OR pg_has_role(app_name, safety_name, 'MEMBER')
              OR pg_has_role(safety_app_name, runtime_name, 'MEMBER') THEN
                RAISE EXCEPTION
                    'STRATUS safety membership boundary drifted';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_shdepend
                WHERE refclassid = 'pg_authid'::regclass
                  AND refobjid IN (
                      safety_role.oid, safety_app_role.oid
                  )
                  AND deptype = 'o'
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety role owns cluster objects';
            END IF;

            IF NOT has_database_privilege(
                       safety_app_role.oid,
                       current_database(),
                       'CONNECT'
                   )
               OR has_database_privilege(
                       safety_app_role.oid,
                       current_database(),
                       'TEMPORARY'
                   )
               OR has_database_privilege(
                       safety_app_role.oid,
                       current_database(),
                       'CREATE'
                   )
               OR NOT has_schema_privilege(
                       safety_app_role.oid, 'public', 'USAGE'
                   )
               OR has_schema_privilege(
                       safety_app_role.oid, 'public', 'CREATE'
                   ) THEN
                RAISE EXCEPTION
                    'STRATUS safety database or schema ACL drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_database database_row,
                     LATERAL aclexplode(
                         COALESCE(
                             database_row.datacl,
                             acldefault('d', database_row.datdba)
                         )
                     ) privilege
                WHERE database_row.datname = current_database()
                  AND privilege.grantee = safety_role.oid
            ) <> 1 OR NOT EXISTS (
                SELECT 1
                FROM pg_database database_row,
                     LATERAL aclexplode(
                         COALESCE(
                             database_row.datacl,
                             acldefault('d', database_row.datdba)
                         )
                     ) privilege
                WHERE database_row.datname = current_database()
                  AND privilege.grantee = safety_role.oid
                  AND privilege.grantor = 'stratus'::regrole
                  AND privilege.privilege_type = 'CONNECT'
                  AND NOT privilege.is_grantable
            ) OR EXISTS (
                SELECT 1
                FROM pg_database database_row,
                     LATERAL aclexplode(
                         COALESCE(
                             database_row.datacl,
                             acldefault('d', database_row.datdba)
                         )
                     ) privilege
                WHERE database_row.datname = current_database()
                  AND privilege.grantee = safety_app_role.oid
            ) OR (
                SELECT count(*)
                FROM pg_namespace namespace,
                     LATERAL aclexplode(
                         COALESCE(
                             namespace.nspacl,
                             acldefault('n', namespace.nspowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = safety_role.oid
            ) <> 1 OR NOT EXISTS (
                SELECT 1
                FROM pg_namespace namespace,
                     LATERAL aclexplode(
                         COALESCE(
                             namespace.nspacl,
                             acldefault('n', namespace.nspowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = safety_role.oid
                  AND privilege.grantor = 'pg_database_owner'::regrole
                  AND privilege.privilege_type = 'USAGE'
                  AND NOT privilege.is_grantable
            ) OR EXISTS (
                SELECT 1
                FROM pg_namespace namespace,
                     LATERAL aclexplode(
                         COALESCE(
                             namespace.nspacl,
                             acldefault('n', namespace.nspowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = safety_app_role.oid
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety direct database or schema ACL drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_class relation
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND (
                      has_table_privilege(
                          safety_app_role.oid, relation.oid, 'SELECT'
                      ) <> (
                          relation.relname =
                              'paper_canary_safety_leases'
                      )
                      OR has_table_privilege(
                          safety_app_role.oid, relation.oid, 'INSERT'
                      )
                      OR has_table_privilege(
                          safety_app_role.oid, relation.oid, 'UPDATE'
                      )
                      OR has_table_privilege(
                          safety_app_role.oid, relation.oid, 'DELETE'
                      )
                      OR has_table_privilege(
                          safety_app_role.oid, relation.oid, 'TRUNCATE'
                      )
                      OR has_table_privilege(
                          safety_app_role.oid, relation.oid, 'REFERENCES'
                      )
                      OR has_table_privilege(
                          safety_app_role.oid, relation.oid, 'TRIGGER'
                      )
                  )
            ) THEN
                RAISE EXCEPTION 'STRATUS safety table ACL drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_class relation
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             relation.relacl,
                             acldefault('r', relation.relowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND privilege.grantee = safety_role.oid
            ) <> 1 OR NOT EXISTS (
                SELECT 1
                FROM pg_class relation
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             relation.relacl,
                             acldefault('r', relation.relowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND relation.relname = 'paper_canary_safety_leases'
                  AND privilege.grantee = safety_role.oid
                  AND privilege.grantor = 'stratus'::regrole
                  AND privilege.privilege_type = 'SELECT'
                  AND NOT privilege.is_grantable
            ) OR EXISTS (
                SELECT 1
                FROM pg_class relation
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             relation.relacl,
                             acldefault('r', relation.relowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND privilege.grantee = safety_app_role.oid
            ) OR EXISTS (
                SELECT 1
                FROM pg_attribute attribute
                JOIN pg_class relation
                  ON relation.oid = attribute.attrelid
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace,
                     LATERAL aclexplode(attribute.attacl) privilege
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                  AND privilege.grantee IN (
                      0, safety_role.oid, safety_app_role.oid
                  )
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety direct or column table ACL drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_class sequence
                JOIN pg_namespace namespace
                  ON namespace.oid = sequence.relnamespace
                WHERE namespace.nspname = 'public'
                  AND sequence.relkind = 'S'
                  AND (
                      has_sequence_privilege(
                          safety_app_role.oid, sequence.oid, 'USAGE'
                      )
                      OR has_sequence_privilege(
                          safety_app_role.oid, sequence.oid, 'SELECT'
                      )
                      OR has_sequence_privilege(
                          safety_app_role.oid, sequence.oid, 'UPDATE'
                      )
                  )
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety sequence ACL drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_class sequence
                JOIN pg_namespace namespace
                  ON namespace.oid = sequence.relnamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             sequence.relacl,
                             acldefault('S', sequence.relowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND sequence.relkind = 'S'
                  AND privilege.grantee IN (
                      safety_role.oid, safety_app_role.oid
                  )
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety direct sequence ACL drifted';
            END IF;
            IF NOT (
                SELECT relrowsecurity
                FROM pg_class
                WHERE oid =
                    'public.paper_canary_safety_leases'::regclass
            ) OR (
                SELECT relforcerowsecurity
                FROM pg_class
                WHERE oid =
                    'public.paper_canary_safety_leases'::regclass
            ) OR (
                SELECT count(*)
                FROM pg_policy
                WHERE polrelid =
                    'public.paper_canary_safety_leases'::regclass
            ) <> 1 OR NOT EXISTS (
                SELECT 1
                FROM pg_policy
                WHERE polrelid =
                    'public.paper_canary_safety_leases'::regclass
                  AND polname = 'paper_canary_safety_role_select'
                  AND polroles = ARRAY[safety_role.oid]::oid[]
                  AND polcmd = 'r'
                  AND polpermissive
                  AND pg_get_expr(polqual, polrelid, true) = 'true'
                  AND polwithcheck IS NULL
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety RLS boundary drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'public.safety_paper_canary_stop('
                            'text,text,text)'::text,
                            true
                        ),
                        (
                            'public.safety_paper_canary_ready()'::text,
                            true
                        ),
                        (
                            'public.safety_paper_canary_acquire('
                            'text,text,text,text,text,text,text,text,text,integer)'::text,
                            true
                        ),
                        (
                            'public.safety_paper_canary_heartbeat('
                            'text,text,text,integer)'::text,
                            true
                        ),
                        (
                            'public.safety_paper_canary_release('
                            'text,text,text,text)'::text,
                            true
                        ),
                        (
                            'public.safety_paper_canary_watchdog_tick()'::text,
                            true
                        ),
                        (
                            'public.resume_paper_canary_exact(bigint)'::text,
                            false
                        ),
                        (
                            'public.engage_paper_canary_runtime_stop('
                            'text,text,text)'::text,
                            false
                        ),
                        (
                            'public.paper_canary_stop_core('
                            'text,text,text,text)'::text,
                            false
                        ),
                        (
                            'public.paper_canary_fence_armed()'::text,
                            false
                        ),
                        (
                            'public.paper_canary_active_lease_exists()'::text,
                            false
                        ),
                        (
                            'public.paper_canary_execution_authorized()'::text,
                            false
                        ),
                        (
                            'public.paper_canary_stop_request_reserved(text)'::text,
                            false
                        ),
                        (
                            'public.paper_canary_resume_request_reserved(text)'::text,
                            false
                        ),
                        (
                            'public.paper_canary_resume_command_authorized('
                            'text,text,text)'::text,
                            false
                        )
                ) AS expected(signature, permitted)
                WHERE has_function_privilege(
                    safety_app_name,
                    expected.signature,
                    'EXECUTE'
                ) <> expected.permitted
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety function ACL drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             procedure.proacl,
                             acldefault('f', procedure.proowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = safety_role.oid
            ) <> 7 OR EXISTS (
                SELECT 1
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             procedure.proacl,
                             acldefault('f', procedure.proowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = safety_role.oid
                  AND (
                      procedure.oid NOT IN (
                          'public.safety_paper_canary_stop('
                          'text,text,text)'::regprocedure,
                          'public.safety_paper_canary_ready()'::regprocedure,
                          'public.safety_paper_canary_acquire('
                          'text,text,text,text,text,text,text,text,text,integer)'
                          ::regprocedure,
                          'public.safety_paper_canary_heartbeat('
                          'text,text,text,integer)'::regprocedure,
                          'public.safety_paper_canary_release('
                          'text,text,text,text)'::regprocedure,
                          'public.safety_paper_canary_watchdog_tick()'
                          ::regprocedure,
                          'public.assert_stratus_safety_boundary(boolean)'
                          ::regprocedure
                      )
                      OR privilege.grantor <> 'stratus'::regrole
                      OR privilege.privilege_type <> 'EXECUTE'
                      OR privilege.is_grantable
                  )
            ) OR EXISTS (
                SELECT 1
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             procedure.proacl,
                             acldefault('f', procedure.proowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = safety_app_role.oid
            ) OR EXISTS (
                SELECT 1
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure.prosecdef
                  AND has_function_privilege(
                      safety_app_role.oid, procedure.oid, 'EXECUTE'
                  )
                  AND procedure.oid NOT IN (
                      'public.safety_paper_canary_stop('
                      'text,text,text)'::regprocedure,
                      'public.safety_paper_canary_ready()'::regprocedure,
                      'public.safety_paper_canary_acquire('
                      'text,text,text,text,text,text,text,text,text,integer)'
                      ::regprocedure,
                      'public.safety_paper_canary_heartbeat('
                      'text,text,text,integer)'::regprocedure,
                      'public.safety_paper_canary_release('
                      'text,text,text,text)'::regprocedure,
                      'public.safety_paper_canary_watchdog_tick()'
                      ::regprocedure
                  )
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety exact function ACL drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'paper_canary_safety_leases'::text,
                            'trg_paper_canary_lease_audit'::text,
                            'public.enforce_paper_canary_lease_audit()'::text,
                            31::smallint
                        ),
                        (
                            'runtime_control'::text,
                            'trg_paper_canary_runtime_fence'::text,
                            'public.enforce_paper_canary_runtime_fence()'::text,
                            31::smallint
                        ),
                        (
                            'venue_runtime_control'::text,
                            'trg_paper_canary_venue_fence'::text,
                            'public.enforce_paper_canary_venue_fence()'::text,
                            31::smallint
                        ),
                        (
                            'operator_commands'::text,
                            'trg_paper_canary_request_reservation'::text,
                            'public.enforce_paper_canary_request_reservation()'::text,
                            31::smallint
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
                RAISE EXCEPTION
                    'STRATUS safety trigger boundary drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_trigger trigger_row
                JOIN pg_class relation
                  ON relation.oid = trigger_row.tgrelid
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname IN (
                      'paper_canary_safety_leases',
                      'runtime_control',
                      'venue_runtime_control',
                      'operator_commands'
                  )
                  AND NOT trigger_row.tgisinternal
            ) <> 4 THEN
                RAISE EXCEPTION
                    'STRATUS safety protected trigger set drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'signals'::text,
                            'trg_paper_canary_signal_commit_fence'::text,
                            21::smallint
                        ),
                        (
                            'orders'::text,
                            'trg_paper_canary_order_commit_fence'::text,
                            21::smallint
                        ),
                        (
                            'execution_order_intents'::text,
                            'trg_paper_canary_intent_commit_fence'::text,
                            5::smallint
                        ),
                        (
                            'execution_fill_legs'::text,
                            'trg_paper_canary_fill_leg_commit_fence'::text,
                            5::smallint
                        ),
                        (
                            'positions'::text,
                            'trg_paper_canary_position_commit_fence'::text,
                            21::smallint
                        )
                ) AS expected(relation_name, trigger_name, trigger_type)
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
                      AND trigger_row.tgfoid = to_regprocedure(
                          'public.enforce_paper_canary_execution_commit_fence()'
                      )
                      AND trigger_row.tgtype = expected.trigger_type
                      AND trigger_row.tgconstraint <> 0
                      AND trigger_row.tgdeferrable
                      AND trigger_row.tginitdeferred
                )
            ) OR (
                SELECT count(*)
                FROM pg_trigger trigger_row
                WHERE NOT trigger_row.tgisinternal
                  AND trigger_row.tgfoid = to_regprocedure(
                      'public.enforce_paper_canary_execution_commit_fence()'
                  )
            ) <> 5 THEN
                RAISE EXCEPTION
                    'STRATUS safety execution commit fence drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'signals'::text,
                            'trg_paper_canary_signal_identity_fence'::text,
                            27::smallint
                        ),
                        (
                            'orders'::text,
                            'trg_paper_canary_order_identity_fence'::text,
                            27::smallint
                        ),
                        (
                            'positions'::text,
                            'trg_paper_canary_position_identity_fence'::text,
                            27::smallint
                        ),
                        (
                            'bankroll_log'::text,
                            'trg_paper_canary_bankroll_identity_fence'::text,
                            31::smallint
                        )
                ) AS expected(relation_name, trigger_name, trigger_type)
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
                      AND trigger_row.tgfoid = to_regprocedure(
                          'public.enforce_paper_canary_execution_identity_fence()'
                      )
                      AND trigger_row.tgtype = expected.trigger_type
                      AND trigger_row.tgconstraint = 0
                )
            ) OR (
                SELECT count(*)
                FROM pg_trigger trigger_row
                WHERE NOT trigger_row.tgisinternal
                  AND trigger_row.tgfoid = to_regprocedure(
                      'public.enforce_paper_canary_execution_identity_fence()'
                  )
            ) <> 4 THEN
                RAISE EXCEPTION
                    'STRATUS execution identity fence drifted';
            END IF;

            IF (
                SELECT count(*)
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure.proname IN (
                      'paper_canary_fence_armed',
                      'paper_canary_active_lease_exists',
                      'paper_canary_execution_authorized',
                      'paper_canary_stop_request_reserved',
                      'paper_canary_resume_request_reserved',
                      'paper_canary_resume_command_authorized',
                      'engage_paper_canary_runtime_stop',
                      'resume_paper_canary_exact',
                      'safety_paper_canary_stop',
                      'safety_paper_canary_ready',
                      'safety_paper_canary_acquire',
                      'safety_paper_canary_heartbeat',
                      'safety_paper_canary_release',
                      'safety_paper_canary_watchdog_tick'
                  )
                  AND procedure.prosecdef
                  AND pg_get_userbyid(procedure.proowner) = 'stratus'
                  AND procedure.proconfig =
                      ARRAY['search_path=pg_catalog, public']::text[]
                  AND NOT EXISTS (
                      SELECT 1
                      FROM aclexplode(
                          COALESCE(
                              procedure.proacl,
                              acldefault('f', procedure.proowner)
                          )
                      ) privilege
                      WHERE privilege.grantee = 0
                        AND privilege.privilege_type = 'EXECUTE'
                  )
            ) <> 14 THEN
                RAISE EXCEPTION
                    'STRATUS safety authority function hardening drifted';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure.proname = 'paper_canary_stop_core'
                  AND pg_get_userbyid(procedure.proowner) = 'stratus'
                  AND NOT procedure.prosecdef
                  AND procedure.proconfig =
                      ARRAY['search_path=pg_catalog, public']::text[]
                  AND NOT EXISTS (
                      SELECT 1
                      FROM aclexplode(
                          COALESCE(
                              procedure.proacl,
                              acldefault('f', procedure.proowner)
                          )
                      ) privilege
                      WHERE privilege.grantee = 0
                        AND privilege.privilege_type = 'EXECUTE'
                  )
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety stop core hardening drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure.proname IN (
                      'enforce_paper_canary_lease_audit',
                      'enforce_paper_canary_runtime_fence',
                      'enforce_paper_canary_venue_fence',
                      'enforce_paper_canary_request_reservation',
                      'enforce_paper_canary_execution_commit_fence',
                      'enforce_paper_canary_execution_identity_fence'
                  )
                  AND pg_get_userbyid(procedure.proowner) = 'stratus'
                  AND NOT procedure.prosecdef
                  AND procedure.prorettype = 'trigger'::regtype
                  AND procedure.proconfig =
                      ARRAY['search_path=pg_catalog, public']::text[]
                  AND NOT EXISTS (
                      SELECT 1
                      FROM aclexplode(
                          COALESCE(
                              procedure.proacl,
                              acldefault('f', procedure.proowner)
                          )
                      ) privilege
                      WHERE privilege.grantee = 0
                        AND privilege.privilege_type = 'EXECUTE'
                  )
            ) <> 6 THEN
                RAISE EXCEPTION
                    'STRATUS safety trigger function hardening drifted';
            END IF;
            RETURN true;
        END
        $safety_boundary$;
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.assert_stratus_safety_boundary(boolean) FROM PUBLIC"
    )
    op.execute(
        """
        DO $safety_boundary_acl$
        DECLARE
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
        BEGIN
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'public.assert_stratus_safety_boundary(boolean) TO %I',
                safety_name
            );
        END
        $safety_boundary_acl$;
        """
    )
    op.execute("SELECT public.assert_stratus_safety_boundary(false)")
    # Preserve the broad 0044 assertion as a versioned implementation and
    # wrap its stable public name with the canary-specific RLS/function proof.
    # Runtime connections call this on every physical checkout, so a later
    # owner-side policy or function-grant drift fails closed instead of relying
    # on this migration's one-time checks.
    op.execute(
        "ALTER FUNCTION public.assert_stratus_runtime_boundary(boolean) "
        "RENAME TO assert_stratus_runtime_boundary_v2"
    )
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
            base_result boolean;
            lease_table oid :=
                'public.paper_canary_safety_leases'::regclass;
            runtime_name name := (
                'stratus_runtime_' || md5(current_database())
            )::name;
            app_name name := (
                'stratus_app_' || md5(current_database())
            )::name;
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            runtime_oid oid;
            app_oid oid;
            safety_oid oid;
        BEGIN
            base_result :=
                public.assert_stratus_runtime_boundary_v2(require_login);
            SELECT oid INTO safety_oid
            FROM pg_roles
            WHERE rolname = safety_name;
            SELECT oid INTO runtime_oid
            FROM pg_roles
            WHERE rolname = runtime_name;
            SELECT oid INTO app_oid
            FROM pg_roles
            WHERE rolname = app_name;
            IF safety_oid IS NULL
               OR runtime_oid IS NULL
               OR app_oid IS NULL THEN
                RAISE EXCEPTION 'STRATUS canary role is missing';
            END IF;
            IF NOT (
                SELECT relrowsecurity
                FROM pg_class
                WHERE oid = lease_table
            ) OR (
                SELECT relforcerowsecurity
                FROM pg_class
                WHERE oid = lease_table
            ) OR (
                SELECT count(*)
                FROM pg_policy
                WHERE polrelid = lease_table
            ) <> 1 OR NOT EXISTS (
                SELECT 1
                FROM pg_policy
                WHERE polrelid = lease_table
                  AND polname = 'paper_canary_safety_role_select'
                  AND polroles = ARRAY[safety_oid]::oid[]
                  AND polcmd = 'r'
                  AND polpermissive
                  AND pg_get_expr(polqual, polrelid, true) = 'true'
                  AND polwithcheck IS NULL
            ) THEN
                RAISE EXCEPTION
                    'PAPER canary safety RLS boundary drifted';
            END IF;
            IF pg_has_role(app_name, safety_name, 'MEMBER')
               OR pg_has_role(safety_app_name, runtime_name, 'MEMBER')
               OR NOT pg_has_role(
                   safety_app_name, safety_name, 'MEMBER'
               )
               OR EXISTS (
                   SELECT 1
                   FROM pg_proc procedure,
                        LATERAL aclexplode(
                            COALESCE(
                                procedure.proacl,
                                acldefault('f', procedure.proowner)
                            )
                        ) privilege
                   WHERE procedure.oid = to_regprocedure(
                       'public.paper_canary_execution_authorized()'
                   )
                     AND privilege.grantee = 0
                     AND privilege.privilege_type = 'EXECUTE'
               )
               OR EXISTS (
                   SELECT 1
                   FROM pg_proc procedure,
                        LATERAL aclexplode(
                            COALESCE(
                                procedure.proacl,
                                acldefault('f', procedure.proowner)
                            )
                        ) privilege
                   WHERE procedure.oid = to_regprocedure(
                       'public.resume_paper_canary_exact(bigint)'
                   )
                     AND privilege.grantee = 0
                     AND privilege.privilege_type = 'EXECUTE'
               )
               OR NOT has_function_privilege(
                   app_name,
                   'public.paper_canary_fence_armed()',
                   'EXECUTE'
               )
               OR NOT has_function_privilege(
                   app_name,
                   'public.paper_canary_active_lease_exists()',
                   'EXECUTE'
               )
               OR NOT has_function_privilege(
                   app_name,
                   'public.paper_canary_execution_authorized()',
                   'EXECUTE'
               )
               OR NOT has_function_privilege(
                   app_name,
                   'public.resume_paper_canary_exact(bigint)',
                   'EXECUTE'
               )
               OR has_function_privilege(
                   safety_app_name,
                   'public.resume_paper_canary_exact(bigint)',
                   'EXECUTE'
               )
               OR has_function_privilege(
                   safety_app_name,
                   'public.engage_paper_canary_runtime_stop('
                   'text,text,text)',
                   'EXECUTE'
               )
               OR has_function_privilege(
                   safety_app_name,
                   'public.paper_canary_stop_core(text,text,text,text)',
                   'EXECUTE'
               )
               OR has_function_privilege(
                   app_name,
                   'public.safety_paper_canary_stop(text,text,text)',
                   'EXECUTE'
               )
               OR EXISTS (
                   SELECT 1
                   FROM (
                       VALUES
                           (
                               'public.safety_paper_canary_ready()'::text
                           ),
                           (
                               'public.safety_paper_canary_acquire('
                               'text,text,text,text,text,text,text,text,text,integer)'
                               ::text
                           ),
                           (
                               'public.safety_paper_canary_heartbeat('
                               'text,text,text,integer)'::text
                           ),
                           (
                               'public.safety_paper_canary_release('
                               'text,text,text,text)'::text
                           ),
                           (
                               'public.safety_paper_canary_watchdog_tick()'
                               ::text
                           )
                   ) AS safety_primitive(signature)
                   WHERE has_function_privilege(
                       app_name,
                       safety_primitive.signature,
                       'EXECUTE'
                   )
               ) THEN
                RAISE EXCEPTION
                    'PAPER canary authority function boundary drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             procedure.proacl,
                             acldefault('f', procedure.proowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = runtime_oid
            ) <> 10 OR EXISTS (
                SELECT 1
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             procedure.proacl,
                             acldefault('f', procedure.proowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = runtime_oid
                  AND (
                      procedure.oid NOT IN (
                          'public.assert_stratus_runtime_boundary_v2(boolean)'
                          ::regprocedure,
                          'public.assert_stratus_runtime_boundary(boolean)'
                          ::regprocedure,
                          'public.paper_canary_fence_armed()'::regprocedure,
                          'public.paper_canary_active_lease_exists()'
                          ::regprocedure,
                          'public.paper_canary_execution_authorized()'
                          ::regprocedure,
                          'public.paper_canary_stop_request_reserved(text)'
                          ::regprocedure,
                          'public.paper_canary_resume_request_reserved(text)'
                          ::regprocedure,
                          'public.paper_canary_resume_command_authorized('
                          'text,text,text)'::regprocedure,
                          'public.engage_paper_canary_runtime_stop('
                          'text,text,text)'::regprocedure,
                          'public.resume_paper_canary_exact(bigint)'
                          ::regprocedure
                      )
                      OR privilege.grantor <> 'stratus'::regrole
                      OR privilege.privilege_type <> 'EXECUTE'
                      OR privilege.is_grantable
                  )
            ) OR EXISTS (
                SELECT 1
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace,
                     LATERAL aclexplode(
                         COALESCE(
                             procedure.proacl,
                             acldefault('f', procedure.proowner)
                         )
                     ) privilege
                WHERE namespace.nspname = 'public'
                  AND privilege.grantee = app_oid
            ) OR EXISTS (
                SELECT 1
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure.prosecdef
                  AND has_function_privilege(
                      app_oid, procedure.oid, 'EXECUTE'
                  )
                  AND procedure.oid NOT IN (
                      'public.paper_canary_fence_armed()'::regprocedure,
                      'public.paper_canary_active_lease_exists()'
                      ::regprocedure,
                      'public.paper_canary_execution_authorized()'
                      ::regprocedure,
                      'public.paper_canary_stop_request_reserved(text)'
                      ::regprocedure,
                      'public.paper_canary_resume_request_reserved(text)'
                      ::regprocedure,
                      'public.paper_canary_resume_command_authorized('
                      'text,text,text)'::regprocedure,
                      'public.engage_paper_canary_runtime_stop('
                      'text,text,text)'::regprocedure,
                      'public.resume_paper_canary_exact(bigint)'
                      ::regprocedure
                  )
            ) THEN
                RAISE EXCEPTION
                    'PAPER canary exact runtime function ACL drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure.proname IN (
                      'paper_canary_fence_armed',
                      'paper_canary_active_lease_exists',
                      'paper_canary_execution_authorized',
                      'paper_canary_stop_request_reserved',
                      'paper_canary_resume_request_reserved',
                      'paper_canary_resume_command_authorized',
                      'engage_paper_canary_runtime_stop',
                      'resume_paper_canary_exact',
                      'safety_paper_canary_stop',
                      'safety_paper_canary_ready',
                      'safety_paper_canary_acquire',
                      'safety_paper_canary_heartbeat',
                      'safety_paper_canary_release',
                      'safety_paper_canary_watchdog_tick'
                  )
                  AND procedure.prosecdef
                  AND pg_get_userbyid(procedure.proowner) = 'stratus'
                  AND procedure.proconfig =
                      ARRAY['search_path=pg_catalog, public']::text[]
            ) <> 14 THEN
                RAISE EXCEPTION
                    'PAPER canary authority function hardening drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'paper_canary_safety_leases'::text,
                            'trg_paper_canary_lease_audit'::text,
                            'public.enforce_paper_canary_lease_audit()'::text,
                            31::smallint
                        ),
                        (
                            'runtime_control'::text,
                            'trg_paper_canary_runtime_fence'::text,
                            'public.enforce_paper_canary_runtime_fence()'::text,
                            31::smallint
                        ),
                        (
                            'venue_runtime_control'::text,
                            'trg_paper_canary_venue_fence'::text,
                            'public.enforce_paper_canary_venue_fence()'::text,
                            31::smallint
                        ),
                        (
                            'operator_commands'::text,
                            'trg_paper_canary_request_reservation'::text,
                            'public.enforce_paper_canary_request_reservation()'::text,
                            31::smallint
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
                RAISE EXCEPTION
                    'PAPER canary integrity trigger boundary drifted';
            END IF;
            IF (
                SELECT count(*)
                FROM pg_trigger trigger_row
                JOIN pg_class relation
                  ON relation.oid = trigger_row.tgrelid
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname IN (
                      'paper_canary_safety_leases',
                      'runtime_control',
                      'venue_runtime_control',
                      'operator_commands'
                  )
                  AND NOT trigger_row.tgisinternal
            ) <> 4 THEN
                RAISE EXCEPTION
                    'PAPER canary protected trigger set drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'signals'::text,
                            'trg_paper_canary_signal_commit_fence'::text,
                            21::smallint
                        ),
                        (
                            'orders'::text,
                            'trg_paper_canary_order_commit_fence'::text,
                            21::smallint
                        ),
                        (
                            'execution_order_intents'::text,
                            'trg_paper_canary_intent_commit_fence'::text,
                            5::smallint
                        ),
                        (
                            'execution_fill_legs'::text,
                            'trg_paper_canary_fill_leg_commit_fence'::text,
                            5::smallint
                        ),
                        (
                            'positions'::text,
                            'trg_paper_canary_position_commit_fence'::text,
                            21::smallint
                        )
                ) AS expected(relation_name, trigger_name, trigger_type)
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
                      AND trigger_row.tgfoid = to_regprocedure(
                          'public.enforce_paper_canary_execution_commit_fence()'
                      )
                      AND trigger_row.tgtype = expected.trigger_type
                      AND trigger_row.tgconstraint <> 0
                      AND trigger_row.tgdeferrable
                      AND trigger_row.tginitdeferred
                )
            ) OR (
                SELECT count(*)
                FROM pg_trigger trigger_row
                WHERE NOT trigger_row.tgisinternal
                  AND trigger_row.tgfoid = to_regprocedure(
                      'public.enforce_paper_canary_execution_commit_fence()'
                  )
            ) <> 5 THEN
                RAISE EXCEPTION
                    'PAPER canary execution commit fence drifted';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        (
                            'signals'::text,
                            'trg_paper_canary_signal_identity_fence'::text,
                            27::smallint
                        ),
                        (
                            'orders'::text,
                            'trg_paper_canary_order_identity_fence'::text,
                            27::smallint
                        ),
                        (
                            'positions'::text,
                            'trg_paper_canary_position_identity_fence'::text,
                            27::smallint
                        ),
                        (
                            'bankroll_log'::text,
                            'trg_paper_canary_bankroll_identity_fence'::text,
                            31::smallint
                        )
                ) AS expected(relation_name, trigger_name, trigger_type)
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
                      AND trigger_row.tgfoid = to_regprocedure(
                          'public.enforce_paper_canary_execution_identity_fence()'
                      )
                      AND trigger_row.tgtype = expected.trigger_type
                      AND trigger_row.tgconstraint = 0
                )
            ) OR (
                SELECT count(*)
                FROM pg_trigger trigger_row
                WHERE NOT trigger_row.tgisinternal
                  AND trigger_row.tgfoid = to_regprocedure(
                      'public.enforce_paper_canary_execution_identity_fence()'
                  )
            ) <> 4 THEN
                RAISE EXCEPTION
                    'PAPER canary execution identity fence drifted';
            END IF;

            IF (
                SELECT count(*)
                FROM pg_proc procedure
                JOIN pg_namespace namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'public'
                  AND procedure.proname IN (
                      'enforce_paper_canary_lease_audit',
                      'enforce_paper_canary_runtime_fence',
                      'enforce_paper_canary_venue_fence',
                      'enforce_paper_canary_request_reservation',
                      'enforce_paper_canary_execution_commit_fence',
                      'enforce_paper_canary_execution_identity_fence'
                  )
                  AND pg_get_userbyid(procedure.proowner) = 'stratus'
                  AND NOT procedure.prosecdef
                  AND procedure.prorettype = 'trigger'::regtype
                  AND procedure.proconfig =
                      ARRAY['search_path=pg_catalog, public']::text[]
                  AND NOT EXISTS (
                      SELECT 1
                      FROM aclexplode(
                          COALESCE(
                              procedure.proacl,
                              acldefault('f', procedure.proowner)
                          )
                      ) privilege
                      WHERE privilege.grantee = 0
                        AND privilege.privilege_type = 'EXECUTE'
                  )
            ) <> 6 THEN
                RAISE EXCEPTION
                    'PAPER canary trigger function hardening drifted';
            END IF;
            RETURN base_result;
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
        DO $runtime_boundary_acl$
        DECLARE
            runtime_name name := (
                'stratus_runtime_' || md5(current_database())
            )::name;
        BEGIN
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'public.assert_stratus_runtime_boundary(boolean) TO %I',
                runtime_name
            );
        END
        $runtime_boundary_acl$;
        """
    )
    op.execute("SELECT public.assert_stratus_runtime_boundary(false)")


def downgrade() -> None:
    bind = op.get_bind()
    has_rows = bind.scalar(
        sa.select(sa.func.count()).select_from(
            sa.table(
                "paper_canary_safety_leases",
                sa.column("id", postgresql.BIGINT()),
            )
        )
    )
    if has_rows:
        raise RuntimeError("cannot downgrade 20260730_0047 with audited PAPER canary leases")
    op.execute(
        """
        DO $remove_paper_canary_bootstrap_audit$
        BEGIN
            -- Downgrade is supported only for empty disposable test/dev
            -- schemas. Older migration reversibility tests intentionally
            -- truncate operator_commands before walking the historical
            -- chain, so absence of this bootstrap row is acceptable here.
            -- Production rollback is forward-only and never uses downgrade.
            DELETE FROM public.operator_commands
            WHERE request_id = 'paper-canary-bootstrap-stop-0047';
        END
        $remove_paper_canary_bootstrap_audit$;
        """
    )
    op.execute("DROP FUNCTION public.assert_stratus_safety_boundary(boolean)")
    op.execute(
        """
        DO $revoke_safety_boundary$
        DECLARE
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            safety_marker text := format(
                'STRATUS managed safety group v1 database=%s',
                current_database()
            );
            safety_app_marker text := format(
                'STRATUS managed safety login v1 database=%s',
                current_database()
            );
            safety_role record;
            safety_app_role record;
        BEGIN
            SELECT * INTO safety_role
            FROM pg_roles WHERE rolname = safety_name;
            SELECT * INTO safety_app_role
            FROM pg_roles WHERE rolname = safety_app_name;
            IF safety_role.oid IS NULL OR safety_app_role.oid IS NULL
               OR shobj_description(
                   safety_role.oid, 'pg_authid'
               ) IS DISTINCT FROM safety_marker
               OR shobj_description(
                   safety_app_role.oid, 'pg_authid'
               ) IS DISTINCT FROM safety_app_marker THEN
                RAISE EXCEPTION
                    'refusing to downgrade unmarked STRATUS safety roles';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_shdepend
                WHERE refclassid = 'pg_authid'::regclass
                  AND refobjid IN (
                      safety_role.oid, safety_app_role.oid
                  )
                  AND deptype = 'o'
            ) THEN
                RAISE EXCEPTION
                    'refusing to drop safety roles that own objects';
            END IF;
            EXECUTE format(
                'ALTER ROLE %I NOLOGIN PASSWORD NULL',
                safety_app_name
            );
            EXECUTE format(
                'ALTER ROLE %I NOLOGIN PASSWORD NULL',
                safety_name
            );
            EXECUTE format(
                'REVOKE %I FROM %I',
                safety_name,
                safety_app_name
            );
            -- Clear every direct grant in the current database, including
            -- unexpected column/schema grants, after proving these managed
            -- roles do not own objects. This makes downgrade complete and
            -- leaves no dependency that can keep a login role alive.
            EXECUTE format(
                'DROP OWNED BY %I, %I',
                safety_app_name,
                safety_name
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
                current_database(),
                safety_name
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I',
                safety_name
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE '
                'public.runtime_control, public.venue_runtime_control, '
                'public.operator_commands, public.signals, public.orders '
                'FROM %I',
                safety_name
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE '
                'public.operator_commands_id_seq FROM %I',
                safety_name
            );
        END
        $revoke_safety_boundary$;
        """
    )
    op.execute("DROP FUNCTION public.safety_paper_canary_watchdog_tick()")
    op.execute("DROP FUNCTION public.safety_paper_canary_release(text, text, text, text)")
    op.execute("DROP FUNCTION public.safety_paper_canary_heartbeat(text, text, text, integer)")
    op.execute(
        "DROP FUNCTION public.safety_paper_canary_acquire("
        "text, text, text, text, text, text, text, text, text, integer)"
    )
    op.execute("DROP FUNCTION public.safety_paper_canary_ready()")
    op.execute("DROP FUNCTION public.safety_paper_canary_stop(text, text, text)")
    op.execute("DROP FUNCTION public.paper_canary_stop_core(text, text, text, text)")
    op.execute("DROP FUNCTION public.resume_paper_canary_exact(bigint)")
    op.execute("DROP FUNCTION public.engage_paper_canary_runtime_stop(text, text, text)")
    op.execute("DROP FUNCTION public.assert_stratus_runtime_boundary(boolean)")
    op.execute(
        "ALTER FUNCTION public.assert_stratus_runtime_boundary_v2(boolean) "
        "RENAME TO assert_stratus_runtime_boundary"
    )
    op.execute(
        "DROP TRIGGER trg_paper_canary_bankroll_identity_fence ON public.bankroll_log"
    )
    op.execute("DROP TRIGGER trg_paper_canary_position_identity_fence ON public.positions")
    op.execute("DROP TRIGGER trg_paper_canary_order_identity_fence ON public.orders")
    op.execute("DROP TRIGGER trg_paper_canary_signal_identity_fence ON public.signals")
    op.execute("DROP FUNCTION public.enforce_paper_canary_execution_identity_fence()")
    op.execute("DROP TRIGGER trg_paper_canary_position_commit_fence ON public.positions")
    op.execute("DROP TRIGGER trg_paper_canary_fill_leg_commit_fence ON public.execution_fill_legs")
    op.execute(
        "DROP TRIGGER trg_paper_canary_intent_commit_fence ON public.execution_order_intents"
    )
    op.execute("DROP TRIGGER trg_paper_canary_order_commit_fence ON public.orders")
    op.execute("DROP TRIGGER trg_paper_canary_signal_commit_fence ON public.signals")
    op.execute("DROP FUNCTION public.enforce_paper_canary_execution_commit_fence()")
    op.execute("DROP TRIGGER trg_paper_canary_request_reservation ON public.operator_commands")
    op.execute("DROP FUNCTION public.enforce_paper_canary_request_reservation()")
    op.execute("DROP FUNCTION public.paper_canary_resume_command_authorized(text, text, text)")
    op.execute("DROP FUNCTION public.paper_canary_resume_request_reserved(text)")
    op.execute("DROP FUNCTION public.paper_canary_stop_request_reserved(text)")
    op.execute("DROP TRIGGER trg_paper_canary_venue_fence ON public.venue_runtime_control")
    op.execute("DROP FUNCTION public.enforce_paper_canary_venue_fence()")
    op.execute("DROP TRIGGER trg_paper_canary_runtime_fence ON public.runtime_control")
    op.execute("DROP FUNCTION public.enforce_paper_canary_runtime_fence()")
    op.drop_constraint(
        "ck_operator_commands_lifecycle",
        "operator_commands",
        type_="check",
    )
    op.drop_constraint(
        "ck_runtime_control_singleton_id",
        "runtime_control",
        type_="check",
    )
    op.drop_index(
        "ix_orders_live_open_stop_cleanup",
        table_name="orders",
    )
    op.drop_index(
        "ix_signals_pending_stop_cleanup",
        table_name="signals",
    )
    op.execute("DROP FUNCTION public.paper_canary_execution_authorized()")
    op.execute("DROP FUNCTION public.paper_canary_active_lease_exists()")
    op.execute("DROP FUNCTION public.paper_canary_fence_armed()")
    op.execute("DROP TRIGGER trg_paper_canary_lease_audit ON public.paper_canary_safety_leases")
    op.execute("DROP FUNCTION public.enforce_paper_canary_lease_audit()")
    op.drop_index(
        "ix_paper_canary_active_expiry",
        table_name="paper_canary_safety_leases",
    )
    op.drop_index(
        "uq_paper_canary_single_active_lease",
        table_name="paper_canary_safety_leases",
    )
    op.drop_table("paper_canary_safety_leases")
    op.execute(
        """
        DO $drop_safety_roles$
        DECLARE
            safety_name name := (
                'stratus_safety_' || md5(current_database())
            )::name;
            safety_app_name name := (
                'stratus_safety_app_' || md5(current_database())
            )::name;
            safety_marker text := format(
                'STRATUS managed safety group v1 database=%s',
                current_database()
            );
            safety_app_marker text := format(
                'STRATUS managed safety login v1 database=%s',
                current_database()
            );
            safety_role record;
            safety_app_role record;
        BEGIN
            SELECT * INTO safety_role
            FROM pg_roles WHERE rolname = safety_name;
            SELECT * INTO safety_app_role
            FROM pg_roles WHERE rolname = safety_app_name;
            IF safety_role.oid IS NULL OR safety_app_role.oid IS NULL
               OR shobj_description(
                   safety_role.oid, 'pg_authid'
               ) IS DISTINCT FROM safety_marker
               OR shobj_description(
                   safety_app_role.oid, 'pg_authid'
               ) IS DISTINCT FROM safety_app_marker
               OR safety_role.rolcanlogin
               OR safety_app_role.rolcanlogin
               OR EXISTS (
                   SELECT 1
                   FROM pg_authid auth_row
                   WHERE auth_row.rolname IN (
                       safety_name, safety_app_name
                   )
                     AND (
                         auth_row.rolcanlogin
                         OR auth_row.rolpassword IS NOT NULL
                     )
               ) THEN
                RAISE EXCEPTION
                    'STRATUS safety roles are not inert before drop';
            END IF;
            EXECUTE format('DROP ROLE %I', safety_app_name);
            EXECUTE format('DROP ROLE %I', safety_name);
            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname IN (safety_name, safety_app_name)
            ) THEN
                RAISE EXCEPTION
                    'STRATUS safety roles survived downgrade';
            END IF;
        END
        $drop_safety_roles$;
        """
    )
