"""Groups: configuration, isolation between groups, sign-in token checks, activity log."""
import io
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from audit import AuditLog
from auth import CloudflareAccess, DevIdentity, build_identity
from orgs import OrgConfigError, OrgDirectory
from storage import LocalStorage, PrefixedStorage, StorageError
from tests.conftest import FakeProcessor, synthetic_jpeg

ORGS = {
    "admins": ["Boss@Example.org"],
    "organisations": {
        "chesham": {"name": "Sustainable Chesham Energy Group", "colour": "#1f3a5f", "sensitivity": "low",
                    "members": ["alice@example.org", "Shared@Example.org"]},
        "amersham": {"name": "Amersham Energy Group", "colour": "#f0e68c", "palette": "cividis",
                     "members": ["carol@example.org", "shared@example.org"]},
    },
}


@pytest.fixture
def orgs_file(tmp_path):
    d = tmp_path / "orgs"
    (d / "amersham").mkdir(parents=True)
    (d / "amersham" / "report_content.json").write_text(json.dumps({"about": ["The Amersham introduction."]}))
    (d / "orgs.json").write_text(json.dumps(ORGS))
    return str(d / "orgs.json")


def make_app(tmp_path, orgs_file, **kw):
    from app import create_app
    from settings import Settings
    settings = Settings(orgs_file=orgs_file, secret_key="test-secret", **kw)
    app = create_app(storage=LocalStorage(str(tmp_path / "store")), settings=settings,
                     processor_factory=FakeProcessor,
                     identity=lambda req: (req.headers.get("X-Test-User") or "").lower() or None)
    app.config["TESTING"] = True
    return app


def as_user(app, email):
    c = app.test_client()
    c.environ_base["HTTP_X_TEST_USER"] = email
    return c


def upload(client, name="one.jpg"):
    r = client.post("/upload", data={"files[]": [(io.BytesIO(synthetic_jpeg()), name)]},
                    content_type="multipart/form-data")
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["batch_id"]


# ---------------------------------------------------------------- configuration
def test_directory_loads_and_matches_emails_ignoring_case(orgs_file):
    d = OrgDirectory.load(orgs_file)
    assert [o.slug for o in d.for_user("ALICE@example.org")] == ["chesham"]
    assert [o.slug for o in d.for_user("shared@example.org")] == ["chesham", "amersham"]      # a shared operator
    assert [o.slug for o in d.for_user("boss@example.org")] == ["chesham", "amersham"]        # admins see every group
    assert d.for_user("stranger@example.org") == [] and d.for_user("") == []
    assert d.is_admin("BOSS@example.org") and not d.is_admin("alice@example.org")
    assert d.orgs["amersham"].content_file.endswith("amersham/report_content.json")
    assert d.orgs["chesham"].content_file is None
    assert d.orgs["amersham"].text_colour == "#000000" and d.orgs["chesham"].text_colour == "#ffffff"


@pytest.mark.parametrize("change, message", [
    ({"organisations": {}}, "at least one group"),
    ({"organisations": {"Bad Slug": {"name": "x"}}}, "Group id"),
    ({"organisations": {"a": {"name": ""}}}, "needs a 'name'"),
    ({"organisations": {"a": {"name": "A", "colour": "red"}}}, "colour"),
    ({"organisations": {"a": {"name": "A", "sensitivity": "loud"}}}, "sensitivity"),
    ({"organisations": {"a": {"name": "A", "palette": "rainbow"}}}, "palette"),
    ({"organisations": {"a": {"name": "A", "members": ["not-an-email"]}}}, "not an email"),
    ({"admins": ["nope"], "organisations": {"a": {"name": "A"}}}, "not an email"),
])
def test_mistakes_in_the_groups_file_are_explained(tmp_path, change, message):
    f = tmp_path / "orgs.json"
    f.write_text(json.dumps(change))
    with pytest.raises(OrgConfigError, match=message):
        OrgDirectory.load(str(f))


