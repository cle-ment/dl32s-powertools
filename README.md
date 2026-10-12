# dl32s-powertools

Utilities for the Mackie DL32S mixer. Currently: export channel names from show files.

Unofficial and not affiliated with Mackie or LOUD Audio. The file format was reverse
engineered from a few DL32S exports, so expect gaps (see "Known gaps").

Reads a Mackie DL32S `.dlshow` export and writes the channel names, numbered the way
they arrive over USB after the mixer's USB routing, as TOML (or JSON). Standard library
only; run it with [uv](https://docs.astral.sh/uv/) (Python 3.11+).

## Usage

```sh
uv run export_shows.py list examples/sample.dlshow
uv run export_shows.py export examples/sample.dlshow --show 1 --snapshot 1 --mixdown 1,2 -o channels.toml
```

`list` prints every show folder and its snapshots. `export` takes the numbers from that
list; `--show` / `--snapshot` can be left out when there is only one.

| Option | Effect |
| --- | --- |
| `--numbering usb` (default) | `channel` = USB send channel after remapping |
| `--numbering mixer` | `channel` = mixer input number |
| `--mixdown CH[,CH]` | mark 1 (mono) or 2 (stereo) channels as the mixdown; they get a `[channels.mixdown]` table and are left out of the stems. Without it every channel is a stem (incl. "Main L"/"Main R") |
| `--format json` | JSON instead of TOML |
| `--keep-empty` | include unnamed channels |
| `--all-slots` | also export bus/group names (labels are guesses) |
| `-o FILE` | write to a file instead of stdout |

Output (with `--mixdown 1,2`):

```toml
[channels.mixdown]
channels = [1, 2]

[[channels.stem]]
channel = 3          # USB channel
name = "Kick"
source = "channel_3" # mixer input; also "main_l", "main_r", "unknown_<id>"
```

## File format (reverse engineered)

A `.dlshow` is a zip: `shows/<n>/Show.dat`, `Show.meta`, `SnapshotN.dat`, `Show.png`.
Everything is little-endian. Numbered folders are show slots. In a full-archive export
the same show can appear in several folders as successive revisions (higher revision =
newer), and unused slots are empty folders.

**Show.dat**: u32 revision, u32 flags, 64-byte show name at 8, u32 unix timestamp at
`0x154`, snapshot table at `0x1A8`: 72-byte entries of {64-byte name, u32 index
(1-based), u32 id}. The id equals word 1 of the matching `SnapshotN.dat`.

**SnapshotN.dat** (25920 bytes):
- 7-word header, then 32 input channel records of 106 words each (word 0 = 1000+n,
  word 1 = 2000+n). Further object sections follow (stereo returns, FX, buses).
- **USB send table** at `0x51C0`: 32 u32 source ids, entry k = USB channel k+1.
  `1000+n` = mixer input n+1, `10000`/`10001` = main L/R (inferred), `0` = none.
- **Stereo links**: word 11 of an input record is 1 on the left (odd) channel of a
  stereo-linked pair. The mixer shows that channel's name for both halves; the right
  channel's own name slot holds a stale name, so the export ignores it and names the pair
  `<name> L` / `<name> R` (a left name already ending in " L" keeps it).
- **Name table** at `0x5280`: 70 slots of 64 bytes, NUL-padded UTF-8. Slots 0-31 are
  input channels 1-32.

## Known gaps

- Name slots 32-69 are grouped by inference only (stereo input, sub-groups, aux/output
  buses, DCA/groups, mute groups/user keys).
- Only USB 1/2 = main differs from identity in the sample archive, so non-trivial USB
  remapping is untested.
- Per-channel parameters (gain, gate, compressor, EQ, faders) are not decoded; the
  106-word record layout is known but its fields are unlabelled.
- The 12 words before the USB table (ids 13000-13007, 10000, 10001, 11000) look like
  other output routings and are ignored.

## Sample data

`examples/sample.dlshow` is a synthetic show with made-up names (a remapped USB table, two
stereo pairs). Regenerate it with `uv run examples/make_sample.py examples/sample.dlshow`.
Don't commit real show exports; `.gitignore` excludes `data/` and other `.dlshow` files.
