"""Data update coordinator for the Rivian integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any, Generic, TypeVar

from aiohttp import ClientResponse
from rivian import Rivian, VehicleCommand
from rivian.exceptions import (
    RivianApiException,
    RivianApiRateLimitError,
    RivianExpiredTokenError,
    RivianUnauthenticated,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ATTR_COORDINATOR,
    ATTR_USER,
    ATTR_VEHICLE,
    CHARGING_API_FIELDS,
    DEFAULT_CHARGING_SCHEDULE,
    DOMAIN,
    INVALID_SENSOR_STATES,
    VEHICLE_STATE_API_FIELDS,
)
from .helpers import parse_parallax_navigation_payload, redact

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T", bound=dict[str, Any] | list[dict[str, Any]])

# Maximum time to wait for the first vehicle state to arrive after subscribing.
# The first `_process_new_data` callback has been observed ~27s after the
# subscription is established, so this needs meaningful headroom.
INITIAL_UPDATE_TIMEOUT = 60
CHARGING_SCHEDULE_COOL_OFF = 10
CHARGING_SCHEDULE_REFRESH_INTERVAL = 900


class RivianDataUpdateCoordinator(DataUpdateCoordinator[T], ABC, Generic[T]):
    """Data update coordinator for the Rivian integration."""

    key: str
    _update_interval_seconds = 30
    _error_count = 0

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, client: Rivian
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=(
                timedelta(seconds=self._update_interval_seconds)
                if self._update_interval_seconds
                else None
            ),
            always_update=False,
        )
        self.api = client

    def _set_update_interval(self, seconds: float | None = None) -> None:
        """Set the update interval or calculate new one based on errors."""
        if not seconds:
            seconds = min(self._update_interval_seconds * 2**self._error_count, 900)
        if self._update_interval_seconds != seconds:
            refresh = self.update_interval and self._update_interval_seconds > seconds
            self.update_interval = timedelta(seconds=seconds)
            if refresh and self.data:
                task = self.async_request_refresh()
                self.config_entry.async_create_task(self.hass, task)
            else:
                self._schedule_refresh()
            _LOGGER.info("Polling set to %s seconds", seconds)

    async def _async_update_data(self) -> T:
        """Get the latest data from Rivian."""
        try:
            resp = await self._fetch_data()
            if resp.status == 200:
                data = await resp.json()
                _LOGGER.debug(
                    "[%s] %s",
                    self.__class__.__name__.replace("Coordinator", ""),
                    redact(data),
                )
                if self._error_count:
                    self._error_count = 0
                    self._set_update_interval()
                return data["data"][self.key]
            resp.raise_for_status()

        except RivianExpiredTokenError:
            _LOGGER.info("Rivian token expired, refreshing")
            await self.api.create_csrf_token()
            return await self._async_update_data()
        except RivianApiRateLimitError as err:
            _LOGGER.error("Rate limit being enforced: %s", err, exc_info=1)
            self._set_update_interval()
        except RivianUnauthenticated as err:
            await self.api.close()
            raise ConfigEntryAuthFailed from err
        except RivianApiException as ex:
            _LOGGER.error("Rivian api exception: %s", ex, exc_info=1)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error(
                "Unknown Exception while updating Rivian data: %s", ex, exc_info=1
            )

        self._error_count += 1
        if self.data:
            return self.data
        raise UpdateFailed("Error communicating with API")

    @abstractmethod
    async def _fetch_data(self) -> ClientResponse:
        """Fetch the data."""
        raise NotImplementedError


class ChargingCoordinator(RivianDataUpdateCoordinator[dict[str, Any]]):
    """Charging data update coordinator for Rivian."""

    key = "getLiveSessionData"
    _unplugged_interval = 15 * 60  # 15 minutes
    _plugged_interval = 30  # 30 seconds
    _update_interval_seconds = _unplugged_interval  # 15 minutes

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Rivian,
        vehicle_id: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass=hass, config_entry=config_entry, client=client)
        self.vehicle_id = vehicle_id

    async def _fetch_data(self) -> ClientResponse:
        """Fetch the data."""
        return await self.api.get_live_charging_session(
            vin=self.vehicle_id, properties=CHARGING_API_FIELDS
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Get the latest data from Rivian, gracefully handling deprecated endpoint failures."""
        try:
            return await super()._async_update_data()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Live charging session endpoint error: %s", err)
            return self.data or {}

    def adjust_update_interval(self, is_plugged_in: bool) -> None:
        """Adjust update interval based on plugged in status."""
        self._set_update_interval(
            self._plugged_interval if is_plugged_in else self._unplugged_interval
        )


