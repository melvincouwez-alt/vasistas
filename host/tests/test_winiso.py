"""ISO de Windows 11 : lien chez Microsoft et téléchargement, sans réseau (réponses simulées)."""

import io
import json
import threading
import urllib.error

import pytest

from vasistas import i18n, winiso


@pytest.fixture(autouse=True)
def francais(monkeypatch):
    # messages attendus en français, quelle que soit la langue du poste
    monkeypatch.setattr(i18n, "_lang", "fr")

ISO_URL = "https://software.download.prss.microsoft.com/dbazure/Win11_25H2_French_x64_v2.iso?t=x"
PAGE = ('<select><option value="" selected>Choisir</option>'
        '<option value="3321">Windows 11 (ISO édition multiple pour les appareils x64)</option></select>'
        '<input id="msg-01" type="hidden" value="Nous ne pouvons pas traiter votre demande. '
        '&lt;a href=&quot;x&quot;&gt;715-123130&lt;/a&gt;"/>')
MDT = 'var u="https://ov-df.microsoft.com/?x=1&w=0A1B2C";var rticks="+638123456789";'
SKUS = {"Skus": [{"Id": "20050", "Language": "French", "LocalizedLanguage": "Français"},
                 {"Id": "20046", "Language": "English", "LocalizedLanguage": "Anglais"}]}
LINKS = {"ProductDownloadOptions": [{"DownloadType": 1, "Uri": ISO_URL}]}


EVAL_ISO = ("https://software-static.download.prss.microsoft.com/dbazure/x/"
            "26200.6584.250915-1905.25h2_ge_release_svc_refresh_CLIENTENTERPRISEEVAL_OEMRET_x64FRE_fr-fr.iso")
EVAL_PAGE_HTML = (
    '<a data-bi-tags="{}" class="cta" data-target="https://go.microsoft.com/fwlink/?linkid=4000001&amp;'
    'clcid=0x409&amp;culture=en-us&amp;country=us" aria-label="64-bit edition: Download Windows 11 '
    'Enterprise ISO 64-bit (fr-FR)" href="x">'
    '<a class="cta" data-target="https://go.microsoft.com/fwlink/?linkid=4000002&amp;clcid=0x409" '
    'aria-label="Download Windows 11 Enterprise ISO LTSC 64-bit (en-Gb)" href="x">'
    '<a class="cta" data-target="https://go.microsoft.com/fwlink/?linkid=4000003&amp;clcid=0x409" '
    'aria-label="ARM64 edition - Download ISO – IoT Enterprise LTSC ARM64 edition" href="x">'
    '<a class="cta" data-target="https://go.microsoft.com/fwlink/?linkid=4000004&amp;clcid=0x409" '
    'aria-label="Download Windows 11 Enterprise VHD 64-bit (fr-FR)" href="x">')


class FakeSession:
    """Répond selon le début de l'adresse ; garde la trace des requêtes."""

    def __init__(self, links=LINKS, skus=SKUS):
        self.links, self.skus, self.calls = links, skus, []
        self.dead_links = set()   # linkid qui ne répondent plus (404)

    def resolve(self, url):
        self.calls.append(("RESOLVE", url, {}))
        link_id = int(url.split("linkid=")[1].split("&")[0])
        if link_id in self.dead_links:
            return url, 404, {}
        return EVAL_ISO, 200, {"Content-Length": "7102060544"}

    def request(self, url, headers=None, method="GET"):
        self.calls.append((method, url, headers or {}))
        if "evalcenter" in url:
            return 200, {}, EVAL_PAGE_HTML.encode()
        if "software-download/windows11" in url or "software-download/windows10ISO" in url:
            return 200, {}, PAGE.encode()
        if "mdt.js" in url:
            return 200, {}, MDT.encode()
        if "getskuinformationbyproductedition" in url:
            return 200, {}, json.dumps(self.skus).encode()
        if "GetProductDownloadLinksBySku" in url:
            return 200, {}, json.dumps(self.links).encode()
        if method == "HEAD":
            return 200, {"Content-Length": "8473616384"}, b""
        return 200, {}, b""


