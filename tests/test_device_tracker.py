"""Tests for Rivian device tracker entities."""

from unittest.mock import MagicMock

from custom_components.rivian.const import ATTR_COORDINATOR, ATTR_VEHICLE, DOMAIN
from custom_components.rivian.device_tracker import (
    DESTINATION_DESCRIPTION,
    LOCATION_DESCRIPTION,
    RivianDestinationTracker,
    RivianDeviceEntity,
    async_setup_entry,
)
from homeassistant.components.device_tracker import SourceType
from homeassistant.core import HomeAssistant


def _build_mock_coordinator(data: dict | None = None) -> MagicMock:
    """Build a mock VehicleCoordinator."""
    coordinator = MagicMock()
    coordinator.data = data or {}
    coordinator.get.side_effect = lambda key: (
        coordinator.data.get(key, {}).get("value")
        if isinstance(coordinator.data.get(key), dict)
        else coordinator.data.get(key)
    )
    return coordinator


async def test_async_setup_entry_device_trackers(hass: HomeAssistant) -> None:
    """Test async_setup_entry registers both vehicle location and destination trackers."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"

    mock_vehicle = {
        "id": "vehicle_123",
        "name": "Rivian Vehicle",
        "vin": "VIN1234567890",
        "model": "R1S",
    }
    mock_coordinator = _build_mock_coordinator(
        {
            "gnssLocation": {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "timeStamp": "2026-08-19T05:51:47Z",
            }
        }
    )

    hass.data = {
        DOMAIN: {
            "test_entry_id": {
                ATTR_VEHICLE: {"vehicle_123": mock_vehicle},
                ATTR_COORDINATOR: {ATTR_VEHICLE: {"vehicle_123": mock_coordinator}},
            }
        }
    }

    added_entities = []

    def mock_add_entities(entities: list) -> None:
        added_entities.extend(entities)

    await async_setup_entry(hass, entry, mock_add_entities)

    assert len(added_entities) == 2
    assert isinstance(added_entities[0], RivianDeviceEntity)
    assert isinstance(added_entities[1], RivianDestinationTracker)


def test_rivian_device_entity_vehicle_location() -> None:
    """Test RivianDeviceEntity reporting vehicle GPS position."""
    mock_coordinator = _build_mock_coordinator(
        {
            "gnssLocation": {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "timeStamp": "2026-08-19T05:51:47Z",
            }
        }
    )
    mock_entry = MagicMock()
    mock_vehicle = {
        "id": "vehicle_123",
        "name": "Rivian Vehicle",
        "vin": "VIN1234567890",
        "model": "R1S",
    }

    entity = RivianDeviceEntity(
        coordinator=mock_coordinator,
        config_entry=mock_entry,
        description=LOCATION_DESCRIPTION,
        vehicle=mock_vehicle,
    )

    assert entity.latitude == 37.7749
    assert entity.longitude == -122.4194
    assert entity.source_type == SourceType.GPS
    assert entity.extra_state_attributes == {"last_update": "2026-08-19T05:51:47Z"}
    assert entity.force_update is False


def test_rivian_destination_tracker_active_navigation() -> None:
    """Test RivianDestinationTracker when navigation route is active."""
    data = {
        "destination_name": {"value": "Irvine, CA"},
        "destination_latitude": {"value": 33.7206991},
        "destination_longitude": {"value": -117.7930813},
        "destination_eta": {"value": "2026-08-19T06:51:40+00:00"},
        "destination_distance_remaining": {"value": 11179.0},
        "destination_duration_remaining": {"value": 605.0},
        "destination_arrival_soc": {"value": 75.64},
        "destination_route_name": {"value": "I-5 N"},
        "destination_route_polyline": {"value": "omag_Av|kp_FoPs..."},
    }
    mock_coordinator = _build_mock_coordinator(data)
    mock_entry = MagicMock()
    mock_vehicle = {
        "id": "vehicle_123",
        "name": "Rivian Vehicle",
        "vin": "VIN1234567890",
        "model": "R1S",
    }

    entity = RivianDestinationTracker(
        coordinator=mock_coordinator,
        config_entry=mock_entry,
        description=DESTINATION_DESCRIPTION,
        vehicle=mock_vehicle,
    )

    assert entity.latitude == 33.7206991
    assert entity.longitude == -117.7930813
    assert entity.source_type == SourceType.GPS
    assert entity.icon == "mdi:map-marker-destination"
    assert entity.location_name == "Irvine, CA"
    assert entity.location_accuracy == 0
    assert entity.battery_level == 76
    assert entity.available is True
    assert entity.force_update is False

    attrs = entity.extra_state_attributes
    assert attrs["destination_name"] == "Irvine, CA"
    assert attrs["route_name"] == "I-5 N"
    assert attrs["eta"] == "2026-08-19T06:51:40+00:00"
    assert attrs["distance_remaining_meters"] == 11179.0
    assert attrs["duration_remaining_seconds"] == 605.0
    assert attrs["arrival_soc"] == 75.64
    assert attrs["polyline"] == "omag_Av|kp_FoPs..."


def test_rivian_destination_tracker_inactive_navigation() -> None:
    """Test RivianDestinationTracker when navigation is inactive / cancelled."""
    data = {
        "destination_name": {"value": None},
        "destination_latitude": {"value": None},
        "destination_longitude": {"value": None},
        "destination_eta": {"value": None},
        "destination_distance_remaining": {"value": None},
        "duration_remaining_seconds": {"value": None},
        "destination_arrival_soc": {"value": None},
        "destination_route_name": {"value": None},
        "destination_route_polyline": {"value": None},
    }
    mock_coordinator = _build_mock_coordinator(data)
    mock_entry = MagicMock()
    mock_vehicle = {
        "id": "vehicle_123",
        "name": "Rivian Vehicle",
        "vin": "VIN1234567890",
        "model": "R1S",
    }

    entity = RivianDestinationTracker(
        coordinator=mock_coordinator,
        config_entry=mock_entry,
        description=DESTINATION_DESCRIPTION,
        vehicle=mock_vehicle,
    )

    assert entity.latitude is None
    assert entity.longitude is None
    assert entity.location_name is None
    assert entity.battery_level is None
    assert entity.available is False
    attrs = entity.extra_state_attributes
    assert attrs["destination_name"] is None
    assert attrs["route_name"] is None
    assert attrs["eta"] is None
    assert attrs["distance_remaining_meters"] is None
