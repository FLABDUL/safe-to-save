import pytest

from safe_to_save.db import isolated_dsn


@pytest.mark.parametrize("dsn", [
    "postgresql://user@localhost:55433/safe_to_save",
    "postgresql://user@127.0.0.1:5432/safe_to_save",
    "postgresql://user@127.0.0.1:55433/other",
    "host=127.0.0.1 hostaddr=192.0.2.1 port=55433 dbname=safe_to_save",
    "service=other host=127.0.0.1 port=55433 dbname=safe_to_save",
])
def test_database_boundary_rejects_other_targets(dsn: str) -> None:
    with pytest.raises(ValueError, match="isolated"):
        isolated_dsn(dsn)


def test_database_boundary_pins_host_address_against_environment_override() -> None:
    from psycopg.conninfo import conninfo_to_dict

    actual = conninfo_to_dict(isolated_dsn(
        "postgresql://user@127.0.0.1:55433/safe_to_save"
    ))
    assert actual["hostaddr"] == "127.0.0.1"
    assert actual["connect_timeout"] == "5"
