"""Constants for the Matter TRV Binding integration."""
from enum import StrEnum

DOMAIN = "matter_trv_binding"
PLATFORMS = ["binary_sensor", "sensor", "button"]
DEFAULT_URL = "ws://localhost:5580/ws"
HUB_UNIQUE_ID = "hub"

CLUSTER_DESCRIPTOR = 29
CLUSTER_BINDING = 30
CLUSTER_ACCESS_CONTROL = 31
CLUSTER_THERMOSTAT = 513
CLUSTER_TEMPERATURE_MEASUREMENT = 1026

PRIVILEGE_VIEW = 1
PRIVILEGE_ADMIN = 5
AUTH_MODE_CASE = 2

OPT_UPDATE_INTERVAL_MINUTES = "update_interval_minutes"
OPT_AUTO_REPAIR = "auto_repair"
OPT_REPAIR_ATTEMPTS = "repair_attempts"
DEFAULT_UPDATE_INTERVAL_MINUTES = 30
DEFAULT_AUTO_REPAIR = True
DEFAULT_REPAIR_ATTEMPTS = 3


class TrvStatus(StrEnum):
    """Health status of one TRV's binding."""

    BOUND = "bound"
    BINDING_MISSING = "binding_missing"
    REMOTE_SENSING_OFF = "remote_sensing_off"
    ACL_MISSING = "acl_missing"
    UNREACHABLE = "unreachable"
    DEVICE_MISSING = "device_missing"
