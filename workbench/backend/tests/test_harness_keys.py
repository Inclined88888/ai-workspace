"""The test client must not seed live model providers from the developer's shell."""


def test_client_does_not_seed_provider_keys_from_environment(client):
    from app import config, db

    assert not config.SEED_KEYS["nvidia"]
    assert db.one("SELECT id FROM connections WHERE base_url = ?", "https://integrate.api.nvidia.com/v1") is None
