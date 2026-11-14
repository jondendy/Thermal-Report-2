"""The Log out button: forgets the chosen group, then ends the Cloudflare login."""
from app import create_app
from settings import Settings
from storage import LocalStorage
from tests.conftest import FakeProcessor
from tests.test_orgs import as_user, make_app, orgs_file      # noqa: F401  (orgs_file is a fixture)

CF = {"Cf-Access-Jwt-Assertion": "token-from-cloudflare"}       # what a request through Cloudflare carries


def plain(tmp_path, **kw):
    app = create_app(storage=LocalStorage(str(tmp_path / "s")), settings=Settings(**kw), processor_factory=FakeProcessor)
    app.config["TESTING"] = True
    return app.test_client()


# ------------------------------------------------------------------ one group (how the live site runs today)
def test_button_only_when_behind_the_login(tmp_path):
    c = plain(tmp_path)
    assert "Log out" not in c.get("/").get_data(as_text=True)             # local trial: nothing to log out of
    assert "Log out" in c.get("/", headers=CF).get_data(as_text=True)
    c.set_cookie("CF_Authorization", "token")
    assert "Log out" in c.get("/").get_data(as_text=True)


def test_logout_goes_to_cloudflare(tmp_path):
    c = plain(tmp_path)
    r = c.post("/logout", headers=CF)
    assert r.status_code == 302 and r.headers["Location"] == "/cdn-cgi/access/logout"
    r = c.post("/logout")                                                   # not behind the login: back to the start
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert c.get("/logout", headers=CF).status_code == 405                  # a link or picture cannot log anyone out


def test_not_on_the_printed_report(tmp_path):
    import io
    from tests.conftest import synthetic_jpeg
    from tests.test_app_flow import edit_state
    c = plain(tmp_path)
    bid = c.post("/upload", data={"files[]": [(io.BytesIO(synthetic_jpeg()), "a.jpg")]},
                 content_type="multipart/form-data").get_json()["batch_id"]
    state = edit_state(c, bid)
    state["images"][0]["spots"][0].update(type="Wall", spot_number=1)
    c.post(f"/save_labels/{bid}", json=state)
    c.post(f"/generate_heat_loss_report/{bid}", json={})
    assert "Log out" in c.get(f"/edit_spots/{bid}", headers=CF).get_data(as_text=True)
    assert "Log out" not in c.get(f"/view_heat_loss_report/{bid}", headers=CF).get_data(as_text=True)


# ------------------------------------------------------------------ several groups
def test_logout_forgets_the_group_and_is_recorded(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    c = as_user(app, "shared@example.org")
    c.post("/switch_group", data={"org": "amersham"})
    page = c.get("/", headers=CF).get_data(as_text=True)
    assert "Amersham Energy Group" in page and "Log out" in page
    r = c.post("/logout", headers=CF)
    assert r.headers["Location"] == "/cdn-cgi/access/logout"
    assert c.get("/").status_code == 302                                    # must choose a group again
    boss = as_user(app, "boss@example.org")
    boss.post("/switch_group", data={"org": "amersham"})
    log = boss.get("/activity").get_data(as_text=True)
    assert "logout" in log and "shared@example.org" in log


def test_logout_offered_when_not_set_up_or_not_yet_chosen(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    stranger = as_user(app, "nobody@example.org")
    r = stranger.get("/", headers=CF)
    assert r.status_code == 403 and "Log out" in r.get_data(as_text=True)   # can sign in again with another address
    assert stranger.post("/logout", headers=CF).headers["Location"] == "/cdn-cgi/access/logout"
    chooser = as_user(app, "shared@example.org")
    assert "Log out" in chooser.get("/choose_group", headers=CF).get_data(as_text=True)
    assert chooser.post("/logout", headers=CF).status_code == 302


def test_logout_needs_a_login(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    assert app.test_client().post("/logout").status_code == 401
