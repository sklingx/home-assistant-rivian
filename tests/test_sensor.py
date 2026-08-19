"""Tests for Rivian sensor entities and Parallax navigation parsing."""

from unittest.mock import MagicMock

from custom_components.rivian.const import NAVIGATION_SENSORS
from custom_components.rivian.coordinator import NavigationCoordinator, NavigationData
from custom_components.rivian.helpers import (
    ProtobufRawDecoder,
    parse_parallax_navigation_payload,
    to_timestamp_iso,
)
from custom_components.rivian.sensor import RivianNavigationSensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTime

# Synthetic Parallax trip_info and trip_progress protobuf payloads
SAMPLE_TRIP_INFO_B64 = (
    "ChQtODU2Mjk4NzA0MzMxNjM2MjI2MBIgChIJDmi/v+TXQEAR//9/wG9uXcAdSqQJQyje6MfDgTQa"
    "0wYJAAAAAIDVxUARAAAAAADogkAaWwpZChIJ2WA83j/cQEARbaQR2MFyXcAYAiIKSXJ2aW5lLCBD"
    "QSobQ2hJSmN4bElNM3JjM0lBUmF4a20zNmxzZmx3OQAAAAAn6VJAQQAAACCD+h5BSgYIvP+U1AYi"
    "zQIJAAAAAIDVxUARAAAAAADogkAaBUktNSBOIqYCZ21ybEVqaHBtVXdAcUFdYUB3RmJKaUB4QHFA"
    "fUB9RGNFV1VnTnpUVVZlR3JKTVpzTHRSaUZgSWpPZExySGBGcEF4QGpAQmBuQXBhQVRqQGBJbEh0"
    "QGRBbEBqQWJAckFYdkFSdkJCfkFFdEFPfEFfQHBCa0B4QXFAbEFhQ2JEc0h+Sn1BeEJhQnRDZ0F2"
    "QlNaa0F8QW9DdENHYkBxQWBBdUJ2QX1HekRtR25EYVNgTHtkQH5XY0doRG92QGZjQHtBfkBpRHpC"
    "e0hkR31DbENrR2pHYUNqQ2NGeEdnRGBGaUNiRW9RcFlhQEZ3Q3ZEdUBuQHdBbkBvQFRpQExpQlBr"
    "QFR1QGhAbUB2QF9AaEB9RHtEdEF3QnBAckBlQHRAQExiQWRBWGNAQgkJAAAAAPfyAUAxAAAAACfp"
    "UkA5AAAAIIP6HkFS/wJvbWFnX0F2fGtwX0ZvUHNYa0hzSW9sQWZuQmNMYlFzTmtSa3pAZ3xAb0Z7"
    "RW93Q3Z5RXtFbkZ7cEFmc0JrQ3ZHZ2dDemNFY2hBcmNCdmJEemJDZl9CcmVBclhiUXZMZkBydVdy"
    "eFN6RXZMcmNCan1Bek96VGpNdlZmSmZZYkduWmZFbmRAZkB+XHtAellfRGpcX0lyYkB3TGJbc05q"
    "V3NnQGZyQGdfQn52QmtcYmVAc116bUBvVW5kQGdFdkd3VmpcX2xAem1Ab0FmSnNYclN7Y0BuWmt4"
    "QXZ5QGtzQX51QHNnRXJhQ3d5Sn54RmdwQWJ0QF9qUG5pSndbflJjdEB2ZUB3YUJ6cEFrcEBqa0B3"
    "ckF2ckFzZ0B2akBnZkFid0Fvc0ByZUFjakBmfEBfeERyaEdzSW5Bb25AbnhAe09+TW9afk1fTnpF"
    "Y0xqQ2NgQHJEd0x6RXtPYkxrTW5QX0liTGt6QHd5QHpZb2RAck5mT3tKek9SakNmVHpUYkdnSiK+"
    "Ag3NzEw+Fc3MTD4aG3N3aXRjaEV4Y2x1ZGVNb3RvcndheTpmYWxzZRoXc3dpdGNoRXhjbHVkZVRv"
    "bGw6ZmFsc2UaGHN3aXRjaEV4Y2x1ZGVGZXJyeTpmYWxzZSIXc3dpdGNoUml2aWFuRmlsdGVyOnRy"
    "dWUiHXN3aXRjaENoYXJnZVBvaW50RmlsdGVyOmZhbHNlIiJzd2l0Y2hFbGVjdHJpZnlBbWVyaWNh"
    "RmlsdGVyOmZhbHNlIhtzd2l0Y2hFVkNvbm5lY3RGaWx0ZXI6ZmFsc2UiFnN3aXRjaEVWZ29GaWx0"
    "ZXI6ZmFsc2UiF3N3aXRjaElPTk5BRmlsdGVyOmZhbHNlIhdzd2l0Y2hUZXNsYUZpbHRlcjpmYWxz"
    "ZSIfc3dpdGNoQXZvaWRBZGFwdGVyUmVxdWlyZWQ6dHJ1ZSkzMzMzM1NTQDIGCLz/lNQG"
)

