from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


def _alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _migration_output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


async def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(_alembic, *arguments)


async def _restore_head() -> None:
    restored = await _run_alembic("upgrade", "head")
    assert restored.returncode == 0, _migration_output(restored)


async def _assert_revision(
    db_sessions: SessionFactory,
    expected: str,
) -> None:
    async with db_sessions() as owner:
        assert await owner.scalar(text("SELECT version_num FROM alembic_version")) == expected


async def _downgrade_to_0046(db_sessions: SessionFactory) -> None:
    downgraded = await _run_alembic("downgrade", "20260730_0046")
    assert downgraded.returncode == 0, _migration_output(downgraded)
    await _assert_revision(db_sessions, "20260730_0046")


async def test_0047_execution_commit_fences_are_exactly_deferred(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions() as owner:
        rows = (
            await owner.execute(
                text(
                    """
                    SELECT relation.relname, trigger_row.tgname,
                           trigger_row.tgtype, trigger_row.tgenabled::text,
                           trigger_row.tgconstraint <> 0,
                           trigger_row.tgdeferrable,
                           trigger_row.tginitdeferred,
                           trigger_row.tgfoid::regprocedure::text
                    FROM pg_trigger trigger_row
                    JOIN pg_class relation
                      ON relation.oid = trigger_row.tgrelid
                    JOIN pg_namespace namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND NOT trigger_row.tgisinternal
                      AND trigger_row.tgfoid = to_regprocedure(
                          'public.enforce_paper_canary_execution_commit_fence()'
                      )
                    ORDER BY relation.relname
                    """
                )
            )
        ).all()
    expected = {
        "signals": ("trg_paper_canary_signal_commit_fence", 21),
        "orders": ("trg_paper_canary_order_commit_fence", 21),
        "execution_order_intents": ("trg_paper_canary_intent_commit_fence", 5),
        "execution_fill_legs": ("trg_paper_canary_fill_leg_commit_fence", 5),
        "positions": ("trg_paper_canary_position_commit_fence", 21),
    }
    assert len(rows) == len(expected)
    for row in rows:
        trigger_name, trigger_type = expected[row[0]]
        assert row[1:] == (
            trigger_name,
            trigger_type,
            "O",
            True,
            True,
            True,
            "enforce_paper_canary_execution_commit_fence()",
        )
    async with db_sessions() as owner:
        identity_rows = (
            await owner.execute(
                text(
                    """
                    SELECT relation.relname, trigger_row.tgname,
                           trigger_row.tgtype, trigger_row.tgenabled::text,
                           trigger_row.tgconstraint,
                           trigger_row.tgfoid::regprocedure::text
                    FROM pg_trigger trigger_row
                    JOIN pg_class relation
                      ON relation.oid = trigger_row.tgrelid
                    JOIN pg_namespace namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND NOT trigger_row.tgisinternal
                      AND trigger_row.tgfoid = to_regprocedure(
                          'public.enforce_paper_canary_execution_identity_fence()'
                      )
                    ORDER BY relation.relname
                    """
                )
            )
        ).all()
    assert identity_rows == [
        (
            "bankroll_log",
            "trg_paper_canary_bankroll_identity_fence",
            31,
            "O",
            0,
            "enforce_paper_canary_execution_identity_fence()",
        ),
        (
            "orders",
            "trg_paper_canary_order_identity_fence",
            27,
            "O",
            0,
            "enforce_paper_canary_execution_identity_fence()",
        ),
        (
            "positions",
            "trg_paper_canary_position_identity_fence",
            27,
            "O",
            0,
            "enforce_paper_canary_execution_identity_fence()",
        ),
        (
            "signals",
            "trg_paper_canary_signal_identity_fence",
            27,
            "O",
            0,
            "enforce_paper_canary_execution_identity_fence()",
        ),
    ]


async def test_0047_upgrade_fail_closes_realistic_unsafe_0046_state(
    db_sessions: SessionFactory,
) -> None:
    await _downgrade_to_0046(db_sessions)
    try:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    """
                    UPDATE runtime_control
                    SET mode = 'live'::execution_mode,
                        kill_switch = false,
                        live_confirmation_until = clock_timestamp()
                            + interval '10 minutes',
                        halted_reason = NULL
                    WHERE id = 1
                    """
                )
            )
            await owner.execute(
                text(
                    """
                    UPDATE venue_runtime_control
                    SET enabled = true,
                        mode = 'live'::venue_mode,
                        kill_switch = false,
                        live_confirmation_until = clock_timestamp()
                            + interval '10 minutes',
                        halted_reason = NULL
                    WHERE venue = 'polymarket'
                    """
                )
            )
            await owner.execute(
                text(
                    """
                    UPDATE venue_runtime_control
                    SET enabled = false,
                        mode = 'live'::venue_mode,
                        kill_switch = false,
                        live_confirmation_until = clock_timestamp()
                            + interval '10 minutes',
                        halted_reason = NULL
                    WHERE venue = 'binance_spot'
                    """
                )
            )
            market_id = await owner.scalar(
                text(
                    """
                    INSERT INTO markets(
                        polymarket_id, event_id, market_date, kind,
                        rules_raw, status, resolves_at
                    ) VALUES (
                        'migration-0047-unsafe-market',
                        'migration-0047-unsafe-event',
                        DATE '2026-08-13',
                        'high',
                        'Migration 0047 fail-closed integration fixture.',
                        'active',
                        TIMESTAMPTZ '2026-08-14 00:00:00+00'
                    )
                    RETURNING id
                    """
                )
            )
            assert isinstance(market_id, int)
            signal_ids = (
                (
                    await owner.execute(
                        text(
                            """
                        INSERT INTO signals(
                            market_id, ts, side, model_prob, market_price,
                            edge, kelly_frac, size_usd, confidence, status
                        ) VALUES
                            (:market_id, TIMESTAMPTZ '2026-08-13 09:00:00+00',
                             'buy_yes', 0.70, 0.50, 0.20, 0.02, 10, 'A',
                             'pending'),
                            (:market_id, TIMESTAMPTZ '2026-08-13 09:01:00+00',
                             'buy_no', 0.30, 0.45, 0.15, 0.02, 11, 'B',
                             'pending'),
                            (:market_id, TIMESTAMPTZ '2026-08-13 09:02:00+00',
                             'buy_yes', 0.65, 0.48, 0.17, 0.02, 12, 'A',
                             'pending')
                        RETURNING id
                        """
                        ),
                        {"market_id": market_id},
                    )
                )
                .scalars()
                .all()
            )
            assert len(signal_ids) == 3
            await owner.execute(
                text(
                    """
                    INSERT INTO orders(
                        signal_id, mode, clob_order_id, token_id, side,
                        price, size_usd, filled_usd, status
                    ) VALUES
                        (:open_signal, 'live', 'live-open-before-0047',
                         'yes-token', 'buy_yes', 0.51, 10, 0, 'open'),
                        (:pending_signal, 'live', 'live-pending-before-0047',
                         'no-token', 'buy_no', 0.46, 11, 0, 'pending'),
                        (:paper_signal, 'paper', 'paper-open-before-0047',
                         'yes-token', 'buy_yes', 0.49, 12, 0, 'open')
                    """
                ),
                {
                    "open_signal": signal_ids[0],
                    "pending_signal": signal_ids[1],
                    "paper_signal": signal_ids[2],
                },
            )
            await owner.execute(
                text(
                    """
                    INSERT INTO operator_commands(
                        request_id, command, payload, actor, reason,
                        status, detail, executed_at
                    ) VALUES
                        ('pre-0047-pending-resume', 'resume',
                         '{"venue":"polymarket"}'::jsonb,
                         'integration-suite', 'Pre-existing pending command.',
                         'pending', NULL, NULL),
                        ('pre-0047-succeeded-stop', 'stop',
                         '{"venue":"all"}'::jsonb,
                         'integration-suite', 'Pre-existing terminal command.',
                         'succeeded', 'already handled', clock_timestamp())
                    """
                )
            )

        upgraded = await _run_alembic("upgrade", "head")
        assert upgraded.returncode == 0, _migration_output(upgraded)
        await _assert_revision(db_sessions, "20260730_0047")

        async with db_sessions() as owner:
            runtime = (
                await owner.execute(
                    text(
                        """
                        SELECT mode::text, kill_switch,
                               live_confirmation_until IS NULL
                        FROM runtime_control
                        WHERE id = 1
                        """
                    )
                )
            ).one()
            assert runtime == ("paper", True, True)
            venues = (
                await owner.execute(
                    text(
                        """
                        SELECT venue::text, enabled, mode::text, kill_switch,
                               live_confirmation_until IS NULL
                        FROM venue_runtime_control
                        WHERE venue::text IN ('polymarket', 'binance_spot')
                        ORDER BY venue::text
                        """
                    )
                )
            ).all()
            assert venues == [
                ("binance_spot", True, "backtest", True, True),
                ("polymarket", True, "paper", True, True),
            ]
            signals = (
                await owner.execute(
                    text(
                        """
                        SELECT status::text, skip_reason, count(*)
                        FROM signals
                        GROUP BY status::text, skip_reason
                        """
                    )
                )
            ).all()
            assert signals == [("expired", "paper_canary_migration_fail_closed", 3)]
            orders = (
                await owner.execute(
                    text(
                        """
                        SELECT mode::text, clob_order_id, status
                        FROM orders
                        ORDER BY clob_order_id
                        """
                    )
                )
            ).all()
            assert orders == [
                ("live", "live-open-before-0047", "cancel_requested"),
                ("live", "live-pending-before-0047", "cancel_requested"),
                ("paper", "paper-open-before-0047", "open"),
            ]
            preserved_commands = (
                await owner.execute(
                    text(
                        """
                        SELECT request_id, status, detail
                        FROM operator_commands
                        WHERE request_id LIKE 'pre-0047-%'
                        ORDER BY request_id
                        """
                    )
                )
            ).all()
            assert preserved_commands == [
                ("pre-0047-pending-resume", "pending", None),
                ("pre-0047-succeeded-stop", "succeeded", "already handled"),
            ]
            bootstrap_rows = (
                await owner.execute(
                    text(
                        """
                        SELECT command, payload, actor, reason, status,
                               detail, executed_at IS NOT NULL AS executed
                        FROM operator_commands
                        WHERE request_id =
                            'paper-canary-bootstrap-stop-0047'
                        """
                    )
                )
            ).all()
            assert len(bootstrap_rows) == 1
            bootstrap = bootstrap_rows[0]
            assert bootstrap.command == "stop"
            assert bootstrap.payload == {"venue": "all"}
            assert bootstrap.actor == "migration-0047"
            assert bootstrap.reason == ("Fail closed before DB-clock PAPER canary authority.")
            assert bootstrap.status == "succeeded"
            assert bootstrap.executed is True
            assert "expired signals: 3" in bootstrap.detail
            assert "cancel-requested LIVE orders: 2" in bootstrap.detail
            assert await owner.scalar(text("SELECT paper_canary_execution_authorized()")) is False
    finally:
        await _restore_head()


