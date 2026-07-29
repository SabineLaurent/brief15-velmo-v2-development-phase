"""The SQL business double: port conformity, authorization, seed, `--reset`.

Fully offline — in-memory SQLite, no LLM, no key, no network. What these lock
down is the claim the `actions/` port makes: two adapters, interchangeable, and
the tools cannot tell them apart. A test that only exercised `SqlSupportBackend`
in isolation would miss the interesting failure, which is the two adapters
drifting apart.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from support_agent.actions.backend import (
    InMemorySupportBackend,
    SupportBackend,
    _ticket_id,
)
from support_agent.actions.sql.backend import SqlSupportBackend
from support_agent.actions.sql.sampledata import DEMO_CUSTOMER_ID, seed
from support_agent.actions.sql.schema import Base, Customer
from support_agent.actions.sql.seed import main as seed_main

# A second customer, used for the cross-customer isolation checks.
OTHER_CUSTOMER_ID = "C-sophie-martin"
# Orders from the reference dataset: one shipped (has a shipment row), one
# prepared (has none — the outer join must still return it).
SHIPPED_ORDER = "O-2024-0103"
PREPARED_ORDER = "O-2024-0101"
OTHER_CUSTOMER_ORDER = "O-2024-0110"


@pytest.fixture
def sessions() -> sessionmaker:
    """A seeded in-memory shop.

    `StaticPool` is not needed because the sessionmaker binds one engine and
    SQLite in-memory databases are per-connection: a single engine with the
    default pool reuses the same connection for these serial calls.
    """
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        seed(session)
    return factory


@pytest.fixture
def backend(sessions: sessionmaker) -> SqlSupportBackend:
    return SqlSupportBackend(sessions)


# --- The port contract -----------------------------------------------------


def test_satisfies_the_support_backend_port(backend: SqlSupportBackend) -> None:
    # `SupportBackend` is runtime_checkable, so this checks the methods exist.
    # It is a smoke test, not a signature check — the behavioural parity tests
    # below are what actually keep the two adapters aligned.
    assert isinstance(backend, SupportBackend)


def test_both_adapters_agree_on_the_shape_of_an_answer(
    backend: SqlSupportBackend,
) -> None:
    """The two adapters must return the same TYPE, field for field.

    This is the test that catches drift. `_format_order` in `tools.py` reads
    `.status`, `.carrier`, `.tracking_number`, `.estimated_delivery` off whatever
    the backend returned; an adapter returning a SQLAlchemy row, or a string
    status enum instead of a plain `str`, would render as garbage in the prompt
    without raising anything.
    """
    sql_order = backend.get_order_status(SHIPPED_ORDER, DEMO_CUSTOMER_ID)
    mem_order = InMemorySupportBackend().get_order_status("CMD-1001", "demo-user")
    assert sql_order is not None and mem_order is not None
    assert type(sql_order) is type(mem_order)
    # `status` must be a plain string, not the SQLAlchemy Enum member.
    assert type(sql_order.status) is str
    assert sql_order.status == "shipped"


# --- Authorization ---------------------------------------------------------


def test_returns_an_order_that_belongs_to_the_customer(
    backend: SqlSupportBackend,
) -> None:
    order = backend.get_order_status(SHIPPED_ORDER, DEMO_CUSTOMER_ID)
    assert order is not None
    assert order.order_id == SHIPPED_ORDER
    assert order.owner_id == DEMO_CUSTOMER_ID
    assert order.carrier == "Colissimo"
    assert order.tracking_number == "6A1234567890"


def test_refuses_another_customers_order(backend: SqlSupportBackend) -> None:
    """The core isolation guarantee: a foreign order is indistinguishable from
    an unknown one, so a caller cannot enumerate valid ids."""
    foreign = backend.get_order_status(OTHER_CUSTOMER_ORDER, DEMO_CUSTOMER_ID)
    unknown = backend.get_order_status("O-9999-9999", DEMO_CUSTOMER_ID)
    assert foreign is None
    assert unknown is None
    # ...and the order really does exist, for its actual owner. Without this the
    # test above would also pass against a backend that returns None for
    # everything.
    assert backend.get_order_status(OTHER_CUSTOMER_ORDER, OTHER_CUSTOMER_ID) is not None


def test_order_without_a_shipment_still_resolves(backend: SqlSupportBackend) -> None:
    """A prepared order has no `shipments` row: the outer join must not drop it.

    An inner join here would make every not-yet-shipped order look like it does
    not exist — the exact question a customer asks most often.
    """
    order = backend.get_order_status(PREPARED_ORDER, DEMO_CUSTOMER_ID)
    assert order is not None
    assert order.status == "prepared"
    assert order.carrier is None
    assert order.tracking_number is None


def test_order_id_is_normalised(backend: SqlSupportBackend) -> None:
    assert backend.get_order_status("  o-2024-0103 ", DEMO_CUSTOMER_ID) is not None


def test_delivered_order_reports_the_actual_delivery_date(
    backend: SqlSupportBackend,
) -> None:
    order = backend.get_order_status("O-2024-0105", DEMO_CUSTOMER_ID)
    assert order is not None
    assert order.status == "delivered"
    assert order.estimated_delivery == "2024-04-21"  # actual, not the 04-20 estimate


# --- Tickets ---------------------------------------------------------------


def test_create_ticket_is_idempotent(backend: SqlSupportBackend) -> None:
    """Same (customer, subject, body) twice = one ticket, not two.

    This is what stops a retried tool call — a network blip, a model repeating
    itself — from opening duplicate tickets for one customer request.
    """
    first = backend.create_ticket(DEMO_CUSTOMER_ID, "Colis en retard", "Rien reçu.")
    second = backend.create_ticket(DEMO_CUSTOMER_ID, "Colis en retard", "Rien reçu.")
    assert first.ticket_id == second.ticket_id
    tickets = backend.list_tickets(DEMO_CUSTOMER_ID)
    assert [t.ticket_id for t in tickets].count(first.ticket_id) == 1


def test_created_ticket_survives_a_new_backend_instance(
    sessions: sessionmaker,
) -> None:
    """Durability: the whole point of this adapter over the in-memory one."""
    created = SqlSupportBackend(sessions).create_ticket(
        DEMO_CUSTOMER_ID, "Question taille", "Le M taille petit ?"
    )
    reopened = SqlSupportBackend(sessions).list_tickets(DEMO_CUSTOMER_ID)
    assert created.ticket_id in {t.ticket_id for t in reopened}


def test_list_tickets_is_scoped_to_the_customer(backend: SqlSupportBackend) -> None:
    backend.create_ticket(DEMO_CUSTOMER_ID, "Privé", "Ne doit pas fuiter.")
    others = backend.list_tickets(OTHER_CUSTOMER_ID)
    assert all(t.user_id == OTHER_CUSTOMER_ID for t in others)
    assert "Privé" not in {t.subject for t in others}


def test_seeded_tickets_use_the_content_derived_id(
    backend: SqlSupportBackend,
) -> None:
    """A seeded ticket must be indistinguishable from a created one.

    If seed rows used arbitrary ids, a customer re-reporting the seeded problem
    would compute a different id and open a DUPLICATE of a ticket that is already
    in their history — defeating the recurrence detection the seed exists for.
    """
    tickets = backend.list_tickets(DEMO_CUSTOMER_ID)
    assert tickets, "the reference dataset must seed a past ticket for the demo customer"
    seeded = tickets[0]
    assert seeded.ticket_id == _ticket_id(seeded.user_id, seeded.subject, seeded.body)
    # And re-creating it returns the SAME ticket rather than a second one.
    again = backend.create_ticket(seeded.user_id, seeded.subject, seeded.body)
    assert again.ticket_id == seeded.ticket_id
    assert again.status == "resolved"  # the seeded status, not a fresh "open"


def test_ticket_for_an_unknown_customer_gets_a_placeholder(
    backend: SqlSupportBackend, sessions: sessionmaker
) -> None:
    """`user_id` comes from the runtime context and may be any string.

    `tickets.customer_id` is a foreign key, so an unknown customer would fail the
    insert on Postgres and dangle silently on SQLite. The double provisions a
    placeholder instead — documented leniency, not an accident.
    """
    ticket = backend.create_ticket("demo-user", "Bonjour", "Test.")
    assert ticket.user_id == "demo-user"
    with sessions() as session:
        assert session.get(Customer, "demo-user") is not None


# --- The seed CLI ----------------------------------------------------------


def test_seed_cli_populates_then_refuses_to_double_seed(tmp_path) -> None:
    db = tmp_path / "shop.db"
    assert seed_main(["--db-path", str(db)]) == 0

    from support_agent.actions.sql.schema import session_factory_for_path

    factory = session_factory_for_path(str(db))
    with factory() as session:
        first_count = len(session.scalars(select(Customer)).all())
    assert first_count == 10

    # Running it again must not duplicate anything (it is the command a
    # newcomer runs twice because they are not sure it worked the first time).
    assert seed_main(["--db-path", str(db)]) == 0
    with factory() as session:
        assert len(session.scalars(select(Customer)).all()) == first_count


def test_seed_reset_rebuilds_and_drops_conversation_tickets(tmp_path) -> None:
    """`--reset` is the answer this project gives instead of migrations.

    It must really drop: a `create_all`-only "reset" would leave the old rows in
    place and report success, which is precisely the silent staleness the flag
    exists to fix.
    """
    db = tmp_path / "shop.db"
    seed_main(["--db-path", str(db)])

    from support_agent.actions.sql.schema import session_factory_for_path

    backend = SqlSupportBackend(session_factory_for_path(str(db)))
    created = backend.create_ticket(DEMO_CUSTOMER_ID, "Ticket de conversation", "Corps.")
    assert created.ticket_id in {t.ticket_id for t in backend.list_tickets(DEMO_CUSTOMER_ID)}

    assert seed_main(["--db-path", str(db), "--reset"]) == 0

    rebuilt = SqlSupportBackend(session_factory_for_path(str(db)))
    remaining = {t.ticket_id for t in rebuilt.list_tickets(DEMO_CUSTOMER_ID)}
    assert created.ticket_id not in remaining, "--reset must drop conversation tickets"
    assert remaining, "...but the seeded history must be back"


def test_from_path_refuses_an_unseeded_shop(tmp_path) -> None:
    """An empty shop answers 'no order found' for everything — a plausible-looking
    failure that reads as a broken agent. Fail at startup instead, with the fix."""
    with pytest.raises(RuntimeError, match="make seed"):
        SqlSupportBackend.from_path(str(tmp_path / "empty.db"))


# --- Wiring ----------------------------------------------------------------


def test_get_backend_honours_the_config_switch(monkeypatch, tmp_path) -> None:
    """`SUPPORT_BACKEND` picks the adapter, and an unknown value is an error.

    Falling back to the demo adapter on a typo would be the dangerous behaviour:
    it looks like it worked while serving three fake orders.
    """
    from support_agent.actions import backend as backend_module
    from support_agent.config import get_settings

    def use(value: str) -> None:
        monkeypatch.setenv("SUPPORT_BACKEND", value)
        get_settings.cache_clear()
        backend_module.reset_backend_cache()

    use("memory")
    assert isinstance(backend_module.get_backend(), InMemorySupportBackend)

    db = tmp_path / "shop.db"
    seed_main(["--db-path", str(db)])
    monkeypatch.setenv("SHOP_DB_PATH", str(db))
    use("sqlite")
    assert isinstance(backend_module.get_backend(), SqlSupportBackend)

    use("postgres")  # plausible-looking typo: it is the OTHER switch's value
    with pytest.raises(ValueError, match="Unknown SUPPORT_BACKEND"):
        backend_module.get_backend()

    # Leave the process-wide caches clean for the rest of the suite.
    monkeypatch.delenv("SUPPORT_BACKEND", raising=False)
    monkeypatch.delenv("SHOP_DB_PATH", raising=False)
    get_settings.cache_clear()
    backend_module.reset_backend_cache()


def test_tool_description_advertises_the_adapters_id_shape(
    backend: SqlSupportBackend,
) -> None:
    """The order-id example shown to the model must match the live adapter.

    Shown `CMD-1001` while the shop uses `O-2024-0103`, the model invents ids in
    the wrong shape when the customer has not given one — a prompt bug with no
    stack trace.
    """
    from support_agent.actions.tools import build_action_tools

    sql_tools = {t.name: t for t in build_action_tools(backend)}
    mem_tools = {t.name: t for t in build_action_tools(InMemorySupportBackend())}
    assert "O-2024-0103" in sql_tools["get_order_status"].description
    assert "CMD-1001" in mem_tools["get_order_status"].description
