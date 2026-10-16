"""Shared fixtures."""
import pytest
import pytest_homeassistant_custom_component  # noqa: F401  registers HA fixtures
from homeassistant.helpers import device_registry as dr

from tests.fake_matter import FakeMatterClient

CFID = 0x1234567890AB


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_client() -> FakeMatterClient:
    return FakeMatterClient()


@pytest.fixture(autouse=True)
def patch_matter_client(monkeypatch, fake_client):
    """Route every MatterConnection to the per-test fake client."""
    monkeypatch.setattr(
        "custom_components.matter_trv_binding.matter.client._create_client",
        lambda hass, url: fake_client,
    )


class MatterDeviceFactory:
    """Creates Matter device-registry entries with realistic identifiers."""

    def __init__(self, hass) -> None:
        self._hass = hass

    def create(self, name: str, node_id: int, postfix: str = "MatterNodeDevice"):
        registry = dr.async_get(self._hass)
        return registry.async_get_or_create(
            config_entry_id="matter_test_entry",
            identifiers={("matter", f"deviceid_{CFID:016X}-{node_id:016X}-{postfix}")},
            name=name,
        )


@pytest.fixture
def matter_devices(hass):
    return MatterDeviceFactory(hass)
