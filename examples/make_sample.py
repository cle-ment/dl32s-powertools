#!/usr/bin/env python3
"""Build a small synthetic .dlshow with made-up channel names (no real show data).

uv run examples/make_sample.py examples/sample.dlshow
"""

import struct
import sys
import zipfile


def put_name(buf, off, name):
    buf[off : off + 64] = name.encode().ljust(64, b"\0")


def snapshot(sid, names, stereo_left, usb):
    d = bytearray(25920)
    struct.pack_into("<II", d, 0, 65536, sid)
    for n in range(32):
        struct.pack_into("<II", d, 28 + 424 * n, 1000 + n, 2000 + n)
        struct.pack_into("<I", d, 28 + 424 * n + 44, 1 if n + 1 in stereo_left else 0)
    struct.pack_into("<32I", d, 0x51C0, *usb)
    for i, name in names.items():
        put_name(d, 0x5280 + 64 * i, name)
    return bytes(d)


def show_dat(name, snaps):
    d = bytearray(7816)
    struct.pack_into("<II", d, 0, 1, 65536)
    put_name(d, 8, name)
    struct.pack_into("<I", d, 0x154, 1767225600)
    for i, (sname, sid) in enumerate(snaps):
        off = 0x1A8 + 72 * i
        put_name(d, off, sname)
        struct.pack_into("<II", d, off + 64, i + 1, sid)
    return bytes(d)


def main(out):
    names = {
        0: "Kick",
        1: "Snare",
        2: "Bass",
        3: "Guitar",
        4: "Keys",
        5: "Keys",
        6: "Vocal",
        7: "Vocal Spare",
        8: "Overhead",
        9: "Stale Hidden Name",
    }
    # USB 1/2 = main L/R; USB 3 = input 3, USB 4/5 = inputs 1/2 (remapped), then inputs 4-10
    usb = [10000, 10001, 1002, 1000, 1001] + [1000 + i for i in range(3, 10)]
    usb += [0] * (32 - len(usb))
    snap = snapshot(0x11111111, names, stereo_left={5, 9}, usb=usb)
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("meta_descr.json", '{"description": ""}')
        z.writestr("shows/1/Show.meta", "<device>10</device><version>1</version>")
        z.writestr(
            "shows/1/Show.dat",
            show_dat("Sample Show", [("Sample Snapshot", 0x11111111)]),
        )
        z.writestr("shows/1/Snapshot1.dat", snap)


if __name__ == "__main__":
    main(sys.argv[1])
