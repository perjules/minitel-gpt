"""MXW01 BLE transport. Optional: importing this module does not require Bleak."""
import asyncio

WIDTH = 384
CONTROL = "0000ae01-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"
DATA = "0000ae03-0000-1000-8000-00805f9b34fb"


def crc8(payload: bytes) -> int:
    crc = 0
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 0xFF
    return crc


def command(code: int, payload: bytes, *, checksum: bool = True) -> bytes:
    if len(payload) > 65535:
        raise ValueError("Command payload too large")
    return (b"\x22\x21" + bytes((code, 0)) + len(payload).to_bytes(2, "little")
            + payload + bytes((crc8(payload) if checksum else 0, 0xFF)))


def pack_image(image) -> bytes:
    """Pack 1-bit Pillow image, black=1, least significant bit first."""
    if image.width != WIDTH or image.height < 1 or image.height > 6000:
        raise ValueError("Expected a 384-pixel-wide image, height 1..6000")
    mono = image.convert("1")
    pixels = mono.load()
    data = bytearray()
    for y in range(mono.height):
        for x in range(0, WIDTH, 8):
            byte = 0
            for bit in range(8):
                if pixels[x + bit, y] == 0:
                    byte |= 1 << bit
            data.append(byte)
    return bytes(data)


def response_packet(raw: bytes):
    if len(raw) < 7 or raw[:2] != b"\x22\x21":
        return None
    length = int.from_bytes(raw[4:6], "little")
    if len(raw) < 7 + length:
        return None
    return raw[2], raw[6:6 + length]


class MXW01:
    """One connection per job. No global callbacks or background daemon."""

    def __init__(self, address: str, *, scan_timeout: float = 10, timeout: float = 15):
        self.address = address
        self.scan_timeout = scan_timeout
        self.timeout = timeout

    async def print_image(self, image):
        from bleak import BleakClient, BleakScanner

        packed = pack_image(image)
        # Scan first to obtain a BLE device object, avoiding BlueZ classic fallback.
        devices = await BleakScanner.discover(timeout=self.scan_timeout)
        device = next((d for d in devices if d.address.upper() == self.address.upper()), None)
        if device is None:
            raise RuntimeError("MXW01 not found by Bluetooth LE scan")
        responses = asyncio.Queue()

        def on_notification(_sender, data):
            parsed = response_packet(bytes(data))
            if parsed is not None:
                responses.put_nowait(parsed)

        async def await_response(code, timeout):
            async def receive():
                while True:
                    kind, payload = await responses.get()
                    if kind == code:
                        return payload
            return await asyncio.wait_for(receive(), timeout)

        async with BleakClient(device, timeout=self.timeout) as client:
            await client.start_notify(NOTIFY, on_notification)
            try:
                for code, payload in ((0xB1, b"\x00"), (0xA2, b"\x5d"), (0xA1, b"\x00")):
                    await client.write_gatt_char(CONTROL, command(code, payload), response=False)
                    await asyncio.sleep(0.01)
                status = await await_response(0xA1, self.timeout)
                if len(status) < 13 or status[12] != 0:
                    raise RuntimeError(\n                        f"Printer status not ready: {len(status)} bytes, payload={status.hex()} "\n                        "(expected >=13 bytes with status flag at offset 12 equal to 00)"\n                    )
                await client.write_gatt_char(CONTROL, command(0xA2, b"\x5d"), response=False)
                request = image.height.to_bytes(2, "little") + (48).to_bytes(2, "little")
                await client.write_gatt_char(CONTROL, command(0xA9, request, checksum=False), response=False)
                accepted = await await_response(0xA9, self.timeout)
                if not accepted or accepted[0] != 0:
                    raise RuntimeError("Printer rejected print request")
                if len(packed) < 4320:
                    packed += bytes(4320 - len(packed))
                for offset in range(0, len(packed), 20):
                    await client.write_gatt_char(DATA, packed[offset:offset + 20], response=False)
                    await asyncio.sleep(0.015)
                await client.write_gatt_char(CONTROL, command(0xAD, b"\x00", checksum=False), response=False)
                # AA completion notification is not consistently delivered by firmware.
                try:
                    await await_response(0xAA, max(15, image.height / 20))
                except asyncio.TimeoutError:
                    raise TimeoutError("Printer did not acknowledge completion; paper may have printed")
            finally:
                await client.stop_notify(NOTIFY)
