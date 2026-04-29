import pytest

from satel_integra.models import SatelDeviceType, SatelOutputInfo, SatelZoneInfo


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
