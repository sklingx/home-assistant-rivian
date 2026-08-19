"""Rivian helpers."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import struct
from typing import Any

from rivian import Rivian

from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_USER_SESSION_TOKEN

TO_REDACT = {
    CONF_EMAIL,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    "hrid",
    "id",
    "identityId",
    "inviteId",
    "mappedIdentityId",
    "orderId",
    "serialNumber",
    "userId",
    "vas",
    "vehicleId",
    "vin",
    "wallboxId",
}

CLEAR_NAVIGATION_DATA: dict[str, dict[str, Any]] = {
    "destination_name": {"value": None},
    "destination_latitude": {"value": None},
    "destination_longitude": {"value": None},
    "destination_eta": {"value": None},
    "destination_distance_remaining": {"value": None},
    "destination_duration_remaining": {"value": None},
    "destination_arrival_soc": {"value": None},
    "destination_route_name": {"value": None},
    "destination_route_polyline": {"value": None},
}


class ProtobufRawDecoder:
    """Raw protobuf tag-wire parser for decoding binary payloads without compiled schemas."""

    WIRE_VARINT = 0
    WIRE_64BIT = 1
    WIRE_LENGTH_DELIMITED = 2
    WIRE_32BIT = 5

    @classmethod
    def decode_varint(cls, data: bytes, offset: int) -> tuple[int, int]:
        """Decode a base-128 varint from bytes."""
        result = 0
        shift = 0
        while offset < len(data):
            byte = data[offset]
            offset += 1
            result |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                return result, offset
            shift += 7
            if shift > 64:
                raise ValueError("Varint too long")
        raise ValueError("Truncated varint")

    @classmethod
    def decode(cls, data: bytes) -> dict[int, Any]:
        """Decode raw protobuf bytes into dictionary of field tags and values."""
        fields: dict[int, Any] = {}
        offset = 0
        data_len = len(data)

        if data_len == 0:
            return fields

        while offset < data_len:
            tag, offset = cls.decode_varint(data, offset)
            wire_type = tag & 0x07
            field_num = tag >> 3

            if field_num == 0 or field_num > 536870911:
                raise ValueError(f"Invalid field number {field_num}")

            if wire_type == cls.WIRE_VARINT:
                val, offset = cls.decode_varint(data, offset)

            elif wire_type == cls.WIRE_64BIT:
                if offset + 8 > data_len:
                    raise ValueError("Truncated 64-bit value")
                chunk = data[offset : offset + 8]
                offset += 8
                (double_val,) = struct.unpack("<d", chunk)
                (uint64_val,) = struct.unpack("<Q", chunk)
                val = {
                    "_type": "fixed64",
                    "uint64": uint64_val,
                    "double": double_val,
                    "hex": chunk.hex(),
                }

            elif wire_type == cls.WIRE_LENGTH_DELIMITED:
                length, offset = cls.decode_varint(data, offset)
                if offset + length > data_len:
                    raise ValueError(
                        f"Truncated length-delimited slice: need {length} bytes"
                    )
                chunk = data[offset : offset + length]
                offset += length

                nested_decoded = None
                if len(chunk) > 0:
                    try:
                        candidate = cls.decode(chunk)
                        if candidate and isinstance(candidate, dict):
                            nested_decoded = candidate
                    except (ValueError, struct.error):
                        pass

                if nested_decoded is not None:
                    val = nested_decoded
                else:
                    try:
                        decoded_str = chunk.decode("utf-8")
                        val = (
                            decoded_str
                            if decoded_str.isprintable()
                            else {
                                "_type": "bytes",
                                "base64": base64.b64encode(chunk).decode("ascii"),
                                "hex": chunk.hex(),
                                "length": len(chunk),
                            }
                        )
                    except UnicodeDecodeError:
                        val = {
                            "_type": "bytes",
                            "base64": base64.b64encode(chunk).decode("ascii"),
                            "hex": chunk.hex(),
                            "length": len(chunk),
                        }

            elif wire_type == cls.WIRE_32BIT:
                if offset + 4 > data_len:
                    raise ValueError("Truncated 32-bit value")
                chunk = data[offset : offset + 4]
                offset += 4
                (float_val,) = struct.unpack("<f", chunk)
                (uint32_val,) = struct.unpack("<I", chunk)
                val = {
                    "_type": "fixed32",
                    "uint32": uint32_val,
                    "float": float_val,
                    "hex": chunk.hex(),
                }
            else:
                raise ValueError(
                    f"Unsupported wire type {wire_type} for field {field_num}"
                )

            if field_num not in fields:
                fields[field_num] = val
            elif isinstance(fields[field_num], list):
                fields[field_num].append(val)
            else:
                fields[field_num] = [fields[field_num], val]

        return fields


def to_timestamp_iso(val: Any) -> str | None:
    """Convert epoch timestamp to ISO string."""
    if isinstance(val, (int, float)) and val > 1000000000:
        if val > 1000000000000:  # milliseconds
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    return None


def parse_parallax_navigation_payload(
    payload_b64_or_bytes: str | bytes | None,
) -> dict[str, dict[str, Any]] | None:
    """Parse a Parallax Protobuf payload (trip_info or trip_progress) into normalized state dict."""
    if not payload_b64_or_bytes:
        return None

    try:
        raw_bytes = (
            base64.b64decode(payload_b64_or_bytes)
            if isinstance(payload_b64_or_bytes, str)
            else payload_b64_or_bytes
        )
        if not raw_bytes:
            return None
        fields = ProtobufRawDecoder.decode(raw_bytes)
    except Exception:  # noqa: BLE001
        return None

    result: dict[str, dict[str, Any]] = {}

    # 1. Check trip_info structure (Field 3 contains distance, duration, destination, routes)
    f3 = fields.get(3)
    if isinstance(f3, dict):
        if 1 in f3 and isinstance(f3[1], dict) and "double" in f3[1]:
            result["destination_distance_remaining"] = {"value": f3[1]["double"]}
        if 2 in f3 and isinstance(f3[2], dict) and "double" in f3[2]:
            result["destination_duration_remaining"] = {"value": f3[2]["double"]}

        dest_container = f3.get(3)
        if isinstance(dest_container, dict):
            dest_item = dest_container.get(1)
            if isinstance(dest_item, dict):
                coords = dest_item.get(1)
                if isinstance(coords, dict):
                    if (
                        1 in coords
                        and isinstance(coords[1], dict)
                        and "double" in coords[1]
                    ):
                        result["destination_latitude"] = {"value": coords[1]["double"]}
                    if (
                        2 in coords
                        and isinstance(coords[2], dict)
                        and "double" in coords[2]
                    ):
                        result["destination_longitude"] = {"value": coords[2]["double"]}
                if 4 in dest_item and isinstance(dest_item[4], str):
                    result["destination_name"] = {"value": dest_item[4]}
                if (
                    7 in dest_item
                    and isinstance(dest_item[7], dict)
                    and "double" in dest_item[7]
                ):
                    result["destination_arrival_soc"] = {
                        "value": round(dest_item[7]["double"], 2)
                    }
                if (
                    9 in dest_item
                    and isinstance(dest_item[9], dict)
                    and 1 in dest_item[9]
                    and (iso := to_timestamp_iso(dest_item[9][1]))
                ):
                    result["destination_eta"] = {"value": iso}

        route_item = f3.get(4)
        if isinstance(route_item, dict):
            if 3 in route_item and isinstance(route_item[3], str):
                result["destination_route_name"] = {"value": route_item[3]}
            if 4 in route_item and isinstance(route_item[4], str):
                result["destination_route_polyline"] = {"value": route_item[4]}

        if (
            6 in f3
            and isinstance(f3[6], dict)
            and "double" in f3[6]
            and "destination_arrival_soc" not in result
        ):
            result["destination_arrival_soc"] = {"value": round(f3[6]["double"], 2)}
        if 10 in f3 and isinstance(f3[10], str):
            result["destination_route_polyline"] = {"value": f3[10]}

    # 2. Check trip_progress structure (Field 4 is distance, Field 5 is duration, Field 6 is location)
    if (
        4 in fields
        and isinstance(fields[4], dict)
        and "double" in fields[4]
        and 5 in fields
        and isinstance(fields[5], dict)
        and "double" in fields[5]
        and 6 in fields
        and isinstance(fields[6], dict)
    ):
        result["destination_distance_remaining"] = {"value": fields[4]["double"]}
        result["destination_duration_remaining"] = {"value": fields[5]["double"]}
        if (
            1 in fields
            and isinstance(fields[1], dict)
            and 1 in fields[1]
            and (iso := to_timestamp_iso(fields[1][1]))
        ) or (
            2 in fields
            and isinstance(fields[2], dict)
            and 1 in fields[2]
            and (iso := to_timestamp_iso(fields[2][1]))
        ):
            result["destination_eta"] = {"value": iso}

    return result if result else None


def get_rivian_api_from_entry(hass: HomeAssistant, entry: ConfigEntry) -> Rivian:
    """Get Rivian API from a config entry."""
    return Rivian(
        request_timeout=30,
        session=async_get_clientsession(hass),
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        user_session_token=entry.data.get(CONF_USER_SESSION_TOKEN),
    )


def redact(data: Any) -> dict:
    """Redact sensitive data."""
    return async_redact_data(data, TO_REDACT)
