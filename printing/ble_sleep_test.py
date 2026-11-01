"""MXW01 BLE sleep experiment. No printing, no background service."""
import argparse
import asyncio
import datetime as dt
import time

from printing.mxw01 import CONTROL, NOTIFY, command, response_packet

def log(message):
    print(f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')} {message}", flush=True)

async def scan(address, seconds=5):
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=seconds)
    return next((d for d in devices if d.address.upper() == address.upper()), None)

async def status(client):
    queue = asyncio.Queue()
    def notification(_sender, data):
        packet = response_packet(bytes(data))
        if packet and packet[0] == 0xA1:
            queue.put_nowait(packet[1])
    await client.start_notify(NOTIFY, notification)
    try:
        await client.write_gatt_char(CONTROL, command(0xA1, b"\\x00"), response=False)
        payload = await asyncio.wait_for(queue.get(), timeout=8)
        log(f"status response: {payload.hex()}")
    finally:
        await client.stop_notify(NOTIFY)

async def run(args):
    from bleak import BleakClient
    deadline = time.monotonic() + args.minutes * 60
    if args.mode == "persistent":
        device = await scan(args.address)
        if device is None:
            log("Printer not advertising at start"); return
        async with BleakClient(device, timeout=15) as client:
            log("Persistent connection established")
            while time.monotonic() < deadline and client.is_connected:
                if args.status:
                    try:
                        await status(client)
                    except Exception as exc:
                        log(f"Status failed: {exc}")
                log(f"connected={client.is_connected}")
                await asyncio.sleep(args.interval)
            log(f"Final connected={client.is_connected}")
        return
    while time.monotonic() < deadline:
        device = await scan(args.address)
        log("advertising=yes" if device else "advertising=no")
        if device and args.mode == "keepalive":
            try:
                async with BleakClient(device, timeout=15) as client:
                    await status(client)
            except Exception as exc:
                log(f"Keepalive failed: {exc}")
        await asyncio.sleep(args.interval)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("monitor", "keepalive", "persistent"), required=True)
    p.add_argument("--minutes", type=float, default=25)
    p.add_argument("--interval", type=float, default=30)
    p.add_argument("--address", default="48:0F:57:1E:0E:EB")
    p.add_argument("--status", action="store_true", help="Query status in persistent mode")
    args = p.parse_args()
    if args.minutes <= 0 or args.interval < 10:
        p.error("minutes must be positive and interval at least 10 seconds")
    asyncio.run(run(args))

if __name__ == "__main__":
    main()
