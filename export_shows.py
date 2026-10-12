#!/usr/bin/env python3
"""Read Mackie DL show archives (.dlshow = zip) and export channel names.

  uv run export_shows.py list   shows.dlshow
  uv run export_shows.py export shows.dlshow --show 22 --snapshot 1 [-o out.toml]
  uv run export_shows.py export shows.dlshow --show 22 --format json --all-slots

Show.dat   u32 revision, u32 flags, 64B show name @8, u32 unix time @0x154,
           snapshot table @0x1a8: 72B entries = 64B name, u32 index (1-based), u32 id
           (id == word 1 of the matching SnapshotN.dat).
SnapshotN  25920 B; 64B NUL-padded name slots at 0x5280 (70 slots).
           Slots 0-31 = input channels 1-32. Slots 32+ are only inferred (see GROUPS).
           Input record n (32 x 106 words from byte 28): word 11 == 1 on the left channel of a
           stereo-linked pair (odd channel); the mixer shows both halves under the left's name; the export suffixes " L"/" R".
           USB send table @0x51C0: 32 u32 source ids, entry k = USB channel k+1.
           1000+n = input channel n+1 (source = "channel_<n+1>"); 10000/10001 = main L/R (inferred); 0 = none.
"""

import argparse
import datetime
import json
import re
import struct
import sys
import zipfile

NAME_TABLE, NAME_SLOTS, NAME_LEN = 0x5280, 70, 64
SNAP_TABLE, SNAP_ENTRY = 0x1A8, 72
USB_TABLE, USB_CHANNELS = (
    0x51C0,
    32,
)  # u32 source id per USB send channel (0 = unassigned)
GROUPS = [
    (32, 33, "stereo/line input"),
    (34, 41, "unclear (FX?)"),
    (42, 47, "sub-group bus"),
    (48, 56, "aux / output bus"),
    (57, 62, "DCA / group"),
    (63, 69, "mute group / user key"),
]


def cstr(b):
    return b.split(b"\0")[0].decode("utf-8", "replace")


def group(slot):
    return next((g for lo, hi, g in GROUPS if lo <= slot <= hi), "unknown")


def load(path):
    """Return {show_id: {file_name: bytes}} for every non-empty show folder."""
    z = zipfile.ZipFile(path)
    shows = {}
    for n in z.namelist():
        m = re.fullmatch(r"shows/(\d+)/([^/]+)", n)
        if m:
            shows.setdefault(int(m[1]), {})[m[2]] = z.read(n)
    return dict(sorted(shows.items()))


def show_info(files):
    d = files["Show.dat"]
    rev = struct.unpack_from("<I", d, 0)[0]
    ts = struct.unpack_from("<I", d, 0x154)[0]
    table = {}
    for o in range(SNAP_TABLE, len(d) - SNAP_ENTRY + 1, SNAP_ENTRY):
        idx, sid = struct.unpack_from("<II", d, o + 64)
        if sid:
            table[sid] = cstr(d[o : o + 64])
    snaps = {}
    for f, data in files.items():
        m = re.fullmatch(r"Snapshot(\d+)\.dat", f)
        if m:
            sid = struct.unpack_from("<I", data, 4)[0]
            snaps[int(m[1])] = {"name": table.get(sid, ""), "id": sid, "data": data}
    return {
        "name": cstr(d[8:72]),
        "revision": rev,
        "timestamp": ts,
        "snapshots": dict(sorted(snaps.items())),
    }


def slots(data):
    return [
        cstr(data[NAME_TABLE + i * NAME_LEN : NAME_TABLE + (i + 1) * NAME_LEN])
        for i in range(NAME_SLOTS)
    ]


def usable(shows):
    return {
        k: show_info(v)
        for k, v in shows.items()
        if "Show.dat" in v and any(f.startswith("Snapshot") for f in v)
    }


def cmd_list(a):
    for k, s in usable(load(a.archive)).items():
        when = (
            datetime.datetime.fromtimestamp(s["timestamp"]).strftime("%Y-%m-%d")
            if s["timestamp"]
            else "?"
        )
        print(f'show {k}: "{s["name"]}"  rev {s["revision"]}  {when}')
        for n, sn in s["snapshots"].items():
            names = [x for x in slots(sn["data"])[:32] if x]
            print(
                f'    snapshot {n}: "{sn["name"]}"  ({len(names)} named inputs: {", ".join(names[:4])}...)'
            )


def pick(d, key, what):
    if key is None:
        if len(d) == 1:
            return next(iter(d))
        sys.exit(
            f"several {what}s ({', '.join(map(str, d))}); choose with --{what} (see `list`)"
        )
    if key not in d:
        sys.exit(f"no {what} {key}; available: {', '.join(map(str, d))}")
    return key


def usb_sources(data):
    """List of (usb_channel, source_id) for assigned USB send channels."""
    ids = struct.unpack_from(f"<{USB_CHANNELS}I", data, USB_TABLE)
    return [(i + 1, sid) for i, sid in enumerate(ids) if sid]