@pytest.fixture
def session(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(winiso, "_session_factory", lambda: fake)
    monkeypatch.setattr(winiso.time, "sleep", lambda s: None)
    monkeypatch.setattr(winiso, "EVAL_LINKS", {k: dict(v) for k, v in winiso.EVAL_LINKS.items()})
    return fake


def test_lien_officiel(session):
    url, name, size = winiso.get_link("French", loc="fr_FR.UTF-8")
    assert (url, name, size) == (ISO_URL, "Win11_25H2_French_x64_v2.iso", 8473616384)
    sku_call = next(c for c in session.calls if "getskuinformation" in c[1])
    assert "productEditionId=3321" in sku_call[1] and "Locale=fr-FR" in sku_call[1]
    # la protection ov-df reçoit w et rticks lus dans mdt.js
    assert any("w=0A1B2C" in c[1] and "rticks=638123456789" in c[1] for c in session.calls)
    links_call = next(c for c in session.calls if "GetProductDownloadLinksBySku" in c[1])
    assert "SKU=20050" in links_call[1] and links_call[2]["Referer"] == winiso.REFERER


@pytest.mark.parametrize("error_type", [8, 9])
def test_refus_sentinel(session, error_type):
    session.links = {"Errors": [{"Key": "ErrorSettings.SentinelReject",
                                 "Value": "Sentinel marked this request as rejected.", "Type": error_type}]}
    with pytest.raises(winiso.DownloadBlocked) as e:
        winiso.get_link("French", loc="fr_FR")
    assert "715-123130" in str(e.value) and "<" not in str(e.value) and "évaluation" in str(e.value)
    # un seul nouvel essai, avec une nouvelle session
    assert sum("GetProductDownloadLinksBySku" in c[1] for c in session.calls) == 2
    assert len({c[1].split("session_id=")[1] for c in session.calls if "vlscppe" in c[1]}) == 2


def test_windows10_par_sa_page(session):
    session.links = {"ProductDownloadOptions": [
        {"DownloadType": 0, "Uri": "https://x/Win10_22H2_French_x32v1.iso?t=1"},
        {"DownloadType": 1, "Uri": "https://x/Win10_22H2_French_x64v1.iso?t=1"}]}
    assert winiso.get_link("French", "win10", "x86", loc="fr_FR")[1] == "Win10_22H2_French_x32v1.iso"
    assert any("software-download/windows10ISO" in c[1] for c in session.calls)
    links_call = next(c for c in session.calls if "GetProductDownloadLinksBySku" in c[1])
    assert links_call[2]["Referer"].endswith("windows10ISO")


def test_architecture_absente():
    with pytest.raises(winiso.DownloadError, match="arm64"):
        winiso.get_link("French", "win11", "arm64", loc="fr_FR")


def test_evaluation_par_lien_fixe(session, monkeypatch):
    monkeypatch.setitem(winiso.EVAL_LINKS, ("enterprise", "x64"), {"French": 2334272})
    url, name, size = winiso.get_link(version="win11_eval", loc="fr_FR.UTF-8")
    assert url == EVAL_ISO and name.endswith("x64FRE_fr-fr.iso") and size == 7102060544
    assert session.calls[0][1].startswith("https://go.microsoft.com/fwlink/?linkid=2334272&")
    assert not any("vlscppe" in c[1] for c in session.calls)  # pas de session Microsoft


def test_evaluation_lien_mort_relu_dans_la_page(session, monkeypatch):
    monkeypatch.setitem(winiso.EVAL_LINKS, ("enterprise", "x64"), {"French": 111})
    session.dead_links = {111}
    assert winiso.get_link("French", "win11_eval")[0] == EVAL_ISO
    assert winiso.EVAL_LINKS[("enterprise", "x64")]["French"] == 4000001
    assert any("evalcenter/download-windows-11-enterprise" in c[1] for c in session.calls)


def test_page_evaluation():
    found = winiso._parse_eval_page(EVAL_PAGE_HTML)
    assert found == {("enterprise", "x64"): {"French": 4000001},
                     ("ltsc", "x64"): {"English International": 4000002},
                     ("iot", "arm64"): {"English": 4000003}}   # le VHD est ignoré


def test_langues_en_ligne(session, monkeypatch):
    monkeypatch.setitem(winiso.get_version("win10"), "languages", list(winiso.LANGUAGES))
    got = winiso.fetch_languages("win10", loc="fr_FR")
    assert got == [("French", "Français"), ("English", "Anglais")]
    assert winiso.languages("win10") == got


def test_versions_et_cles():
    keys = [v["key"] for v in winiso.VERSIONS]
    assert keys[0] == winiso.DEFAULT_VERSION == "win11" and len(set(keys)) == len(keys)
    for v in winiso.VERSIONS:
        assert v["label"] and v["description"] and v["note"] and v["arch"] and v["languages"]
        assert v["needs_key"] == (not v["eval"])
        assert set(v["editions"]) <= {k for k, _, _ in winiso.EDITIONS}
    assert winiso.get_version("win10")["end_of_support"] == "2025-10-14"
    assert not winiso.needs_key("win11_eval") and winiso.needs_key("win11")
    assert winiso.install_key("win11_eval", user_key="W269N-WFGWX-YVC9B-4J6C9-T83GX") is None
    assert winiso.install_key("win11") == winiso.GENERIC_KEYS["pro"]
    assert winiso.install_key("win11", "home") == winiso.GENERIC_KEYS["home"]
    assert winiso.install_key("win11", user_key="aaaaa bbbbb ccccc ddddd eeeee") == "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
    with pytest.raises(ValueError):
        winiso.install_key("win11", user_key="1234")
    with pytest.raises(ValueError):
        winiso.get_version("win12")


@pytest.mark.parametrize("edition, licensed, version", [
    ("pro", False, "win11"), ("home", False, "win11"), ("enterprise", False, "win11_eval"),
    ("enterprise", True, "win11"), ("enterprise_ltsc", False, "ltsc_eval"),
    ("iot_enterprise_ltsc", False, "iot_ltsc_eval"),
])
def test_version_selon_l_edition(edition, licensed, version):
    assert winiso.version_for_edition(edition, licensed) == version


@pytest.mark.parametrize("version, loc, lang", [
    ("win11_eval", "fr_FR", "French"), ("win11_eval", "fr_CA", "French"),
    ("win11_eval", "nl_NL", "English"), ("win11_eval", "en_US", "English"),
    ("iot_ltsc_eval", "fr_FR", "English"), ("win11", "fr_CA", "French Canadian"),
    ("ltsc_eval", "pt_PT", "Brazilian Portuguese"), ("ltsc_eval", "es_MX", "Spanish"),
])
def test_langue_par_defaut_selon_la_version(version, loc, lang):
    assert winiso.default_language(version, loc) == lang


def test_langue_absente(session):
    with pytest.raises(winiso.DownloadError, match="Klingon"):
        winiso.get_link("Klingon", loc="fr_FR")


def test_liste_de_langues_vide(session):
    session.skus = {"Skus": []}
    with pytest.raises(winiso.DownloadError, match="langues"):
        winiso.get_link("French", loc="fr_FR")


@pytest.mark.parametrize("loc, lang", [
    ("fr_FR.UTF-8", "French"), ("fr_CA", "French Canadian"), ("en_US.UTF-8", "English"),
    ("en_GB", "English International"), ("de_DE", "German"), ("es_ES", "Spanish"),
    ("es_MX", "Spanish (Mexico)"), ("pt_BR", "Brazilian Portuguese"), ("pt_PT", "Portuguese"),
    ("zh_TW", "Chinese (Traditional)"), ("zh_CN", "Chinese (Simplified)"), ("nb_NO", "Norwegian"),
    ("C", "English"), ("xx_YY", "English"),
])
def test_langue_selon_la_locale(loc, lang):
    assert winiso.language_for_locale(loc) == lang
    assert lang in dict(winiso.languages("win11"))


def test_adresses_selon_la_locale():
    assert winiso.official_page("fr_FR.UTF-8") == "https://www.microsoft.com/fr-fr/software-download/windows11"
    assert winiso.buy_url("home", "de_DE").startswith("https://www.microsoft.com/de-de/d/windows-11-home/")
    assert winiso.web_locale("C") == "en-us"


@pytest.mark.parametrize("text, key", [
    ("w269n wfgwx yvc9b 4j6c9 t83gx", "W269N-WFGWX-YVC9B-4J6C9-T83GX"),
    ("W269NWFGWXYVC9B4J6C9T83GX", "W269N-WFGWX-YVC9B-4J6C9-T83GX"),
    ("W269N-WFGWX-YVC9B-4J6C9", None), ("", None), (None, None),
])
def test_cle_de_produit(text, key):
    assert winiso.normalize_key(text) == key


def test_cles_generiques_valides():
    assert set(winiso.GENERIC_KEYS) == set(winiso.CONSUMER_EDITIONS)
    assert all(winiso.KEY_RE.match(k) for k in winiso.GENERIC_KEYS.values())


# -- téléchargement --

class FakeResponse(io.BytesIO):
    def __init__(self, data, status=200, length=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def _serve(data, honor_range=True, calls=None):
    def urlopen(req, timeout=None):
        rng = req.headers.get("Range")
        if calls is not None:
            calls.append(rng)
        if rng and honor_range:
            start = int(rng.split("=")[1].rstrip("-"))
            return FakeResponse(data[start:], status=206)
        return FakeResponse(data)
    return urlopen


def test_telechargement_et_progression(tmp_path, monkeypatch):
    data = bytes(range(256)) * 40
    monkeypatch.setattr(winiso, "_urlopen", _serve(data))
    seen = []
    out = winiso.download("https://x/a.iso", tmp_path / "a.iso", lambda d, t: seen.append((d, t)), chunk=1000)
    assert out.read_bytes() == data and not (tmp_path / "a.iso.part").exists()
    assert seen[-1] == (len(data), len(data)) and len(seen) == 11


def test_reprise_apres_interruption(tmp_path, monkeypatch):
    data = bytes(range(256)) * 40
    calls = []
    monkeypatch.setattr(winiso, "_urlopen", _serve(data, calls=calls))
    stop = threading.Event()

    def prog(done, total):
        if done >= 3000:
            stop.set()
    with pytest.raises(winiso.DownloadCancelled):
        winiso.download("https://x/a.iso", tmp_path / "a.iso", prog, stop, chunk=1000)
    assert (tmp_path / "a.iso.part").stat().st_size == 3000
    out = winiso.download("https://x/a.iso", tmp_path / "a.iso", chunk=1000, expected_size=len(data))
    assert out.read_bytes() == data and calls == [None, "bytes=3000-"]


def test_serveur_sans_range_repart_de_zero(tmp_path, monkeypatch):
    data = b"z" * 5000
    (tmp_path / "a.iso.part").write_bytes(b"z" * 2000)
    monkeypatch.setattr(winiso, "_urlopen", _serve(data, honor_range=False))
    assert winiso.download("https://x/a.iso", tmp_path / "a.iso", chunk=700).read_bytes() == data


def test_fichier_incomplet(tmp_path, monkeypatch):
    monkeypatch.setattr(winiso, "_urlopen", lambda req, timeout=None: FakeResponse(b"x" * 100, length=500))
    with pytest.raises(winiso.DownloadError, match="incomplet"):
        winiso.download("https://x/a.iso", tmp_path / "a.iso")
    assert (tmp_path / "a.iso.part").exists() and not (tmp_path / "a.iso").exists()


def test_lien_expire(tmp_path, monkeypatch):
    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(winiso, "_urlopen", refuse)
    with pytest.raises(winiso.DownloadError, match="24 h"):
        winiso.download("https://x/a.iso", tmp_path / "a.iso")