class DriverKeyCoordinator(RivianDataUpdateCoordinator[dict[str, Any]]):
    """Drivers/keys data update coordinator for Rivian."""

    key = "getVehicle"
    _update_interval_seconds = 15 * 60  # 15 minutes

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Rivian,
        vehicle_id: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass=hass, config_entry=config_entry, client=client)
        self.vehicle_id = vehicle_id

    async def _fetch_data(self) -> ClientResponse:
        """Fetch the data."""
        return await self.api.get_drivers_and_keys(vehicle_id=self.vehicle_id)

    def get_device_details(self, identity_id: str) -> dict[str, Any] | None:
        """Get the details of a device."""
        if not self.data:
            return None
        return next(
            (
                device
                for user in self.data.get("invitedUsers")
                if user["__typename"] == "ProvisionedUser"
                for device in user["devices"]
                if device["mappedIdentityId"] == identity_id
            ),
            None,
        )


@dataclass
class NavigationData:
    """Dataclass representing navigation state."""

    is_navigating: bool = False
    destination_name: str | None = None
    destination_latitude: float | None = None
    destination_longitude: float | None = None
    eta: str | None = None
    distance_remaining_meters: float | None = None
    duration_remaining_seconds: float | None = None
    arrival_soc: float | None = None
    route_name: str | None = None
    route_polyline: str | None = None
    last_update: datetime | None = None


class NavigationCoordinator(RivianDataUpdateCoordinator[NavigationData]):
    """Navigation data update coordinator for Rivian."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Rivian,
        vehicle_id: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass=hass, config_entry=config_entry, client=client)
        self.vehicle_id = vehicle_id
        self._unsub_handler: Coroutine[None, None, None] | None = None
        self.data = NavigationData()

    async def _fetch_data(self) -> ClientResponse:
        """Fetch data (polling not used for navigation)."""
        raise NotImplementedError("Polling navigation is not supported")

    async def _async_update_data(self) -> NavigationData:
        """Get the latest data from Rivian."""
        if not self._unsub_handler:
            self._unsub_handler = await self._subscribe_parallax()
        return self.data or NavigationData()

    async def _subscribe_parallax(self) -> Any:
        """Subscribe to real-time Parallax RVM updates."""
        try:
            if not self.api._ws_monitor or not self.api._ws_monitor.connected:
                return None
            payload = {
                "operationName": "ParallaxMessages",
                "query": (
                    "subscription ParallaxMessages($vehicleID: String!) {\n"
                    "  parallaxMessages(vehicleId: $vehicleID) {\n"
                    "    payload\n"
                    "    sequenceNumber\n"
                    "  }\n"
                    "}"
                ),
                "variables": {"vehicleID": self.vehicle_id},
            }
            return await self.api._ws_monitor.start_subscription(
                payload, self._process_parallax_data
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to subscribe to Parallax updates for %s: %s",
                self.vehicle_id,
                err,
            )
            return None

    @callback
    def _process_parallax_data(self, data: dict[str, Any]) -> None:
        """Process incoming Parallax RVM message."""
        payload_wrapper = data.get("payload", {})
        data_block = payload_wrapper.get("data", {})
        parallax_msg = data_block.get("parallaxMessages")
        if not parallax_msg or not isinstance(parallax_msg, dict):
            return

        raw_payload = parallax_msg.get("payload")
        if not raw_payload:
            return

        nav_updates = parse_parallax_navigation_payload(raw_payload)
        if not nav_updates:
            return

        _LOGGER.debug(
            "Vehicle %s parallax navigation update: %s",
            self.vehicle_id,
            redact(nav_updates),
        )

        current = self.data or NavigationData()
        now = datetime.now(timezone.utc)

        dest_name = (
            nav_updates["destination_name"]["value"]
            if "destination_name" in nav_updates
            else current.destination_name
        )
        dest_lat = (
            nav_updates["destination_latitude"]["value"]
            if "destination_latitude" in nav_updates
            else current.destination_latitude
        )
        dest_lon = (
            nav_updates["destination_longitude"]["value"]
            if "destination_longitude" in nav_updates
            else current.destination_longitude
        )
        route_name = (
            nav_updates["destination_route_name"]["value"]
            if "destination_route_name" in nav_updates
            else current.route_name
        )
        polyline = (
            nav_updates["destination_route_polyline"]["value"]
            if "destination_route_polyline" in nav_updates
            else current.route_polyline
        )
        arrival_soc = (
            nav_updates["destination_arrival_soc"]["value"]
            if "destination_arrival_soc" in nav_updates
            else current.arrival_soc
        )

        dist = (
            nav_updates["destination_distance_remaining"]["value"]
            if "destination_distance_remaining" in nav_updates
            else current.distance_remaining_meters
        )
        dur = (
            nav_updates["destination_duration_remaining"]["value"]
            if "destination_duration_remaining" in nav_updates
            else current.duration_remaining_seconds
        )
        eta = (
            nav_updates["destination_eta"]["value"]
            if "destination_eta" in nav_updates
            else current.eta
        )

        is_navigating = bool(dest_name or dest_lat is not None or (dist and dist > 0))

        updated_nav = NavigationData(
            is_navigating=is_navigating,
            destination_name=dest_name,
            destination_latitude=dest_lat,
            destination_longitude=dest_lon,
            eta=eta,
            distance_remaining_meters=dist,
            duration_remaining_seconds=dur,
            arrival_soc=arrival_soc,
            route_name=route_name,
            route_polyline=polyline,
            last_update=now,
        )

        self.async_set_updated_data(updated_nav)

    @callback
    def clear_navigation(self) -> None:
        """Explicitly clear navigation state."""
        self.async_set_updated_data(NavigationData())

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and unsubscribe."""
        if unsub := self._unsub_handler:
            await unsub()
            self._unsub_handler = None
        await super().async_shutdown()


