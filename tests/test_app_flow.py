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
    auto.update(type="Wall", spot_number=1, note="Cold bridge at lintel")
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
    assert "Heat loss at Wall #1" in page
    assert "Heat loss detected at wall" in page                     # auto-generated comment
    assert "Cold bridge at lintel" in page and "Same wall, other side" in page   # point notes
    assert "North elevation, taken at dusk" in page                 # image note
    assert "Clear night, 2C outside." in page                       # general notes
    assert "1 High Street" in page and "J Dendy" in page

    # labelled pictures referenced by the report are retrievable
    img = client.get(f"/download/{batch_id}/one_labeled.jpg")
    assert img.status_code == 200 and img.mimetype == "image/jpeg" and img.data[:2] == b"\xff\xd8"
    assert client.get(f"/batch_image/{batch_id}/one.jpg").status_code == 200
    assert client.get(f"/download/{batch_id}/one_temperatures.csv").status_code == 200


def test_user_text_is_escaped_in_report(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg",))
    state = edit_state(client, batch_id)
    state["images"][0]["spots"][0].update(type="Door", spot_number=1, note="<script>alert(1)</script>")
    client.post(f"/save_labels/{batch_id}", json={"general_notes": "<img src=x onerror=alert(2)>", "images": state["images"]})
    client.post(f"/generate_heat_loss_report/{batch_id}", json={})
    page = client.get(f"/view_heat_loss_report/{batch_id}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "<img src=x onerror" not in page


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


def test_automatic_points_arrive_prelabelled_as_walls_and_operator_edits_stick(client, jpeg_bytes):
    batch_id, _ = upload(client, jpeg_bytes, names=("one.jpg", "two.jpg"))
    state = edit_state(client, batch_id)
    autos = [s for i in state["images"] for s in i["spots"]]
    assert [(s["type"], s["spot_number"]) for s in autos] == [("Wall", 1), ("Wall", 2)]

    autos[0]["type"], autos[0]["spot_number"] = "Door", 1           # operator: W1 is really D1
    client.post(f"/save_labels/{batch_id}", json={"images": state["images"]})
    again = [s for i in edit_state(client, batch_id)["images"] for s in i["spots"]]
    assert [(s["type"], s["spot_number"]) for s in again] == [("Door", 1), ("Wall", 2)]

    gen = client.post(f"/generate_heat_loss_report/{batch_id}", json={}).get_json()
    page = client.get(gen["report_url"]).get_data(as_text=True)
    assert "Heat loss at Door #1" in page and "Heat loss at Wall #2" in page