def test_missing_or_broken_file(tmp_path):
    with pytest.raises(OrgConfigError, match="not found"):
        OrgDirectory.load(str(tmp_path / "nope.json"))
    (tmp_path / "bad.json").write_text("{oops")
    with pytest.raises(OrgConfigError, match="not valid JSON"):
        OrgDirectory.load(str(tmp_path / "bad.json"))


# ---------------------------------------------------------------- storage separation
def test_prefixed_storage_keeps_groups_apart(tmp_path):
    base = LocalStorage(str(tmp_path))
    a, b = PrefixedStorage(base, "chesham"), PrefixedStorage(base, "amersham")
    a.write_text("reports/x.txt", "chesham data")
    assert not b.exists("reports/x.txt") and base.exists("chesham/reports/x.txt")
    for evil in ("../amersham/x", "/etc/passwd", "reports/../../amersham"):
        with pytest.raises(StorageError):
            a.read_bytes(evil)
    with pytest.raises(StorageError):
        a.delete("")                                            # never wipe a whole group by accident
    assert a.exists("reports/x.txt")


# ---------------------------------------------------------------- the app with groups
def test_no_token_and_unknown_people_get_nothing(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    assert app.test_client().get("/").status_code == 401
    r = as_user(app, "stranger@example.org").get("/")
    assert r.status_code == 403
    page = r.get_data(as_text=True)
    assert "Amersham" not in page and "Chesham" not in page         # does not reveal which groups exist
    assert app.test_client().get("/healthz").get_json() == {"status": "ok"}      # health check stays minimal


def test_each_group_only_sees_its_own_surveys(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    alice, carol = as_user(app, "alice@example.org"), as_user(app, "carol@example.org")
    batch = upload(alice)
    store = app.extensions["storage"]
    assert store.exists(f"chesham/images/batches/{batch}") and not store.exists("images")      # stored in her group's folder
    assert batch in alice.get("/").get_data(as_text=True)
    assert batch not in carol.get("/").get_data(as_text=True)
    for url in (f"/report/{batch}", f"/edit_spots/{batch}", f"/api/report/{batch}",
                f"/download/{batch}/one_labeled.jpg", f"/batch_image/{batch}/one.jpg",
                f"/view_heat_loss_report/{batch}"):
        assert carol.get(url).status_code == 404, url
    assert carol.post(f"/delete/{batch}").status_code == 404
    assert carol.post(f"/save_labels/{batch}", json={}).status_code == 404
    assert alice.get(f"/report/{batch}").status_code == 200


def test_banner_names_the_group_and_each_group_has_its_own_settings(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file, sensitivity="high")
    alice_page = as_user(app, "alice@example.org").get("/").get_data(as_text=True)
    assert "Working for: <strong>Sustainable Chesham Energy Group</strong>" in alice_page
    assert 'background: #1f3a5f' in alice_page and "Switch group" not in alice_page     # one group: no dropdown
    assert '<option value="low" selected>' in alice_page                                 # chesham's own sensitivity
    carol_page = as_user(app, "carol@example.org").get("/").get_data(as_text=True)
    assert "Amersham Energy Group" in carol_page and "Sustainable Chesham" not in carol_page
    assert '<option value="high" selected>' in carol_page                                # falls back to the global default
    assert app.extensions["context_for"]("amersham").service.palette == "cividis"


def test_shared_operator_chooses_the_group_and_the_work_lands_there(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    shared = as_user(app, "shared@example.org")
    r = shared.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/choose_group")
    page = shared.get("/choose_group").get_data(as_text=True)
    assert "Sustainable Chesham Energy Group" in page and "Amersham Energy Group" in page
    assert shared.post("/switch_group", data={"org": "amersham"}).status_code == 302
    assert "Working for: <strong>Amersham Energy Group</strong>" in shared.get("/").get_data(as_text=True)
    batch = upload(shared)
    assert app.extensions["storage"].exists(f"amersham/images/batches/{batch}")
    # switching to the other group: that job is not there, and the banner changes
    shared.post("/switch_group", data={"org": "chesham"})
    assert shared.get(f"/report/{batch}").status_code == 404
    assert "Working for: <strong>Sustainable Chesham" in shared.get("/").get_data(as_text=True)
    assert "Switch group" in shared.get("/").get_data(as_text=True)


def test_cannot_switch_into_a_group_you_do_not_belong_to(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    alice = as_user(app, "alice@example.org")
    assert alice.post("/switch_group", data={"org": "amersham"}).status_code == 403
    assert alice.post("/switch_group", data={"org": "../x"}).status_code == 403
    assert "Chesham" in alice.get("/").get_data(as_text=True)               # still in her own group


def test_a_forged_group_in_the_cookie_is_ignored(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    alice = as_user(app, "alice@example.org")
    with alice.session_transaction() as s:
        s["org"] = "amersham"                                               # as if she had edited her cookie
    page = alice.get("/").get_data(as_text=True)
    assert "Working for: <strong>Sustainable Chesham" in page and "Amersham" not in page


def test_report_carries_the_groups_name_and_own_wording_and_who_made_it(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    carol = as_user(app, "carol@example.org")
    batch = upload(carol)
    state = carol.get(f"/edit_spots/{batch}").get_data(as_text=True)
    svc = app.extensions["context_for"]("amersham").service
    es = svc.build_edit_state(batch)
    es["images"][0]["spots"][0].update(type="Window", spot_number=1)
    assert carol.post(f"/save_labels/{batch}", json={"images": es["images"]}).get_json()["success"]
    gen = carol.post(f"/generate_heat_loss_report/{batch}", json={}).get_json()
    page = carol.get(gen["report_url"]).get_data(as_text=True)
    assert "Amersham Energy Group" in page and "The Amersham introduction." in page
    assert "Sustainable Chesham" not in page
    assert "Condensing boiler" in page or "condensing boiler" in page.lower()   # untouched parts keep the standard wording
    saved = svc.get_report(batch)
    assert saved["generated_by"] == "carol@example.org" and saved["group"] == "amersham"


def test_activity_log_records_who_did_what_and_only_admins_can_read_it(tmp_path, orgs_file):
    app = make_app(tmp_path, orgs_file)
    alice, boss = as_user(app, "alice@example.org"), as_user(app, "boss@example.org")
    batch = upload(alice)
    alice.post(f"/delete/{batch}")
    assert alice.get("/activity").status_code == 403
    boss.post("/switch_group", data={"org": "chesham"})
    page = boss.get("/activity").get_data(as_text=True)
    assert "alice@example.org" in page and "upload" in page and "delete_batch" in page and batch in page
    boss.post("/switch_group", data={"org": "amersham"})
    assert "alice@example.org" not in boss.get("/activity").get_data(as_text=True)    # that was Chesham's activity


def test_audit_never_breaks_the_request(tmp_path):
    class Broken(LocalStorage):
        def write_bytes(self, path, data):
            raise StorageError("disk full")
    log = AuditLog(Broken(str(tmp_path)), "x")
    log.record("a@b.org", "upload")                                         # must not raise
    assert log.recent() == []


# ---------------------------------------------------------------- the sign-in token
@pytest.fixture(scope="module")
def keys():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048), rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeRequest:
    def __init__(self, token=None, cookie=None):
        self.headers = {"Cf-Access-Jwt-Assertion": token} if token else {}
        self.cookies = {"CF_Authorization": cookie} if cookie else {}


def token(key, **over):
    now = int(time.time())
    claims = {"aud": ["AUD123"], "iss": "https://team.cloudflareaccess.com", "email": "Alice@Example.org",
              "iat": now, "exp": now + 600, **over}
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def test_a_valid_cloudflare_token_gives_the_email(keys):
    good, _ = keys
    access = CloudflareAccess("team.cloudflareaccess.com", "AUD123", key_fetcher=lambda t: good.public_key())
    assert access(FakeRequest(token(good))) == "alice@example.org"
    assert access(FakeRequest(cookie=token(good))) == "alice@example.org"       # Cloudflare also sets it as a cookie


def test_forged_expired_or_misdirected_tokens_are_refused(keys):
    good, other = keys
    access = CloudflareAccess("https://team.cloudflareaccess.com/", "AUD123", key_fetcher=lambda t: good.public_key())
    assert access(FakeRequest()) is None
    assert access(FakeRequest(token(other))) is None                            # signed with someone else's key
    assert access(FakeRequest(token(good, aud=["SOMETHING-ELSE"]))) is None     # meant for a different application
    assert access(FakeRequest(token(good, iss="https://evil.example"))) is None
    assert access(FakeRequest(token(good, exp=int(time.time()) - 5))) is None   # expired
    assert access(FakeRequest(token(good, email=None))) is None                 # no email (e.g. a service token)
    assert access(FakeRequest("not.a.token")) is None
    from cryptography.hazmat.primitives import serialization
    pub_pem = good.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    import base64, hashlib, hmac
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"aud": ["AUD123"], "iss": "https://team.cloudflareaccess.com", "email": "x@y.org",
                           "iat": int(time.time()), "exp": int(time.time()) + 60}).encode())
    sig = b64(hmac.new(pub_pem, head + b"." + body, hashlib.sha256).digest())      # built by hand: PyJWT refuses to make it
    hs = (head + b"." + body + b"." + sig).decode()
    assert access(FakeRequest(hs)) is None                                      # 'sign with the public key' trick


def test_start_up_refuses_unsafe_configuration(tmp_path, orgs_file):
    from app import create_app
    from settings import Settings
    st = LocalStorage(str(tmp_path / "s"))
    with pytest.raises(ValueError, match="AUTH_MODE"):
        create_app(storage=st, settings=Settings(orgs_file=orgs_file, secret_key="x"))
    with pytest.raises(ValueError, match="CF_ACCESS"):
        create_app(storage=st, settings=Settings(orgs_file=orgs_file, secret_key="x", auth_mode="cloudflare"))
    with pytest.raises(ValueError, match="SECRET_KEY"):
        create_app(storage=st, settings=Settings(orgs_file=orgs_file, auth_mode="cloudflare",
                                                 cf_team_domain="t.cloudflareaccess.com", cf_aud="a"))
    with pytest.raises(OrgConfigError):
        create_app(storage=st, settings=Settings(orgs_file=str(tmp_path / "missing.json"), auth_mode="dev",
                                                 dev_user_email="a@b.org"))
    app = create_app(storage=st, settings=Settings(orgs_file=orgs_file, auth_mode="dev", dev_user_email="boss@example.org"))
    assert app.test_client().get("/choose_group").status_code == 200            # dev mode works without a real token


def test_build_identity_modes():
    assert build_identity("dev", dev_email="A@B.org")(None) == "a@b.org"
    with pytest.raises(ValueError):
        build_identity("")
    with pytest.raises(ValueError):
        DevIdentity("")


def test_without_a_groups_file_nothing_changes(client):
    assert client.get("/").status_code == 200 and client.get("/activity").status_code == 403
    assert "Working for" not in client.get("/").get_data(as_text=True)


def test_migration_moves_existing_surveys_into_the_group_folder(tmp_path):
    from migrate_to_org import migrate
    (tmp_path / "images" / "batches" / "b1").mkdir(parents=True)
    (tmp_path / "images" / "batches" / "b1" / "a.jpg").write_bytes(b"x")
    (tmp_path / "reports" / "batches" / "b1").mkdir(parents=True)
    assert len(migrate(str(tmp_path), "chesham", dry_run=True)) == 2
    assert (tmp_path / "images").exists()                                        # dry run changed nothing
    migrate(str(tmp_path), "chesham")
    assert (tmp_path / "chesham" / "images" / "batches" / "b1" / "a.jpg").read_bytes() == b"x"
    assert not (tmp_path / "images").exists() and not (tmp_path / "reports").exists()
    with pytest.raises(SystemExit):
        migrate(str(tmp_path), "Bad Name")