SAMPLE_TRIP_PROGRESS_B64 = (
    "CioKJGU5MDYzY2M1LWY4NzUtNGU3OC1hMzI2LTE3MDhkNWM3ZjUwZSICCgAKMgokZDU5NDBkYTUt"
    "MzdhNi00OTA4LTljZGQtOGM1ZDZiYzA2YmZkGgoIeBIGCgRhd2F5EgYItu3q0wY="
)


def test_protobuf_decoder_varint() -> None:
    """Test raw Protobuf decoder for varint fields."""
    # Tag 1 (field 1, varint 150) -> 0x08, 0x96, 0x01
    data = bytes([0x08, 0x96, 0x01])
    fields = ProtobufRawDecoder.decode(data)
    assert fields == {1: 150}


def test_to_timestamp_iso() -> None:
    """Test epoch to ISO string converter."""
    assert to_timestamp_iso(1787117500) == "2026-08-19T05:31:40+00:00"
    assert to_timestamp_iso(1787116516446) == "2026-08-19T05:15:16.446000+00:00"
    assert to_timestamp_iso("invalid") is None
    assert to_timestamp_iso(100) is None


def test_parse_parallax_navigation_payload() -> None:
    """Verify parsing trip_info payload."""
    result = parse_parallax_navigation_payload(SAMPLE_TRIP_INFO_B64)
    assert result is not None
    assert result["destination_name"]["value"] == "Irvine, CA"
    assert result["destination_latitude"]["value"] == 33.7206991
    assert result["destination_longitude"]["value"] == -117.7930813
    assert result["destination_distance_remaining"]["value"] == 11179.0
    assert result["destination_duration_remaining"]["value"] == 605.0
    assert result["destination_arrival_soc"]["value"] == 75.64
    assert result["destination_route_name"]["value"] == "I-5 N"
    assert "omag_Av|kp" in result["destination_route_polyline"]["value"]


def test_parse_empty_or_invalid_payloads() -> None:
    """Verify handling empty or non-navigation payloads gracefully."""
    assert parse_parallax_navigation_payload(None) is None
    assert parse_parallax_navigation_payload("") is None
    assert parse_parallax_navigation_payload(b"") is None
    assert parse_parallax_navigation_payload("invalid_base64!!!") is None


def test_navigation_sensor_descriptions_defined() -> None:
    """Verify all navigation sensor entity descriptions are registered under NAVIGATION_SENSORS."""
    nav_sensors = {desc.key: desc for desc in NAVIGATION_SENSORS}

    assert "destination" in nav_sensors
    assert nav_sensors["destination"].field == "destination_name"

    assert "navigation_eta" in nav_sensors
    assert nav_sensors["navigation_eta"].device_class == SensorDeviceClass.TIMESTAMP
    assert nav_sensors["navigation_eta"].field == "eta"

    assert "distance_to_destination" in nav_sensors
    assert (
        nav_sensors["distance_to_destination"].device_class
        == SensorDeviceClass.DISTANCE
    )
    assert (
        nav_sensors["distance_to_destination"].native_unit_of_measurement
        == UnitOfLength.METERS
    )

    assert "time_to_destination" in nav_sensors
    assert nav_sensors["time_to_destination"].device_class == SensorDeviceClass.DURATION
    assert (
        nav_sensors["time_to_destination"].native_unit_of_measurement
        == UnitOfTime.MINUTES
    )
    # Test duration conversion lambda (seconds to minutes)
    assert nav_sensors["time_to_destination"].value_lambda(605.0) == 10.1
    assert nav_sensors["time_to_destination"].value_lambda(None) is None

    assert "battery_level_at_destination" in nav_sensors
    assert (
        nav_sensors["battery_level_at_destination"].device_class
        == SensorDeviceClass.BATTERY
    )
    assert (
        nav_sensors["battery_level_at_destination"].native_unit_of_measurement
        == PERCENTAGE
    )
    assert (
        nav_sensors["battery_level_at_destination"].state_class
        == SensorStateClass.MEASUREMENT
    )