class UserCoordinator(RivianDataUpdateCoordinator[dict[str, Any]]):
    """User data update coordinator for Rivian."""

    key = "currentUser"

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Rivian,
        include_phones: bool = False,
    ) -> None:
        super().__init__(hass=hass, config_entry=config_entry, client=client)
        self.include_phones = include_phones

    async def _fetch_data(self) -> ClientResponse:
        """Fetch the data."""
        return await self.api.get_user_information(self.include_phones)

    def get_enrolled_phone_data(
        self, public_key: str
    ) -> tuple[str, dict[str, str]] | None:
        """Get enrolled phone data."""
        phones = self.data.get("enrolledPhones", [])
        if phone := next(
            (phone for phone in phones if phone["vas"]["publicKey"] == public_key), None
        ):
            phone_id = phone["vas"]["vasPhoneId"]
            vehicle_entry = {
                entry["vehicleId"]: entry["identityId"] for entry in phone["enrolled"]
            }
            return (phone_id, vehicle_entry)
        return None

    def get_vehicles(self) -> dict[str, dict[str, Any]]:
        """Get the user's vehicles."""
        return {
            vehicle["id"]: vehicle["vehicle"]
            | {
                "name": vehicle["name"],
                "supported_features": [
                    supported_feature.get("name")
                    for supported_feature in vehicle.get("vehicle", {})
                    .get("vehicleState", {})
                    .get("supportedFeatures", [])
                    if supported_feature.get("status") == "AVAILABLE"
                ],
                "vas_id": (vas := vehicle.get("vas", {})).get("vasVehicleId"),
                "public_key": vas.get("vehiclePublicKey"),
            }
            for vehicle in self.data["vehicles"]
        }