async def test_0047_downgrade_refuses_an_existing_audited_lease(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                """
                WITH database_clock AS (
                    SELECT clock_timestamp() AS now
                )
                INSERT INTO paper_canary_safety_leases(
                    lease_id, run_id, acquire_request_id, resume_request_id,
                    resume_actor, resume_reason, watchdog_stop_request_id,
                    direct_stop_request_id, release_request_id, nonce_sha256,
                    state, acquired_at, heartbeat_at, watchdog_at, expires_at
                )
                SELECT
                    'downgrade-proof-lease', 'downgrade-proof-run',
                    'downgrade-proof-acquire', 'downgrade-proof-resume',
                    'integration-suite',
                    'Prove migration 0047 cannot discard an audited lease.',
                    'downgrade-proof-watchdog', 'downgrade-proof-direct',
                    'downgrade-proof-release', repeat('a', 64), 'active',
                    now, now, now, now + interval '20 seconds'
                FROM database_clock
                """
            )
        )

    downgraded = await _run_alembic("downgrade", "20260730_0046")
    assert downgraded.returncode != 0
    assert "cannot downgrade 20260730_0047 with audited PAPER canary leases" in (
        _migration_output(downgraded)
    )
    await _assert_revision(db_sessions, "20260730_0047")
    async with db_sessions() as owner:
        assert (
            int(await owner.scalar(text("SELECT count(*) FROM paper_canary_safety_leases")) or 0)
            == 1
        )