def describe(sid, names):
    """-> (source label, name)"""
    if 1000 <= sid < 1032:
        n = sid - 999
        return f"channel_{n}", names[n - 1]
    if sid in (10000, 10001):
        return ("main_l", "Main L") if sid == 10000 else ("main_r", "Main R")
    return f"unknown_{sid}", ""


def apply_stereo_links(data, names):
    """Name both halves of a stereo-linked input pair "<name> L" / "<name> R".

    Word 11 of an input record is 1 on the left (odd) channel of a stereo pair; the
    right channel's own name slot only holds a stale, hidden name. A left name that
    already ends in " L" is kept and its right half becomes the matching " R".
    """
    names = list(names)
    for i in range(0, 32, 2):
        if struct.unpack_from("<I", data, 28 + 424 * i + 44)[0] == 1 and names[i]:
            base = names[i][:-2] if names[i].endswith(" L") else names[i]
            names[i], names[i + 1] = f"{base} L", f"{base} R"
    return names


def parse_mixdown(value):
    """'1,2' -> [1, 2]; only mono (1 value) or stereo (2 values) are supported."""
    try:
        chans = [int(v) for v in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected e.g. 1 or 1,2, got {value!r}")
    if not 1 <= len(chans) <= 2:
        raise argparse.ArgumentTypeError("mixdown must be 1 (mono) or 2 (stereo) channels")
    if len(set(chans)) != len(chans) or not all(1 <= c <= USB_CHANNELS for c in chans):
        raise argparse.ArgumentTypeError(f"channels must be distinct and in 1-{USB_CHANNELS}")
    return chans


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)  # JSON basic strings are valid TOML


def cmd_export(a):
    shows = usable(load(a.archive))
    sk = pick(shows, a.show, "show")
    show = shows[sk]
    nk = pick(show["snapshots"], a.snapshot, "snapshot")
    snap = show["snapshots"][nk]
    names = apply_stereo_links(snap["data"], slots(snap["data"]))
    chans = []
    if a.numbering == "mixer":
        chans = [
            {"channel": i + 1, "name": n, "source": f"channel_{i + 1}"}
            for i, n in enumerate(names[:32])
            if n or a.keep_empty
        ]
    else:
        for usb, sid in usb_sources(snap["data"]):
            src, name = describe(sid, names)
            if name or a.keep_empty:
                chans.append({"channel": usb, "name": name, "source": src})
    if a.mixdown:
        chans = [c for c in chans if c["channel"] not in a.mixdown]
    other = [
        {"slot": i, "group": group(i), "name": n}
        for i, n in enumerate(names)
        if i >= 32 and n
    ]
    if a.format == "json":
        out = json.dumps(
            {
                "show": show["name"],
                "snapshot": snap["name"],
                **({"mixdown": a.mixdown} if a.mixdown else {}),
                "channels": chans,
                **({"other_slots": other} if a.all_slots else {}),
            },
            indent=2,
            ensure_ascii=False,
        )
    else:
        lines = [f"# show {sk}: {show['name']} / snapshot {nk}: {snap['name']}", ""]
        if a.mixdown:
            lines += [
                "[channels.mixdown]",
                f"channels = [{', '.join(map(str, a.mixdown))}]",
                "",
            ]
        for c in chans:
            src = c.get("source")
            channel = f"channel = {c['channel']}"
            if src and src != f"channel_{c['channel']}":
                channel += (
                    f"  # source: {src}"  # USB channel differs from the mixer channel
                )
            lines += ["[[channels.stem]]", channel, f"name = {toml_str(c['name'])}", ""]
        if a.all_slots:
            for o in other:
                lines += [
                    "[[other.slot]]",
                    f"slot = {o['slot']}",
                    f"group = {toml_str(o['group'])}",
                    f"name = {toml_str(o['name'])}",
                    "",
                ]
        out = "\n".join(lines)
    if a.output:
        open(a.output, "w", encoding="utf-8").write(out + "\n")
    else:
        print(out)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list", help="show shows and snapshots in the archive")
    l.add_argument("archive")
    l.set_defaults(fn=cmd_list)
    e = sub.add_parser("export", help="export one snapshot's channel names")
    e.add_argument("archive")
    e.add_argument("--show", type=int, help="show folder number from `list`")
    e.add_argument("--snapshot", type=int, help="snapshot number from `list`")
    e.add_argument(
        "--numbering",
        choices=["usb", "mixer"],
        default="usb",
        help="channel = USB send channel after remapping (default) or the mixer input number",
    )
    e.add_argument(
        "--mixdown",
        type=parse_mixdown,
        metavar="CH[,CH]",
        help="channel(s) carrying the mixdown, e.g. 1,2 (stereo) or 1 (mono); "
        "written as [channels.mixdown] and left out of the stems. "
        "Uses the --numbering channel numbers. Without it, every channel is a stem.",
    )
    e.add_argument("--format", choices=["toml", "json"], default="toml")
    e.add_argument(
        "--all-slots",
        action="store_true",
        help="also export slots 32+ (buses, groups; labels inferred)",
    )
    e.add_argument(
        "--keep-empty", action="store_true", help="include unnamed input channels"
    )
    e.add_argument("-o", "--output")
    e.set_defaults(fn=cmd_export)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
