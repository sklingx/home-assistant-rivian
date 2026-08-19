"""Rivian (Unofficial) Tracker"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_COORDINATOR, ATTR_VEHICLE, DOMAIN
from .coordinator import NavigationCoordinator, VehicleCoordinator
from .data_classes import RivianTrackerEntityDescription
from .entity import RivianEntity, RivianVehicleEntity

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
                coordinators[vehicle_id].navigation_coordinator,
                entry,
                DESTINATION_DESCRIPTION,
                vehicle,
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


class RivianDestinationTracker(RivianEntity[NavigationCoordinator], TrackerEntity):
    """A class representing the active navigation destination waypoint for a Rivian vehicle."""

    entity_description: RivianTrackerEntityDescription

    def __init__(
        self,
        coordinator: NavigationCoordinator,
        config_entry: ConfigEntry,
        description: RivianTrackerEntityDescription,
        vehicle: dict[str, Any],
    ) -> None:
        """Create a Rivian destination tracker entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._config_entry = config_entry
        self._vin = (vin := vehicle["vin"])
        self._attr_unique_id = f"{vin}-{description.key}"
        name = vehicle["name"]
        model = vehicle["model"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vin), (DOMAIN, vehicle["id"])},
            name=name if name else model,
            manufacturer="Rivian",
            model=model,
            serial_number=vin,
            sw_version=None,
        )

    @property
    def force_update(self) -> bool:
        """Disable forced updates since updates come from the coordinator."""
        return False

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the destination."""
        if not self.coordinator.data or not self.coordinator.data.is_navigating:
            return None
        return self.coordinator.data.destination_latitude

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the destination."""
        if not self.coordinator.data or not self.coordinator.data.is_navigating:
            return None
        return self.coordinator.data.destination_longitude

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
        if not self.coordinator.data or not self.coordinator.data.is_navigating:
            return None
        return self.coordinator.data.destination_name

    @property
    def battery_level(self) -> int | None:
        """Return estimated battery level at destination."""
        if (
            self.coordinator.data
            and self.coordinator.data.is_navigating
            and self.coordinator.data.arrival_soc is not None
        ):
            return round(self.coordinator.data.arrival_soc)
        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes of the destination."""
        if not self.coordinator.data:
            return {}
        d = self.coordinator.data
        return {
            "is_navigating": d.is_navigating,
            "destination_name": d.destination_name,
            "route_name": d.route_name,
            "eta": d.eta,
            "distance_remaining_meters": d.distance_remaining_meters,
            "duration_remaining_seconds": d.duration_remaining_seconds,
            "arrival_soc": d.arrival_soc,
            "polyline": d.route_polyline,
            "last_update": d.last_update.isoformat() if d.last_update else None,
        }
