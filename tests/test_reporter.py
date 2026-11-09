import json

from heat_loss_reporter import HeatLossReporter


def spot(image, typ, num, temp, sev, note="", loc=(10, 10), **extra):
    return {"spot_id": f"{image}_{typ}{num}", "image_name": image, "type": typ, "spot_number": num,
            "temperature": temp, "severity": sev, "note": note, "location": list(loc), "source": "auto", **extra}


def test_metadata_key_is_not_a_spot_type():
    r = HeatLossReporter()
    assert "_metadata" not in r.spot_types and "Wall" in r.spot_types


def test_points_are_catalogued_under_their_picture_with_short_ids():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "Wall", 1, 20.0, "high"), spot("a.jpg", "Window", 1, 18.0, "low"),
                        spot("b.jpg", "Door", 2, 17.0, "medium")]}
    rep = r.build_report("b1", labels)
    assert [i["image_name"] for i in rep["images"]] == ["a.jpg", "b.jpg"]
    assert [p["id"] for p in rep["images"][0]["points"]] == ["Wa1", "Wi1"]      # same number, different type
    assert [p["id"] for p in rep["images"][1]["points"]] == ["D2"]
    assert rep["images"][0]["labeled_image"] == "a_labeled.jpg"


def test_same_feature_in_two_pictures_appears_in_both_with_the_same_id():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "Wall", 2, 20.0, "medium"), spot("b.jpg", "Wall", 2, 24.0, "very_high")]}
    rep = r.build_report("b1", labels)
    assert [i["points"][0]["id"] for i in rep["images"]] == ["Wa2", "Wa2"]


def test_unlabelled_points_are_left_out():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "", None, 20.0, "high"), spot("a.jpg", "Door", 3, 19.0, "low")]}
    rep = r.build_report("b1", labels)
    assert rep["summary"]["total_points"] == 1


def test_generated_comment_edited_comment_extras_and_notes_are_kept_apart():
    r = HeatLossReporter()
    labels = {
        "general_notes": "  Cold clear night, 2C outside.  ",
        "image_notes": {"a.jpg": "Taken from the drive", "b.jpg": "   "},
        "spots": [
            spot("a.jpg", "Eaves", 1, 21.5, "high", note="Missing insulation behind fascia", excess=7.4,
                 extras=["Improve loft insulation", "  "]),
            spot("a.jpg", "Window", 1, 15.0, "low", comment="  Old single glazing.  "),
        ],
    }
    rep = r.build_report("b1", labels, property_address="1 High St", inspector_name="J Dendy",
                         images_meta=[{"image_name": "a.jpg", "scale": {"lo": 3.0, "hi": 22.0, "css": "x"}},
                                      {"image_name": "b.jpg"}])
    eaves, window = rep["images"][0]["points"]                                  # sorted by number then type
    assert "Heat loss detected at eaves location: 21.5°C, 7.4°C warmer than the surrounding wall" in eaves["comment"]
    assert eaves["extras"] == ["Improve loft insulation"]                      # blank extra dropped
    assert eaves["note"] == "Missing insulation behind fascia"
    assert window["comment"] == "Old single glazing."                           # the surveyor's wording wins
    assert rep["general_notes"] == "Cold clear night, 2C outside."
    assert rep["images"][0]["image_note"] == "Taken from the drive" and rep["images"][1]["image_note"] == ""
    assert rep["images"][1]["points"] == [] and rep["images"][0]["scale"]["lo"] == 3.0
    assert rep["property_address"] == "1 High St" and rep["inspector_name"] == "J Dendy"


def test_summary_counts_and_the_word_critical_never_appears():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "Window", 1, 20, "critical"),      # saved by an older version
                        spot("a.jpg", "Wall", 2, 15, "low")]}
    rep = r.build_report("b1", labels, org_name="Chesham Energy Group")
    assert rep["summary"]["total_points"] == 2 and rep["summary"]["highest_severity"] == "Very high"
    assert {b["key"]: b["count"] for b in rep["summary"]["breakdown"]} == {"very_high": 1, "high": 0, "medium": 0, "low": 1}
    assert "critical" not in json.dumps(rep).lower()
    assert "very high heat loss point" in rep["images"][0]["points"][0]["comment"]


def test_options_offer_advice_for_the_type_and_the_typical_saving():
    r = HeatLossReporter()
    opts = r.options_for("Window")
    assert any("weather stripping" in o.lower() for o in opts) and opts[-1].startswith("Typical saving: 10-20%")
    assert r.options_for("Nonsense") == [] and set(r.options_by_type()) == set(r.spot_types)


def test_fixed_wording_uses_the_organisation_name_and_only_https_links():
    r = HeatLossReporter()
    c = r.content("Chesham Energy Group")
    assert "Chesham Energy Group" in c["about"][0] and "{org}" not in json.dumps(c)
    assert "your local energy group" in r.content("")["about"][0]
    links = [l["url"] for m in c["general_measures"] for l in m.get("links", [])]
    assert links and all(u.startswith("https://") for u in links)
    assert any("condensing" in m["title"].lower() for m in c["general_measures"])


def test_pictures_are_grouped_four_to_a_sheet():
    r = HeatLossReporter()
    labels = {"spots": [spot(f"{i}.jpg", "Wall", i, 20.0, "low") for i in range(1, 7)]}
    rep = r.build_report("b1", labels)
    assert rep["images_per_sheet"] == 4 and len(rep["images"]) == 6


def test_a_group_can_add_its_own_closing_page_entries(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"extra_measures": [{"title": "Local help", "text": "x",
                                                 "links": [{"label": "bad", "url": "http://insecure.example"},
                                                           {"label": "ok", "url": "https://ok.example/"}]}]}))
    r = HeatLossReporter(content_file=str(f))
    titles = [m["title"] for m in r.content("G")["general_measures"]]
    assert titles[-1] == "Local help" and "condensing" in titles[0].lower()          # standard entries first, then the group's
    assert [l["label"] for l in r.content("G")["general_measures"][-1]["links"]] == ["ok"]    # only https links survive
    assert "extra_measures" not in r.content("G")
    assert "Local help" not in [m["title"] for m in HeatLossReporter().content("G")["general_measures"]]