class VehicleCoordinator(RivianDataUpdateCoordinator[dict[str, Any]]):
    """Vehicle data update coordinator for Rivian."""

    key = "vehicleState"
    _update_interval_seconds = 15 * 60  # 15 minutes

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Rivian,
        vehicle_id: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass=hass, config_entry=config_entry, client=client)
        self.vehicle_id = vehicle_id
        self.charging_coordinator = ChargingCoordinator(
            hass=hass, config_entry=config_entry, client=client, vehicle_id=vehicle_id
        )
        self.drivers_coordinator = DriverKeyCoordinator(
            hass=hass, config_entry=config_entry, client=client, vehicle_id=vehicle_id
        )
        self.navigation_coordinator = NavigationCoordinator(
            hass=hass, config_entry=config_entry, client=client, vehicle_id=vehicle_id
        )
        self._initial = asyncio.Event()
        self._unsub_handler: Coroutine[None, None, None] | None = None
        self._awake = asyncio.Event()
        self._charging_schedule: dict[str, Any] | None = None
        self._last_schedule_fetch: float = 0.0

    @property
    def charging_schedule(self) -> dict[str, Any]:
        """Return the charging schedule or empty dict."""
        return self._charging_schedule or {}

    async def get_charging_schedule_data(
        self, force_refresh: bool = False
    ) -> dict[str, Any]:
        """Fetch charging schedule via Rivian API."""
        now = time.time()
        cooldown = (
            CHARGING_SCHEDULE_COOL_OFF
            if force_refresh
            else CHARGING_SCHEDULE_REFRESH_INTERVAL
        )
        if self._charging_schedule is None or (
            now - self._last_schedule_fetch > cooldown
        ):
            self._last_schedule_fetch = now
            try:
                response = await self.api.get_charging_schedules(self.vehicle_id)
                res_json = await response.json()
                if (
                    res_json
                    and "data" in res_json
                    and res_json["data"].get("getVehicle")
                ):
                    schedules = res_json["data"]["getVehicle"].get(
                        "chargingSchedules", []
                    )
                    if schedules:
                        old_schedule = self._charging_schedule
                        self._charging_schedule = schedules[0]
                        if old_schedule != self._charging_schedule:
                            self.async_update_listeners()
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Error fetching charging schedule: %s", err)

            if self._charging_schedule is None:
                self._charging_schedule = dict(DEFAULT_CHARGING_SCHEDULE)
        return self._charging_schedule

    async def update_charging_schedule_data(self, schedule: dict[str, Any]) -> None:
        """Update charging schedule via Rivian API mutation."""
        current = dict(await self.get_charging_schedule_data(force_refresh=True))
        current.update(schedule)
        try:
            await self.api.set_charging_schedules(self.vehicle_id, [current])
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error setting charging schedule: %s", err)
        self._charging_schedule = current
        self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, Any]:
        """Get the latest data from Rivian."""
        await self.get_charging_schedule_data()
        if not self.data or not self.last_update_success:
            await self._unsubscribe()
            self._unsub_handler = await self.api.subscribe_for_vehicle_updates(
                vehicle_id=self.vehicle_id,
                properties=VEHICLE_STATE_API_FIELDS,
                callback=self._process_new_data,
            )

            try:
                await asyncio.wait_for(self._initial.wait(), INITIAL_UPDATE_TIMEOUT)
            except asyncio.TimeoutError as err:
                raise UpdateFailed(
                    "Timed out waiting for initial vehicle data after "
                    f"{INITIAL_UPDATE_TIMEOUT}s"
                ) from err

        return self.data

    async def _fetch_data(self) -> ClientResponse:
        """Fetch the data."""
        raise NotImplementedError("Polling VehicleState no longer allowed")

    async def async_shutdown(self) -> None:
        await self._unsubscribe(True)
        await self.navigation_coordinator.async_shutdown()
        return await super().async_shutdown()

    @callback
    def _process_new_data(self, data: dict[str, Any]) -> None:
        """Process new data."""
        if not (payload := data.get("payload")) or not (pdata := payload.get("data")):
            _LOGGER.error("Received an unknown subscription update: %s", data)
            self._error_count += 1
            if not self._initial.is_set() or self._error_count > 5:
                task = self._unsubscribe()
                self.config_entry.async_create_task(self.hass, task, eager_start=True)
            return
        vehicle_info = self._build_vehicle_info_dict(pdata.get(self.key, {}))
        current = dict(self.data) if self.data else {}
        self.async_set_updated_data(current | vehicle_info)
        self._error_count = 0
        self._initial.set()

    def _build_vehicle_info_dict(self, vijson: dict[str, Any]) -> dict[str, Any]:
        """Take the json output of vehicle_info and build a dictionary."""
        items = {
            k: v | ({"history": {v["value"]}} if "value" in v else {})
            for k, v in vijson.items()
            if v
        }

        if items:
            _LOGGER.debug("Vehicle %s updated: %s", self.vehicle_id, redact(items))

        if power_state := items.get("powerState"):
            if power_state.get("value") == "sleep":
                self._awake.clear()
            else:
                self._awake.set()
        if charger_status := items.get("chargerStatus"):
            self.charging_coordinator.adjust_update_interval(
                is_plugged_in=charger_status.get("value") != "chrgr_sts_not_connected"
            )

        if not (prev_items := (self.data or {})):
            return items
        if not items or prev_items == items:
            return prev_items

        new_data = prev_items | items
        for key in filter(lambda i: i != "gnssLocation", items):
            value = items[key].get("value")
            if str(value).lower() in INVALID_SENSOR_STATES and key in prev_items:
                new_data[key] = prev_items[key]
            new_data[key]["history"] |= prev_items.get(key, {}).get("history", set())

        return new_data

    async def _unsubscribe(self, close_monitor: bool = False):
        """Unsubscribe."""
        if unsub := self._unsub_handler:
            await unsub()
            self._unsub_handler = None
            self._initial.clear()
        if close_monitor and (monitor := self.api._ws_monitor):
            await monitor.close()

    def get(self, key: str) -> Any | None:
        """Get a data value by key."""
        if entity := self.data.get(key, {}):
            return entity.get("value")
        return None

    async def send_vehicle_command(
        self, command: VehicleCommand, params: dict[str, Any] | None = None
    ) -> None:
        """Send a command to the vehicle."""
        if self.get("powerState") == "sleep" and command != VehicleCommand.WAKE_VEHICLE:
            await self.send_vehicle_command(VehicleCommand.WAKE_VEHICLE)
            try:
                await asyncio.wait_for(self._awake.wait(), 30)
            except asyncio.TimeoutError:
                pass  # didn't wake-up in time, but we'll try command anyway

        entry_data = self.hass.data[DOMAIN][self.config_entry.entry_id]
        vehicle = entry_data[ATTR_VEHICLE][self.vehicle_id]
        user: UserCoordinator = entry_data[ATTR_COORDINATOR][ATTR_USER]
        phone_info = user.get_enrolled_phone_data(
            self.config_entry.options.get("public_key")
        )

        if response := await self.api.send_vehicle_command(
            command=command,
            vehicle_id=self.vehicle_id,
            phone_id=phone_info[0],
            identity_id=vehicle["phone_identity_id"],
            vehicle_key=vehicle["public_key"],
            private_key=self.config_entry.options.get("private_key"),
            params=params,
        ):
            _LOGGER.debug("%s response was: %s", command, response)


class VehicleImageCoordinator(RivianDataUpdateCoordinator[dict[str, Any]]):
    """Vehicle image data update coordinator for Rivian."""

    key = "getVehicleMobileImages"
    _update_interval_seconds = 0  # disabled
    _last_updated: datetime | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Rivian,
        version: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass=hass, config_entry=config_entry, client=client)
        self.version = version

    async def _fetch_data(self) -> ClientResponse:
        """Fetch the data."""
        data = await self.api.get_vehicle_images(
            resolution="@3x", vehicle_version=self.version
        )
        self._last_updated = datetime.now(timezone.utc)
        return data


class WallboxCoordinator(RivianDataUpdateCoordinator[list[dict[str, Any]]]):
    """Wallbox data update coordinator for Rivian."""

    key = "getRegisteredWallboxes"

    async def _fetch_data(self) -> ClientResponse:
        """Fetch the data."""
        return await self.api.get_registered_wallboxes()
