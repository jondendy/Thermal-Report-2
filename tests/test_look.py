"""Per-batch picture colours: style (inferno, ironbow, camera...) and temperature range."""
import io
import json

import numpy as np
import pytest
from PIL import Image

import palette
from app import create_app
from settings import Settings, load_settings
from storage import LocalStorage
from tests.conftest import FakeProcessor, synthetic_jpeg
from tests.test_app_flow import edit_state


@pytest.fixture
def c(tmp_path):
    app = create_app(storage=LocalStorage(str(tmp_path / "s")), settings=Settings(batch_size=8),
                     processor_factory=FakeProcessor)
    app.config["TESTING"] = True
    return app.test_client()


def new_batch(c, n=2):
    files = [(io.BytesIO(synthetic_jpeg()), f"p{i}.jpg") for i in range(n)]
    return c.post("/upload", data={"files[]": files}, content_type="multipart/form-data").get_json()["batch_id"]


def picture(c, bid, name):
    r = c.get(f"/download/{bid}/{name}")
    assert r.status_code == 200
    return r.data


def look(c, bid, **body):
    return c.post(f"/look/{bid}", json=body)


# ------------------------------------------------------------------ the palette module
def test_ironbow_runs_dark_to_light_without_green():
    cmap = palette._cmap("ironbow")
    rgb = np.array([cmap(x)[:3] for x in np.linspace(0, 1, 64)])
    light = rgb @ np.array([0.2126, 0.7152, 0.0722])
    assert np.all(np.diff(light) > 0), "brightness must rise steadily from cold to warm"
    assert light[0] < 0.02 and light[-1] > 0.9
    assert not np.any((rgb[:, 1] > rgb[:, 0] + 0.05) & (rgb[:, 1] > rgb[:, 2] + 0.05)), "no green stretch"


def test_render_with_a_set_range():
    temp = np.linspace(0, 20, 160 * 120).reshape(120, 160)
    span = lambda img: float(np.ptp(np.asarray(img.convert("L"), dtype=float)))
    auto, wide = palette.render(temp, "inferno"), palette.render(temp, "inferno", lo=-40, hi=60)
    assert span(auto) > 200 and span(wide) < span(auto) / 2              # a wider scale flattens the picture
    narrow = np.asarray(palette.render(temp, "inferno", lo=9, hi=11).convert("L"))
    assert narrow[0, 0] < 10 and narrow[-1, -1] > 200                    # outside the range: clipped to the ends
    got = palette.legend(temp, "ironbow", 5, 15)
    assert (got["lo"], got["hi"], got["palette"]) == (5.0, 15.0, "ironbow") and got["css"].startswith("linear-gradient")
    assert palette.legend(temp, "inferno", 9, 3)["lo"] != 9                # a nonsense range falls back to automatic
    assert [p["key"] for p in palette.choices()] == ["inferno", "ironbow", "cividis", "grey", "camera"]


def test_settings_accept_ironbow():
    assert load_settings({"PALETTE": "Ironbow"}).palette == "ironbow"
    assert load_settings({"PALETTE": "rainbow"}).palette == "inferno"


# ------------------------------------------------------------------ switching a batch
def test_starts_automatic_in_the_default_style(c):
    bid = new_batch(c)
    state = edit_state(c, bid)
    assert state["look"] == {"palette": "inferno", "mode": "auto", "lo": None, "hi": None}
    assert state["data_range"]["lo"] < state["data_range"]["hi"]
    assert all(i["scale"]["palette"] == "inferno" for i in state["images"])


def test_change_style_redraws_pictures_and_scale_bars(c):
    bid = new_batch(c)
    before_plain, before_marked = picture(c, bid, "p0_thermal.jpg"), picture(c, bid, "p0_labeled.jpg")
    assert look(c, bid, palette="ironbow").get_json()["look"]["palette"] == "ironbow"
    state = edit_state(c, bid)
    assert all(i["scale"]["palette"] == "ironbow" for i in state["images"])
    assert picture(c, bid, "p0_thermal.jpg") != before_plain and picture(c, bid, "p0_labeled.jpg") != before_marked


def test_set_range_by_hand(c):
    bid = new_batch(c)
    r = look(c, bid, palette="inferno", mode="custom", lo=0, hi="30")
    assert r.status_code == 200 and r.get_json()["look"] == {"palette": "inferno", "mode": "custom", "lo": 0.0, "hi": 30.0}
    state = edit_state(c, bid)
    assert [(i["scale"]["lo"], i["scale"]["hi"]) for i in state["images"]] == [(0.0, 30.0)] * 2
    assert state["look"]["mode"] == "custom"


def test_same_scale_on_every_picture(c):
    bid = new_batch(c, 3)
    got = look(c, bid, palette="cividis", mode="batch").get_json()["look"]
    assert got["mode"] == "batch" and got["lo"] < got["hi"]
    scales = {(i["scale"]["lo"], i["scale"]["hi"]) for i in edit_state(c, bid)["images"]}
    assert scales == {(got["lo"], got["hi"])}