async def test_0047_upgrade_rejects_bootstrap_audit_identity_collision(
    db_sessions: SessionFactory,
) -> None:
    await _downgrade_to_0046(db_sessions)
    try:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    """
                    INSERT INTO operator_commands(
                        request_id, command, payload, actor, reason, status
                    ) VALUES (
                        'paper-canary-bootstrap-stop-0047',
                        'resume',
                        '{"venue":"polymarket"}'::jsonb,
                        'identity-collision-fixture',
                        'This row must make migration 0047 fail atomically.',
                        'pending'
                    )
                    """
                )
            )

        upgraded = await _run_alembic("upgrade", "head")
        assert upgraded.returncode != 0
        assert "paper-canary-bootstrap-stop-0047" in _migration_output(upgraded)
        await _assert_revision(db_sessions, "20260730_0046")
        async with db_sessions() as owner:
            assert (
                await owner.scalar(text("SELECT to_regclass('public.paper_canary_safety_leases')"))
                is None
            )
            collision = (
                await owner.execute(
                    text(
                        """
                        SELECT command, actor, status
                        FROM operator_commands
                        WHERE request_id =
                            'paper-canary-bootstrap-stop-0047'
                        """
                    )
                )
            ).one()
            assert collision == (
                "resume",
                "identity-collision-fixture",
                "pending",
            )

        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    """
                    DELETE FROM operator_commands
                    WHERE request_id =
                        'paper-canary-bootstrap-stop-0047'
                    """
                )
            )
    finally:
        await _restore_head()


