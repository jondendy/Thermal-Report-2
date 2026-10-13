from heat_loss_reporter import HeatLossReporter


def spot(image, typ, num, temp, sev, note="", loc=(10, 10)):
    return {"spot_id": f"{image}_{typ}{num}", "image_name": image, "type": typ, "spot_number": num,
            "temperature": temp, "severity": sev, "note": note, "location": list(loc), "source": "auto"}


def test_metadata_key_is_not_a_spot_type():
    r = HeatLossReporter()
    assert "_metadata" not in r.spot_types and "Wall" in r.spot_types


def test_same_number_different_type_are_separate_findings():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "Wall", 1, 20.0, "high"), spot("a.jpg", "Window", 1, 18.0, "low")]}
    rep = r.build_report("b1", labels)
    assert [f["title"] for f in rep["findings"]] == ["Heat loss at Wall #1", "Heat loss at Window #1"]


def test_same_feature_in_two_images_is_one_finding_with_both_images():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "Wall", 2, 20.0, "medium"), spot("b.jpg", "Wall", 2, 24.0, "critical")]}
    f = r.build_report("b1", labels)["findings"][0]
    assert f["image_count"] == 2 and f["severity"] == "critical"
    assert round(f["max_temp"], 1) == 24.0 and round(f["min_temp"], 1) == 20.0
    assert [i["labeled_image"] for i in f["images"]] == ["a_labeled.jpg", "b_labeled.jpg"]


def test_unlabelled_points_are_left_out():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "", None, 20.0, "high"), spot("a.jpg", "Door", 3, 19.0, "low")]}
    assert len(r.build_report("b1", labels)["findings"]) == 1


def test_auto_comment_and_surveyor_notes_are_kept_apart():
    r = HeatLossReporter()
    labels = {
        "general_notes": "  Cold clear night, 2C outside.  ",
        "image_notes": {"a.jpg": "Taken from the drive", "b.jpg": "   "},
        "spots": [spot("a.jpg", "Eaves", 1, 21.5, "high", note="Missing insulation behind fascia")],
    }
    rep = r.build_report("b1", labels, property_address="1 High St", inspector_name="J Dendy")
    f = rep["findings"][0]
    assert "Heat loss detected at eaves location" in f["description"]          # auto text
    assert f["surveyor_notes"] == [{"image_name": "a.jpg", "note": "Missing insulation behind fascia"}]
    assert rep["general_notes"] == "Cold clear night, 2C outside."
    assert [n["image_name"] for n in rep["image_notes"]] == ["a.jpg"]          # blank note dropped
    assert f["images"][0]["image_note"] == "Taken from the drive"
    assert rep["property_address"] == "1 High St" and rep["inspector_name"] == "J Dendy"


def test_summary_and_recommendations():
    r = HeatLossReporter()
    labels = {"spots": [spot("a.jpg", "Window", 1, 20, "critical"), spot("a.jpg", "Wall", 2, 15, "low")]}
    rep = r.build_report("b1", labels)
    assert rep["summary"]["total_findings"] == 2 and rep["summary"]["highest_severity"] == "Critical"
    assert rep["recommendations"] and rep["recommendations"][0]["priority"] == "high"
