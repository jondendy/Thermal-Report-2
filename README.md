# Thermal Report

Upload FLIR thermal images, define the heat-loss points on them, add your own notes, and produce a heat loss
survey report. Runs on any server and stores its files on local disk, a **WebDAV** share or an **SMB** share.

## Workflow

1. **Upload** 1-8 FLIR `.jpg` images. Temperatures are extracted and areas that are distinctly warmer than the
   wall are detected automatically (see *How warm areas are found* below).
2. **Define points** (`/edit_spots/<batch>`): give each point a *type* (Window, Wall, ...) and a *number*
   (same number in two images = the same feature). Missed something? **Click the picture** to add a point; its
   temperature is read from the thermal data.
3. **Add notes** in your own words at three levels: whole survey, each image, each point.
4. **Generate the report.** For every finding it shows the comment generated from your point definition
   (severity, temperatures, recommendations) *and* your freehand notes, plus the labelled pictures.

## Run it

```bash
sudo apt install libimage-exiftool-perl          # FLIR reader dependency (macOS: brew install exiftool)
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp env.example .env                             # then export the variables, or set them in your service
python app.py                                    # development  ->  http://localhost:5000
gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 300 wsgi:app    # production
```

Docker: `docker build -t thermal-report . && docker run -p 5000:5000 -v $PWD/data:/app/data thermal-report`

## How warm areas are found

An area is flagged when it is clearly warmer than **the building surface in the same picture**:

1. Very cold pixels (sky, ground) are set aside, so they cannot distort the measurement.
2. The typical wall temperature is the most common temperature among the rest, and the wall's natural
   scatter is measured on its warm side.
3. Pixels above that baseline by a clear margin are grouped into areas; tiny specks are dropped, points are
   never placed on the outer frame, and the strongest are kept (`MAX_SPOTS_PER_IMAGE`).

Each point shows its temperature and **how many degrees warmer than the wall it is** (also used in the report
comment). Severity follows that difference: under 2 degC low, 2+ medium, 4+ high, 8+ critical. Clicking on a
picture to add a point uses the same scale. `SENSITIVITY` (`low` / `medium` / `high`) sets how distinct an area
must be; on the six sample photos `medium` finds 1-8 points per image, mostly eaves, rooflines and window/door
frames, plus a 16 degC window hot spot.

## Storage

Set `STORAGE_BACKEND` (see `env.example`):

| Backend  | Use it for | Notes |
|----------|-----------|-------|
| `local`  | A folder, or an SMB/NFS share **already mounted** on the server | `STORAGE_ROOT` |
| `webdav` | Nextcloud, ownCloud, Synology, Apache/nginx DAV, NAS boxes | `WEBDAV_URL`, user, password |
| `smb`    | Windows/Samba shares, no mount needed | `smbprotocol`; `SMB_SERVER`, `SMB_SHARE`, ... |

All code reads/writes through `storage.StorageBackend`, so adding another backend (S3, SFTP, ...) means one new
class implementing seven small methods.

## Tests

```bash
pytest                                            # 51 tests, no photos or exiftool needed
THERMAL_TEST_IMAGES=/path/to/flir pytest          # also runs the real FLIR extraction tests
```

The WebDAV tests run against a real WebDAV server (wsgidav) started by the test suite.

## Where each part came from (git provenance)

| Part | Source |
|------|--------|
| FLIR extraction + temperature stats (`flir_processor.py`) | `legacy-stable:experiments/flir_processor_simple.py` (adds the colour-blind-friendly colormap). Same extraction code as `heat-loss-report`. |
| Warm-area detection (`warm_areas.py`) | new: replaces the `heat-loss-report` detector (`thermal_analyzer.py`, kept for its report-text helpers) |
| Point definition: type + number, grid editor, labelled report (`templates/edit_spots.html`, `heat_loss_report.html`, `heat_loss_reporter.py`) | `heat-loss-report` (`c59dd99`, `c1c83c0`, `9966258`, `24aeaf1`), rebuilt - see below |
| Freehand notes: survey notes, per-finding notes | idea and report layout from `GCSmain` (`3b7d0b6`, `ed8e900`); extended with per-image notes |
| Storage layer (`storage/`) | new |
| Google Drive code | deliberately left out |

The FLIR library is [`nationaldronesau/FlirImageExtractor`](https://github.com/nationaldronesau/FlirImageExtractor)
(PyPI: `flirimageextractor`). It is **pinned to 1.3.0**: newer 1.5.x versions download a DJI SDK on first use,
which fails offline and on read-only hosts.

## Bugs fixed on the way

- Saving points sent placeholder values (temperature `0`, location `0,0`, severity `medium`) instead of the real
  ones, so reports contained wrong data. Now the server reads real values and ignores client-supplied temperatures.
- The editor read `image_name` but the analysis stored `filename`, so image names were blank.
- The sky-reflection filter computed a mask and then threw it away; it is now applied.
- Label drawing assumed a 120x160 sensor and used `arial.ttf` (missing on Linux); it now scales from the real
  array and bundles a font. Overlapping labels are nudged apart.
- `energy_recommendations.json` was loaded relative to the working directory; its `_metadata` key was treated as a spot type.
- Findings were grouped by number only (Wall #1 and Window #1 merged); now by type + number.
- Broken "Add points" link, CSV links that failed for `.jpeg`/`.JPG`, debug mode on by default, no path validation
  on downloads, user text not validated.
- `flir_processor_simple.py` (legacy) used `LinearSegmentedColormap` without importing it and called `exit(1)` at
  import time, which would kill a web server.

## Known limits

- **No login.** Put it behind a VPN, reverse proxy with auth, or your network firewall.
- **Detection is relative to the wall.** A building that is warm all over (e.g. a whole elevation lit by
  the sun or an uninsulated gable) has nothing "distinctly warmer" than itself, so it can find few points;
  that is a real limit of comparing a wall with itself. Use click-to-add for anything the detector misses.
- Batches processed before this change keep their old automatic points; re-upload their images to re-detect.
- **SMB** is implemented on `smbprotocol` but has only been tested at path-logic level (no SMB server was available).
  **WebDAV** is tested against wsgidav, not yet against Nextcloud/Synology.
- No PDF export yet (the report page has print styles: use the browser's *Print to PDF*).
- Not yet ported to Node/TypeScript.
