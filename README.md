# Thermal Report

Upload FLIR thermal images, define the heat-loss points on them, add your own notes, and produce a heat loss
survey report. Runs on any server and stores its files on local disk, a **WebDAV** share or an **SMB** share.

## Workflow

1. **Upload** 1-8 FLIR `.jpg` images. Temperatures are extracted and areas that are distinctly warmer than the
   wall are detected automatically (see *How warm areas are found* below).
2. **Define points** (`/edit_spots/<batch>`): give each point a *type* (Window, Wall, ...) and a *number*
   (same number in two images = the same feature). Missed something? **Click the picture** to add a point; its
   temperature is read from the thermal data. Too many or too few automatic points? Choose **Fewest...Most** and
   *Re-detect points* (points you added and all notes are kept).
3. **Edit what the report will say.** Under every point a dashed box shows the exact report text: the generated
   comment (editable, with *Reset*), recommendations picked from a per-type list (each editable), and your own
   note. There are also notes for the whole survey and for each picture.
4. **Generate the report.** Page 1: property, summary, about the survey and how to read the pictures. Then
   the thermal pictures four to a page with every point catalogued underneath. Last page: simple energy-saving
   measures with links. All fixed wording lives in `report_content.json` - edit it to suit your group.

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

## Picture colours

The camera bakes its own colour scheme into each JPEG, so surveys taken with different camera settings
look completely different. The app therefore **draws the thermal picture itself from the temperature
readings** in one fixed scale: dark (cold) to light (warm), `inferno` by default, which stays readable with
all common kinds of colour blindness (no green/red). Each picture has a scale bar showing its own temperature
range. Marker colours use the same scale. `PALETTE` can be `cividis`, `grey`, or `camera` (keep the camera's own
picture). Priority names are Low / Medium / High / Very high.

## How warm areas are found

An area is flagged when it is clearly warmer than **the building surface in the same picture**:

1. Very cold pixels (sky, ground) are set aside, so they cannot distort the measurement.
2. The typical wall temperature is the most common temperature among the rest, and the wall's natural
   scatter is measured on its warm side.
3. Pixels above that baseline by a clear margin are grouped into areas; tiny specks are dropped, points are
   never placed on the outer frame, and the strongest are kept (`MAX_SPOTS_PER_IMAGE`).

Each point shows its temperature and **how many degrees warmer than the wall it is** (also used in the report
comment). Priority follows that difference: under 2 degC low, 2+ medium, 4+ high, 8+ very high. Clicking on a
picture to add a point uses the same scale. `SENSITIVITY` (`lowest` ... `highest`, shown as Fewest ... Most) sets
how distinct an area must be, and can be changed per survey; on the six sample photos the standard level finds
1-8 points per image, mostly eaves, rooflines and window/door frames, plus a 16 degC boiler vent hot spot.

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
pytest                                            # 65 tests, no photos or exiftool needed
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
- Batches processed before this change keep their old automatic points (use *Re-detect points* on the points
  page to redo them). Reports made by older versions must be generated again.
- The links on the last report page are root pages of well-known sites; check them and edit `report_content.json`.
- **SMB** is implemented on `smbprotocol` but has only been tested at path-logic level (no SMB server was available).
  **WebDAV** is tested against wsgidav, not yet against Nextcloud/Synology.
- No PDF export yet (the report page has print styles: use the browser's *Print to PDF*).
- Not yet ported to Node/TypeScript.
