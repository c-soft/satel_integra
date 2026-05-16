import pytest

from satel_integra.models import (
    SatelDeviceType,
    SatelFirmwareVersion,
    SatelOutputInfo,
    SatelPartitionInfo,
    SatelZoneInfo,
)


@pytest.mark.parametrize(
    (
        "partition_byte",
        "name_payload",
        "object_assignment",
        "expected_number",
        "expected_name",
        "expected_object_assignment",
    ),
    [
        (0x01, b"Ground Floor    ", 0x02, 1, "Ground Floor", 2),
        (0x20, b"Guest Wing      ", 0x08, 32, "Guest Wing", 8),
        (
            0x02,
            b"Night\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            0x00,
            2,
            "Night",
            None,
        ),
    ],
)
def test_partition_info_from_payload(
    partition_byte,
    name_payload,
    object_assignment,
    expected_number,
    expected_name,
    expected_object_assignment,
) -> None:
    payload = (
        bytearray([0x10, partition_byte, 0x03])
        + bytearray(name_payload)
        + bytearray([object_assignment])
    )

    partition_info = SatelPartitionInfo._from_payload(payload)

    assert partition_info.device_type is SatelDeviceType.PARTITION
    assert partition_info.device_number == expected_number
    assert partition_info.name == expected_name
    assert partition_info.type_code == 0x03
    assert partition_info.object_assignment == expected_object_assignment


@pytest.mark.parametrize(
    (
        "zone_byte",
        "name_payload",
        "partition_assignment",
        "expected_number",
        "expected_name",
        "expected_partition_assignment",
    ),
    [
        (0x01, b"Front Door      ", 0x03, 1, "Front Door", 3),
        (0x00, b"Top Floor       ", 0x00, 256, "Top Floor", None),
        (
            0x02,
            b"Garage\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            0x01,
            2,
            "Garage",
            1,
        ),
    ],
)
def test_zone_info_from_payload(
    zone_byte,
    name_payload,
    partition_assignment,
    expected_number,
    expected_name,
    expected_partition_assignment,
) -> None:
    payload = (
        bytearray([0x05, zone_byte, 0x2A])
        + bytearray(name_payload)
        + bytearray([partition_assignment])
    )

    zone_info = SatelZoneInfo._from_payload(payload)

    assert zone_info.device_number == expected_number
    assert zone_info.name == expected_name
    assert zone_info.type_code == 0x2A
    assert zone_info.partition_assignment == expected_partition_assignment


@pytest.mark.parametrize(
    (
        "output_byte",
        "name_payload",
        "expected_number",
        "expected_name",
    ),
    [
        (0x01, b"Output 1        ", 1, "Output 1"),
        (0x00, b"Bell            ", 256, "Bell"),
        (
            0x02,
            b"Strobe\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            2,
            "Strobe",
        ),
    ],
)
def test_output_info_from_payload(
    output_byte,
    name_payload,
    expected_number,
    expected_name,
) -> None:
    payload = bytearray([0x04, output_byte, 0x10]) + bytearray(name_payload)

    output_info = SatelOutputInfo._from_payload(payload)

    assert output_info.device_type is SatelDeviceType.OUTPUT
    assert output_info.device_number == expected_number
    assert output_info.name == expected_name
    assert output_info.type_code == 0x10


def test_firmware_version_formats_as_string() -> None:
    firmware = SatelFirmwareVersion("1.23", "2025-05-15")

    assert str(firmware) == "1.23 (2025-05-15)"