@pytest.mark.parametrize(
    ("missing_control", "expected_error"),
    [
        ("runtime", "requires runtime_control id=1"),
        ("polymarket", "requires both venue controls"),
        ("binance_spot", "requires both venue controls"),
    ],
)
async def test_0047_upgrade_rejects_missing_required_control_rows(
    db_sessions: SessionFactory,
    missing_control: str,
    expected_error: str,
) -> None:
    await _downgrade_to_0046(db_sessions)
    try:
        async with db_sessions.begin() as owner:
            if missing_control == "runtime":
                await owner.execute(text("DELETE FROM runtime_control WHERE id = 1"))
            else:
                await owner.execute(
                    text("DELETE FROM venue_runtime_control WHERE venue::text = :venue"),
                    {"venue": missing_control},
                )

        upgraded = await _run_alembic("upgrade", "head")
        assert upgraded.returncode != 0
        assert expected_error in _migration_output(upgraded)
        await _assert_revision(db_sessions, "20260730_0046")
        async with db_sessions() as owner:
            assert (
                await owner.scalar(text("SELECT to_regclass('public.paper_canary_safety_leases')"))
                is None
            )

        async with db_sessions.begin() as owner:
            if missing_control == "runtime":
                await owner.execute(
                    text(
                        """
                        INSERT INTO runtime_control(
                            id, kill_switch, mode, live_confirmation_until,
                            halted_reason, updated_at
                        ) VALUES (
                            1, true, 'paper'::execution_mode, NULL, NULL,
                            clock_timestamp()
                        )
                        """
                    )
                )
            else:
                mode = "paper" if missing_control == "polymarket" else "backtest"
                risk_config_version = (
                    "legacy" if missing_control == "polymarket" else "v1-conservative"
                )
                await owner.execute(
                    text(
                        """
                        INSERT INTO venue_runtime_control(
                            venue, enabled, mode, kill_switch, halted_reason,
                            live_confirmation_until, strategy_version,
                            risk_config_version, last_reconciled_at, updated_at
                        ) VALUES (
                            CAST(:venue AS trading_venue), true,
                            CAST(:mode AS venue_mode), true, NULL, NULL, NULL,
                            :risk_config_version, NULL, clock_timestamp()
                        )
                        """
                    ),
                    {
                        "venue": missing_control,
                        "mode": mode,
                        "risk_config_version": risk_config_version,
                    },
                )
    finally:
        await _restore_head()


