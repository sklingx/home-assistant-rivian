"""Rivian (Unofficial) Tracker"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_COORDINATOR, ATTR_VEHICLE, DOMAIN
from .coordinator import VehicleCoordinator
from .data_classes import RivianTrackerEntityDescription
from .entity import RivianVehicleEntity

LOCATION_DESCRIPTION = RivianTrackerEntityDescription(key="location", name="Location")
DESTINATION_DESCRIPTION = RivianTrackerEntityDescription(
    key="destination_location",
    name="Destination",
    translation_key="destination",
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the device tracker entities."""
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    vehicles: dict[str, Any] = data[ATTR_VEHICLE]
    coordinators: dict[str, VehicleCoordinator] = data[ATTR_COORDINATOR][ATTR_VEHICLE]

    entities: list[TrackerEntity] = [
        RivianDeviceEntity(
            coordinators[vehicle_id], entry, LOCATION_DESCRIPTION, vehicle
        )
        for vehicle_id, vehicle in vehicles.items()
    ]
    entities.extend(
        [
            RivianDestinationTracker(
                coordinators[vehicle_id], entry, DESTINATION_DESCRIPTION, vehicle
            )
            for vehicle_id, vehicle in vehicles.items()
        ]
    )

    async_add_entities(entities)


class RivianDeviceEntity(RivianVehicleEntity, TrackerEntity):
    """A class representing a Rivian device."""

    entity_description: RivianTrackerEntityDescription

    def __init__(
        self,
        coordinator: VehicleCoordinator,
        config_entry: ConfigEntry,
        description: RivianTrackerEntityDescription,
        vehicle: dict[str, Any],
    ) -> None:
        """Create a Rivian device tracker entity."""
        super().__init__(coordinator, config_entry, description, vehicle)
        self._attribute = "gnssLocation"
        self._tracker_data = coordinator.data[self._attribute]

    @property
    def force_update(self) -> bool:
        """Disable forced updated since we are polling via the coordinator updates."""
        return False

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._tracker_data["latitude"]

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._tracker_data["longitude"]

    @property
    def source_type(self) -> SourceType:
        """Return the source type, eg gps or router, of the device."""
        return SourceType.GPS

    # @property
    # def location_accuracy(self) -> int:
    #     return self._tracker_data[6]

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes of the device."""
        return {
            "last_update": self._tracker_data["timeStamp"],
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Respond to a DataUpdateCoordinator update."""
        entity = self.coordinator.data[self._attribute]
        try:
            if entity["timeStamp"] != self._tracker_data["timeStamp"]:
                self._tracker_data = entity
                self.async_write_ha_state()
        except Exception:  # noqa: BLE001
            self._tracker_data = entity


class RivianDestinationTracker(RivianVehicleEntity, TrackerEntity):
    """A class representing the active navigation destination waypoint for a Rivian vehicle."""

    entity_description: RivianTrackerEntityDescription

    def __init__(
        self,
        coordinator: VehicleCoordinator,
        config_entry: ConfigEntry,
        description: RivianTrackerEntityDescription,
        vehicle: dict[str, Any],
    ) -> None:
        """Create a Rivian destination tracker entity."""
        super().__init__(coordinator, config_entry, description, vehicle)

    @property
    def force_update(self) -> bool:
        """Disable forced updates since updates come from the coordinator."""
        return False

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the destination."""
        lat = self.coordinator.get("destination_latitude")
        return float(lat) if lat is not None else None

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the destination."""
        lon = self.coordinator.get("destination_longitude")
        return float(lon) if lon is not None else None

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the device."""
        return SourceType.GPS

    @property
    def icon(self) -> str:
        """Return destination icon."""
        return "mdi:map-marker-destination"

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy of the destination."""
        return 0

    @property
    def location_name(self) -> str | None:
        """Return a location name for the current position of the device."""
        return self.coordinator.get("destination_name")

    @property
    def battery_level(self) -> int | None:
        """Return estimated battery level at destination."""
        soc = self.coordinator.get("destination_arrival_soc")
        return round(soc) if soc is not None else None

    @property
    def available(self) -> bool:
        """Return True if destination coordinates are available."""
        return self.latitude is not None and self.longitude is not None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes of the destination."""
        return {
            "destination_name": self.coordinator.get("destination_name"),
            "route_name": self.coordinator.get("destination_route_name"),
            "eta": self.coordinator.get("destination_eta"),
            "distance_remaining_meters": self.coordinator.get(
                "destination_distance_remaining"
            ),
            "duration_remaining_seconds": self.coordinator.get(
                "destination_duration_remaining"
            ),
            "arrival_soc": self.coordinator.get("destination_arrival_soc"),
            "polyline": self.coordinator.get("destination_route_polyline"),
        }