def test_rivian_navigation_sensor_entity_states() -> None:
    """Test RivianNavigationSensorEntity reporting active and inactive navigation metrics."""
    nav_data = NavigationData(
        is_navigating=True,
        destination_name="Irvine, CA",
        destination_latitude=33.7206991,
        destination_longitude=-117.7930813,
        eta="2026-08-19T05:31:40+00:00",
        distance_remaining_meters=11179.0,
        duration_remaining_seconds=605.0,
        arrival_soc=75.64,
        route_name="I-5 N",
    )

    mock_coordinator = MagicMock()
    mock_coordinator.data = nav_data
    mock_vehicle = {
        "id": "vehicle_123",
        "name": "Rivian Vehicle",
        "vin": "VIN1234567890",
        "model": "R1S",
    }

    nav_sensors = {desc.key: desc for desc in NAVIGATION_SENSORS}

    # Destination Name Sensor
    dest_sensor = RivianNavigationSensorEntity(
        mock_coordinator, nav_sensors["destination"], mock_vehicle
    )
    assert dest_sensor.native_value == "Irvine, CA"

    # ETA Sensor
    eta_sensor = RivianNavigationSensorEntity(
        mock_coordinator, nav_sensors["navigation_eta"], mock_vehicle
    )
    assert eta_sensor.native_value == "2026-08-19T05:31:40+00:00"

    # Distance Sensor
    dist_sensor = RivianNavigationSensorEntity(
        mock_coordinator,
        nav_sensors["distance_to_destination"],
        mock_vehicle,
    )
    assert dist_sensor.native_value == 11179.0

    # Time Duration Sensor (minutes)
    time_sensor = RivianNavigationSensorEntity(
        mock_coordinator,
        nav_sensors["time_to_destination"],
        mock_vehicle,
    )
    assert time_sensor.native_value == 10.1

    # Arrival Battery Level Sensor
    soc_sensor = RivianNavigationSensorEntity(
        mock_coordinator,
        nav_sensors["battery_level_at_destination"],
        mock_vehicle,
    )
    assert soc_sensor.native_value == 75.64

    # When not navigating: sensors return None (clean idle)
    mock_coordinator.data = NavigationData(is_navigating=False)
    assert dest_sensor.native_value is None
    assert dist_sensor.native_value is None
    assert time_sensor.native_value is None
    assert eta_sensor.native_value is None
    assert soc_sensor.native_value is None


def test_navigation_coordinator_process_parallax_data() -> None:
    """Test NavigationCoordinator ingesting Parallax message frames."""
    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_client = MagicMock()

    coordinator = NavigationCoordinator(
        hass=mock_hass,
        config_entry=mock_entry,
        client=mock_client,
        vehicle_id="vehicle_123",
    )

    trip_info_frame = {
        "payload": {
            "data": {
                "parallaxMessages": {
                    "payload": SAMPLE_TRIP_INFO_B64,
                    "sequenceNumber": 100,
                }
            }
        }
    }

    coordinator._process_parallax_data(trip_info_frame)

    assert coordinator.data.is_navigating is True
    assert coordinator.data.destination_name == "Irvine, CA"
    assert coordinator.data.destination_latitude == 33.7206991
    assert coordinator.data.destination_longitude == -117.7930813
    assert coordinator.data.distance_remaining_meters == 11179.0
    assert coordinator.data.duration_remaining_seconds == 605.0
    assert coordinator.data.arrival_soc == 75.64
    assert coordinator.data.route_name == "I-5 N"

    # Test route cancellation / clearing
    coordinator.clear_navigation()
    assert coordinator.data.is_navigating is False
    assert coordinator.data.destination_name is None
    assert coordinator.data.destination_latitude is None
    assert coordinator.data.destination_longitude is None
    assert coordinator.data.distance_remaining_meters is None
