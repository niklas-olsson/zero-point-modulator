"""Closed installer roles, independent of runtime and wire schema versions."""

from .models import ConfigurationError


LEGACY_SERVICES = ("zpm", "openjules", "polyphony")
ROLE_SERVICES = {
    "controller-authority": ("zpm", "polyphony"),
    "execution": ("zpm", "openjules"),
}


def role_services(role):
    if type(role) is not str or role not in ROLE_SERVICES:
        raise ConfigurationError("installer role must be controller-authority or execution")
    return ROLE_SERVICES[role]


def services(configuration):
    if type(configuration.get("schema_version")) is int and configuration["schema_version"] == 3:
        return role_services(configuration.get("role"))
    return LEGACY_SERVICES
