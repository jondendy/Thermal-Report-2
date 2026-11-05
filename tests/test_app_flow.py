"""Full workflow through the Flask app, on local disk AND a real WebDAV server."""
import io
import json
import re


def upload(client, jpeg_bytes, names=("one.jpg", "two.jpg")):
    data = {"files[]": [(io.BytesIO(jpeg_bytes), n) for n in names]}
    resp = client.post("/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["batch_id"], resp.get_json()["results"]


def edit_state(client, batch_id):
    html = client.get(f"/edit_spots/{batch_id}").get_data(as_text=True)
    m = re.search(r"const STATE = (\{.*?\});\n", html, re.S)
    assert m, "editor page did not embed its state"
    return json.loads(m.group(1))


def test_upload_detects_hot_spot_and_ignores_sky(client, jpeg_bytes):
    batch_id, results = upload(client, jpeg_bytes)
    assert results["summary"]["total_images"] == 2
    spots = results["images"][0]["hot_spots"]
    assert len(spots) == 1                                  # the hot patch only, not the -60C corner
    row, col = spots[0]["location"]
    assert 40 <= row < 50 and 60 <= col < 72
    assert client.get("/").status_code == 200               # batch listed on the home page
    assert batch_id in client.get("/").get_data(as_text=True)


def test_full_survey_with_notes_produces_report(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes)
    state = edit_state(client, batch_id)
    first, second = state["images"]

    # 1. define the auto-detected point on image one
    auto = first["spots"][0]
    auto.update(type="Wall", spot_number=1, note="Cold bridge at lintel",
                comment="Edited: lintel runs warm along its whole length.", extras=["Check for thermal bridging at structural elements (lintels, columns)"])
    first["image_note"] = "North elevation, taken at dusk"

    # 2. user clicks on image two where nothing was detected -> temperature comes from the data
    probe = client.get(f"/api/temperature/{batch_id}/two.jpg?row=45&col=66").get_json()
    assert probe["temperature"] == 27.0
    second["spots"].append({"spot_id": "two.jpg_manual1", "source": "manual", "location": [probe["row"], probe["col"]],
                            "temperature": 999, "severity": probe["severity"],      # client temperature must be ignored
                            "type": "Wall", "spot_number": 1, "note": "Same wall, other side"})
    payload = {"property_address": "1 High Street", "inspector_name": "J Dendy",
               "general_notes": "Clear night, 2C outside.", "images": state["images"]}

    saved = client.post(f"/save_labels/{batch_id}", json=payload).get_json()
    assert saved["success"] and saved["labeled_spots"] == 3     # the auto point on image 2 arrives pre-labelled too

    # persisted labels: client-supplied temperature was replaced by the real one
    labels = client.application.extensions["service"].get_labels(batch_id)
    manual = [s for s in labels["spots"] if s["source"] == "manual"][0]
    assert manual["temperature"] == 27.0

    # reopening the editor restores everything the user typed
    again = edit_state(client, batch_id)
    assert again["general_notes"] == "Clear night, 2C outside."
    assert again["images"][0]["image_note"] == "North elevation, taken at dusk"
    assert any(s["note"] == "Same wall, other side" for s in again["images"][1]["spots"])

    # 3. generate + view the report
    gen = client.post(f"/generate_heat_loss_report/{batch_id}", json={}).get_json()
    assert gen["success"]
    page = client.get(gen["report_url"]).get_data(as_text=True)
    assert ">Wa1</span>" in page                                      # point code in the catalogue
    assert "Edited: lintel runs warm along its whole length." in page   # the surveyor's own wording replaces the generated text
    assert "Check for thermal bridging at structural elements" in page  # recommendation picked from the list
    assert "Heat loss detected at wall location: 27.0°C" in page       # untouched point keeps the generated comment
    assert "Cold bridge at lintel" in page and "Same wall, other side" in page   # point notes
    assert "North elevation, taken at dusk" in page                 # image note
    assert "Clear night, 2C outside." in page                       # general notes
    assert "1 High Street" in page and "J Dendy" in page
    assert "Simple ways to save energy" in page and "condensing boiler" in page.lower()   # closing page
    assert page.count('<section class="page">') == 3                    # intro, one sheet of pictures, closing page
    assert "critical" not in page.lower()

    # labelled pictures referenced by the report are retrievable
    img = client.get(f"/download/{batch_id}/one_labeled.jpg")
    assert img.status_code == 200 and img.mimetype == "image/jpeg" and img.data[:2] == b"\xff\xd8"
    assert client.get(f"/batch_image/{batch_id}/one.jpg").status_code == 200
    assert client.get(f"/download/{batch_id}/one_temperatures.csv").status_code == 200


def test_user_text_is_escaped_in_report(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    state = edit_state(client, batch_id)
    state["images"][0]["spots"][0].update(type="Door", spot_number=1, note="<script>alert(1)</script>",
                                          comment="<b>bold</b>", extras=["<iframe src=x>"])
    client.post(f"/save_labels/{batch_id}", json={"general_notes": "<img src=x onerror=alert(2)>", "images": state["images"]})
    client.post(f"/generate_heat_loss_report/{batch_id}", json={})
    page = client.get(f"/view_heat_loss_report/{batch_id}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "<img src=x onerror" not in page
    assert "<b>bold</b>" not in page and "<iframe" not in page


def test_report_needs_at_least_one_defined_point(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    resp = client.post(f"/generate_heat_loss_report/{batch_id}", json={})
    assert resp.status_code == 400 and "at least one point" in resp.get_json()["error"]
    assert client.get(f"/view_heat_loss_report/{batch_id}").status_code == 404


def test_delete_batch_removes_everything(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    assert client.post(f"/delete/{batch_id}").get_json()["success"]
    assert client.get(f"/report/{batch_id}").status_code == 404
    st = client.application.extensions["storage"]
    assert not st.exists(f"images/batches/{batch_id}") and not st.exists(f"reports/batches/{batch_id}")


def test_security_checks(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    assert client.get("/report/..%2F..%2Fetc").status_code == 404
    assert client.get("/report/not-a-batch").status_code == 404
    assert client.get(f"/download/{batch_id}/results.json").status_code == 404     # only jpg/csv/html are served
    assert client.get(f"/download/{batch_id}/..%2F..%2Fsecret.csv").status_code == 404
    assert client.post("/upload", data={"files[]": [(io.BytesIO(b"x"), "evil.php")]},
                       content_type="multipart/form-data").status_code == 400
    too_many = {"files[]": [(io.BytesIO(jpeg_bytes), f"{i}.jpg") for i in range(9)]}
    assert client.post("/upload", data=too_many, content_type="multipart/form-data").status_code == 400
    # bad coordinates / unknown image never crash the server
    assert client.get(f"/api/temperature/{batch_id}/one.jpg?row=x&col=1").status_code == 400
    far = client.get(f"/api/temperature/{batch_id}/one.jpg?row=9999&col=-5").get_json()
    assert far["row"] == 119 and far["col"] == 0                                    # clamped to the array


def test_unprocessable_upload_is_reported_and_not_stored(local_storage, jpeg_bytes):
    from app import create_app
    from settings import Settings

    class Broken:
        def process_single_image(self, path, display=False):
            raise RuntimeError("not a radiometric FLIR image")

    client = create_app(storage=local_storage, settings=Settings(), processor_factory=Broken).test_client()
    resp = client.post("/upload", data={"files[]": [(io.BytesIO(jpeg_bytes), "plain.jpg")]},
                       content_type="multipart/form-data")
    assert resp.status_code == 422
    assert "not a radiometric FLIR image" in resp.get_json()["error"]
    assert local_storage.listdir("reports/batches") == []          # nothing half-saved
    assert local_storage.listdir("images/batches") == []
    assert client.get("/healthz").get_json()["status"] == "ok"


def test_automatic_points_arrive_prelabelled_as_windows_and_operator_edits_stick(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg", "two.jpg"))
    state = edit_state(client, batch_id)
    autos = [s for i in state["images"] for s in i["spots"]]
    assert [(s["type"], s["spot_number"]) for s in autos] == [("Window", 1), ("Window", 2)]

    autos[0]["type"], autos[0]["spot_number"] = "Door", 1           # operator: Wi1 is really D1
    client.post(f"/save_labels/{batch_id}", json={"images": state["images"]})
    again = [s for i in edit_state(client, batch_id)["images"] for s in i["spots"]]
    assert [(s["type"], s["spot_number"]) for s in again] == [("Door", 1), ("Window", 2)]

    gen = client.post(f"/generate_heat_loss_report/{batch_id}", json={}).get_json()
    page = client.get(gen["report_url"]).get_data(as_text=True)
    assert ">D1</span>" in page and ">Wi2</span>" in page


class GradedProcessor:
    """A wall at 9 degC with three patches of known strength: +1.1 (subtle), +1.8, +4.5 (obvious)."""

    def process_single_image(self, image_path, display=False):
        import numpy as np
        rng = np.random.default_rng(3)
        t = 9.0 + rng.normal(0, 0.15, (120, 160))
        t[60:68, 20:36] += 1.1
        t[60:68, 80:96] += 1.8
        t[60:68, 130:146] += 4.5
        return t, {"min": float(t.min()), "max": float(t.max()), "mean": float(t.mean()),
                   "median": float(np.median(t)), "std": float(t.std())}


def graded_client(local_storage):
    from app import create_app
    from settings import Settings
    return create_app(storage=local_storage, settings=Settings(), processor_factory=GradedProcessor).test_client()


def test_fewer_or_more_points_at_upload_and_afterwards(local_storage, jpeg_bytes):
    client = graded_client(local_storage)
    resp = client.post("/upload", data={"files[]": [(io.BytesIO(jpeg_bytes), "wall.jpg")], "sensitivity": "lowest"},
                       content_type="multipart/form-data")
    batch_id = resp.get_json()["batch_id"]
    assert edit_state(client, batch_id)["sensitivity"] == "lowest"
    assert len(edit_state(client, batch_id)["images"][0]["spots"]) == 1

    # the operator adds one point by hand, then asks for MORE automatic points
    state = edit_state(client, batch_id)
    p = client.get(f"/api/temperature/{batch_id}/wall.jpg?row=30&col=30").get_json()
    state["images"][0]["spots"].append({"spot_id": "m1", "source": "manual", "location": [p["row"], p["col"]],
                                        "type": "Door", "spot_number": 9, "note": "my own point"})
    state["images"][0]["image_note"] = "keep me"
    client.post(f"/save_labels/{batch_id}", json={"general_notes": "keep this too", "images": state["images"]})

    out = client.post(f"/redetect/{batch_id}", json={"sensitivity": "highest"}).get_json()
    assert out["success"] and out["points"] == 3
    again = edit_state(client, batch_id)
    assert again["sensitivity"] == "highest"
    spots = again["images"][0]["spots"]
    assert [s["source"] for s in spots].count("auto") == 3
    assert [s["note"] for s in spots if s["source"] == "manual"] == ["my own point"]      # manual point survives
    assert again["images"][0]["image_note"] == "keep me" and again["general_notes"] == "keep this too"
    # and back to fewer
    assert client.post(f"/redetect/{batch_id}", json={"sensitivity": "lowest"}).get_json()["points"] == 1
    assert client.post(f"/redetect/{batch_id}", json={"sensitivity": "bogus"}).status_code == 400


def test_pictures_are_drawn_in_one_dark_to_light_scale_not_the_camera_colours(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    state = edit_state(client, batch_id)
    img = state["images"][0]
    assert img["picture"] == "one_thermal.jpg" and img["scale"]["lo"] < img["scale"]["hi"]
    assert img["scale"]["css"].startswith("linear-gradient")
    thermal = client.get(f"/download/{batch_id}/one_thermal.jpg")
    assert thermal.status_code == 200 and thermal.data[:2] == b"\xff\xd8"

    from PIL import Image
    pic = Image.open(io.BytesIO(thermal.data)).convert("RGB")
    assert pic.size == (640, 480)
    wall, hot = pic.getpixel((300, 400)), pic.getpixel((264, 180))      # a wall pixel, then the 27 degC patch
    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    assert lum(hot) > lum(wall) + 60                                       # warmer is clearly lighter
    assert not (wall[1] > wall[0] + 40 and wall[1] > wall[2] + 40)          # no green anywhere in the scale
    assert not (hot[1] > hot[0] + 40 and hot[1] > hot[2] + 40)


def test_camera_palette_keeps_the_cameras_own_picture(local_storage, jpeg_bytes):
    from app import create_app
    from settings import Settings
    from tests.conftest import FakeProcessor
    client = create_app(storage=local_storage, settings=Settings(palette="camera"), processor_factory=FakeProcessor).test_client()
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    img = edit_state(client, batch_id)["images"][0]
    assert img["picture"] is None and img["scale"] is None
    assert not local_storage.exists(f"reports/batches/{batch_id}/one_thermal.jpg")


def test_batches_made_before_this_update_get_the_new_picture_when_opened(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    st = client.application.extensions["storage"]
    st.delete(f"reports/batches/{batch_id}/one_thermal.jpg")             # as an older batch would be
    img = edit_state(client, batch_id)["images"][0]
    assert img["picture"] == "one_thermal.jpg" and st.exists(f"reports/batches/{batch_id}/one_thermal.jpg")


def test_report_text_preview_endpoint_matches_the_report(client):
    body = {"type": "Window", "temperature": 16.24, "excess": 7.4, "severity": "high"}
    text = client.post("/api/point_text", json=body).get_json()["comment"]
    assert text.startswith("Heat loss detected at window location: 16.2°C, 7.4°C warmer than the surrounding wall surface.")
    assert client.post("/api/point_text", json={"type": "Nope"}).get_json()["comment"] == ""


def test_the_word_critical_appears_on_no_page(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes)
    state = edit_state(client, batch_id)
    client.post(f"/save_labels/{batch_id}", json={"images": state["images"]})
    client.post(f"/generate_heat_loss_report/{batch_id}", json={})
    for url in ("/", "/info", f"/report/{batch_id}", f"/edit_spots/{batch_id}", f"/view_heat_loss_report/{batch_id}",
                f"/download/{batch_id}/one_thermal_report.html"):
        assert "critical" not in client.get(url).get_data(as_text=True).lower(), url