def test_camera_style_and_back(c):
    bid = new_batch(c)
    assert look(c, bid, palette="camera", mode="custom", lo=0, hi=30).get_json()["look"] == \
        {"palette": "camera", "mode": "auto", "lo": None, "hi": None}       # the camera picture cannot be re-scaled
    state = edit_state(c, bid)
    assert all(i["picture"] is None and i["scale"] is None for i in state["images"])
    Image.open(io.BytesIO(picture(c, bid, "p0_labeled.jpg"))).verify()      # marked picture drawn on the camera's own
    look(c, bid, palette="inferno")
    assert all(i["picture"] and i["scale"]["palette"] == "inferno" for i in edit_state(c, bid)["images"])


@pytest.mark.parametrize("body", [
    {"palette": "rainbow"}, {"palette": ""}, {}, {"palette": "inferno", "mode": "sideways"},
    {"palette": "inferno", "mode": "custom"}, {"palette": "inferno", "mode": "custom", "lo": 20, "hi": 10},
    {"palette": "inferno", "mode": "custom", "lo": 10, "hi": 10.4}, {"palette": "inferno", "mode": "custom", "lo": "a", "hi": 5},
    {"palette": "inferno", "mode": "custom", "lo": -500, "hi": 5}, {"palette": "inferno", "mode": "custom", "lo": 0, "hi": 1e9},
    {"palette": "inferno", "mode": "custom", "lo": "nan", "hi": 5}, {"palette": "inferno", "mode": "custom", "lo": [1], "hi": {"a": 2}},
])
def test_bad_requests_change_nothing(c, body):
    bid = new_batch(c)
    before = picture(c, bid, "p0_thermal.jpg")
    assert look(c, bid, **body).status_code == 400
    assert edit_state(c, bid)["look"]["palette"] == "inferno" and picture(c, bid, "p0_thermal.jpg") == before


def test_unknown_batch(c):
    assert look(c, "batch_20200101_000000_deadbeef", palette="ironbow").status_code == 404


def test_points_notes_and_temperatures_survive(c):
    bid = new_batch(c)
    state = edit_state(c, bid)
    spot = state["images"][0]["spots"][0]
    spot.update(type="Wall", spot_number=3, note="by the lintel")
    state.update(property_address="1 High Street", general_notes="Cold night")
    c.post(f"/save_labels/{bid}", json=state)
    look(c, bid, palette="ironbow", mode="custom", lo=-5, hi=40)
    after = edit_state(c, bid)
    kept = after["images"][0]["spots"][0]
    assert (kept["type"], kept["spot_number"], kept["note"], kept["temperature"]) == ("Wall", 3, "by the lintel", spot["temperature"])
    assert after["property_address"] == "1 High Street" and after["general_notes"] == "Cold night"
    assert c.get(f"/api/temperature/{bid}/p0.jpg?row=45&col=66").get_json()["temperature"] == 27.0


def test_existing_report_gets_the_new_scale_bars(c):
    bid = new_batch(c)
    state = edit_state(c, bid)
    state["images"][0]["spots"][0].update(type="Wall", spot_number=1)
    c.post(f"/save_labels/{bid}", json=state)
    c.post(f"/generate_heat_loss_report/{bid}", json={})
    assert "0.0&deg;C" not in c.get(f"/view_heat_loss_report/{bid}").get_data(as_text=True)
    look(c, bid, palette="ironbow", mode="custom", lo=0, hi=35)
    report = c.get(f"/view_heat_loss_report/{bid}").get_data(as_text=True)
    assert "0.0&deg;C" in report and "35.0&deg;C" in report
    look(c, bid, palette="camera")
    assert 'class="scale"' not in c.get(f"/view_heat_loss_report/{bid}").get_data(as_text=True)


def test_redetect_keeps_the_look(c):
    bid = new_batch(c)
    look(c, bid, palette="ironbow", mode="custom", lo=0, hi=35)
    c.post(f"/redetect/{bid}", json={"sensitivity": "high"})
    state = edit_state(c, bid)
    assert state["look"]["palette"] == "ironbow" and state["images"][0]["scale"]["hi"] == 35.0


def test_batches_made_before_this_feature_still_open(c, tmp_path):
    bid = new_batch(c)
    path = tmp_path / "s" / "reports" / "batches" / bid / "thermal_analysis.json"
    data = json.loads(path.read_text())
    del data["look"]
    path.write_text(json.dumps(data))
    assert edit_state(c, bid)["look"]["palette"] == "inferno"
    assert look(c, bid, palette="grey").status_code == 200


def test_pictures_are_never_served_stale(c):
    bid = new_batch(c)
    assert "no-cache" in c.get(f"/download/{bid}/p0_labeled.jpg").headers["Cache-Control"]


def test_page_offers_the_choices(c):
    page = c.get(f"/edit_spots/{new_batch(c)}").get_data(as_text=True)
    for text in ("Picture colours", "Ironbow", "As it came off the camera", "Same scale on every picture", "Set by hand"):
        assert text in page