async def test_0047_empty_downgrade_reupgrade_is_fresh_and_drops_safety_login(
    db_sessions: SessionFactory,
) -> None:
    # The per-test reset deliberately removes the migration audit. Recreate a
    # clearly old exact identity so downgrade must remove it and re-upgrade
    # must generate a genuinely fresh record rather than silently reuse it.
    async with db_sessions.begin() as owner:
        safety_app_name = await owner.scalar(
            text("SELECT 'stratus_safety_app_' || md5(current_database())")
        )
        assert isinstance(safety_app_name, str)
        await owner.execute(
            text(
                """
                INSERT INTO operator_commands(
                    request_id, command, payload, actor, reason,
                    status, detail, created_at, executed_at
                ) VALUES (
                    'paper-canary-bootstrap-stop-0047',
                    'stop',
                    '{"venue":"all"}'::jsonb,
                    'migration-0047',
                    'Fail closed before DB-clock PAPER canary authority.',
                    'succeeded',
                    'old bootstrap row must not survive reapplication',
                    TIMESTAMPTZ '2000-01-01 00:00:00+00',
                    TIMESTAMPTZ '2000-01-01 00:00:00+00'
                )
                """
            )
        )
        old_audit_id = await owner.scalar(
            text(
                "SELECT id FROM operator_commands "
                "WHERE request_id='paper-canary-bootstrap-stop-0047'"
            )
        )
        assert isinstance(old_audit_id, int)
        await owner.execute(
            text(
                f'ALTER ROLE "{safety_app_name}" LOGIN '
                "PASSWORD 'temporary-downgrade-proof-password'"
            )
        )

    downgraded = await _run_alembic("downgrade", "20260730_0046")
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
    try:
        async with db_sessions() as owner:
            assert (
                await owner.scalar(text("SELECT version_num FROM alembic_version"))
                == "20260730_0046"
            )
            assert (
                await owner.scalar(text("SELECT to_regclass('public.paper_canary_safety_leases')"))
                is None
            )
            assert (
                int(
                    await owner.scalar(
                        text(
                            "SELECT count(*) FROM operator_commands "
                            "WHERE request_id="
                            "'paper-canary-bootstrap-stop-0047'"
                        )
                    )
                    or 0
                )
                == 0
            )
            assert (
                int(
                    await owner.scalar(
                        text(
                            "SELECT count(*) FROM pg_roles "
                            "WHERE rolname IN ("
                            "'stratus_safety_' || md5(current_database()), "
                            "'stratus_safety_app_' || md5(current_database())"
                            ")"
                        )
                    )
                    or 0
                )
                == 0
            )
            assert (
                await owner.scalar(
                    text(
                        "SELECT to_regprocedure("
                        "'public.assert_stratus_runtime_boundary(boolean)') "
                        "IS NOT NULL"
                    )
                )
                is True
            )
            assert (
                await owner.scalar(
                    text(
                        "SELECT to_regprocedure("
                        "'public.assert_stratus_runtime_boundary_v2(boolean)') "
                        "IS NULL"
                    )
                )
                is True
            )

        upgraded = await _run_alembic("upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

        async with db_sessions() as owner:
            fresh = (
                await owner.execute(
                    text(
                        """
                        SELECT id, created_at, executed_at, detail
                        FROM operator_commands
                        WHERE request_id =
                            'paper-canary-bootstrap-stop-0047'
                        """
                    )
                )
            ).one()
            assert fresh.id > old_audit_id
            assert fresh.created_at.year > 2000
            assert fresh.executed_at.year > 2000
            assert "forced global/Polymarket PAPER kills ON" in fresh.detail
            assert await owner.scalar(text("SELECT paper_canary_execution_authorized()")) is False
            assert (
                await owner.execute(
                    text(
                        """
                        SELECT mode::text, kill_switch,
                               live_confirmation_until IS NULL
                        FROM runtime_control
                        WHERE id=1
                        """
                    )
                )
            ).one() == ("paper", True, True)
            venues = (
                await owner.execute(
                    text(
                        """
                        SELECT venue::text, enabled, mode::text,
                               kill_switch,
                               live_confirmation_until IS NULL
                        FROM venue_runtime_control
                        WHERE venue::text IN (
                            'polymarket', 'binance_spot'
                        )
                        ORDER BY venue::text
                        """
                    )
                )
            ).all()
            assert venues == [
                ("binance_spot", True, "backtest", True, True),
                ("polymarket", True, "paper", True, True),
            ]
            safety_roles = (
                await owner.execute(
                    text(
                        """
                        SELECT rolname, rolcanlogin,
                               rolpassword IS NULL AS password_is_null
                        FROM pg_authid
                        WHERE rolname IN (
                            'stratus_safety_' || md5(current_database()),
                            'stratus_safety_app_' || md5(current_database())
                        )
                        ORDER BY rolname
                        """
                    )
                )
            ).all()
            assert len(safety_roles) == 2
            assert all(not role.rolcanlogin and role.password_is_null for role in safety_roles)
            assert await owner.scalar(text("SELECT assert_stratus_runtime_boundary(false)")) is True
            assert await owner.scalar(text("SELECT assert_stratus_safety_boundary(false)")) is True
    finally:
        await _restore_head()
