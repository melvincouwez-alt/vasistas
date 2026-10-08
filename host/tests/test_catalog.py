"""Catalogue d'installation : configuration ODT, scripts winget, lecture des résultats."""

import xml.etree.ElementTree as ET

import pytest

from vasistas import i18n, catalog


@pytest.fixture(autouse=True)
def francais(monkeypatch):
    # messages attendus en français, quelle que soit la langue du poste
    monkeypatch.setattr(i18n, "_lang", "fr")


def _parse(xml):
    return ET.fromstring(xml)


def test_configuration_office_par_defaut():
    root = _parse(catalog.office_config("business"))
    add = root.find("Add")
    assert add.get("OfficeClientEdition") == "64" and add.get("Channel") == "Current"
    product = add.find("Product")
    assert product.get("ID") == "O365BusinessRetail"
    assert product.find("Language").attrib == {"ID": "MatchOS", "Fallback": "en-us"}
    assert {e.get("ID") for e in product.findall("ExcludeApp")} == set(catalog.DEFAULT_EXCLUDED)
    props = {p.get("Name"): p.get("Value") for p in root.findall("Property")}
    assert props["AUTOACTIVATE"] == "0" and props["FORCEAPPSHUTDOWN"] == "FALSE"
    assert props["SharedComputerLicensing"] == "0"
    assert root.find("Display").attrib == {"Level": "None", "AcceptEULA": "TRUE"}
    assert root.find("Updates").get("Enabled") == "TRUE"


def test_exclusions_langues_et_ordinateur_partage():
    root = _parse(catalog.office_config("enterprise", "MonthlyEnterprise", ["fr-fr", "en-us"],
                                        exclude=["Access", "Teams"], shared=True))
    product = root.find("Add/Product")
    assert [l.get("ID") for l in product.findall("Language")] == ["fr-fr", "en-us"]
    assert "Fallback" not in product.find("Language").attrib
    assert [e.get("ID") for e in product.findall("ExcludeApp")] == ["Access", "Teams"]
    assert {p.get("Name"): p.get("Value") for p in root.findall("Property")}["SharedComputerLicensing"] == "1"


@pytest.mark.parametrize("product,visio,project", [
    ("business", "VisioProRetail", "ProjectProRetail"),
    ("homebusiness2024", "VisioPro2024Retail", "ProjectPro2024Retail"),
    ("ltsc2024", "VisioPro2024Volume", "ProjectPro2024Volume"),
])
def test_modules_selon_la_famille(product, visio, project):
    root = _parse(catalog.office_config(product, addons=["visio", "project"]))
    ids = [p.get("ID") for p in root.findall("Add/Product")]
    assert ids[1:] == [visio, project]
    # OneDrive exclu aussi pour les modules
    assert root.findall("Add/Product")[1].find("ExcludeApp").get("ID") == "Groove"


def test_ltsc_canal_et_cle():
    root = _parse(catalog.office_config("ltsc2024", pidkey="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"))
    assert root.find("Add").get("Channel") == "PerpetualVL2024"
    assert root.find("Add/Product").get("PIDKEY") == "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"


@pytest.mark.parametrize("args", [("inconnu",), ("home2024", "MonthlyEnterprise"), ("ltsc2024", "Current")])
def test_refus_produit_ou_canal(args):
    with pytest.raises(ValueError):
        catalog.office_config(*args)


def test_plusieurs_langues_et_verification():
    root = _parse(catalog.office_config("business", languages=["fr-fr", "en-us", "ca-es"],
                                        addons=["visio"], proofing=["de-de", "fr-fr", "es-es"]))
    products = root.findall("Add/Product")
    langs = lambda el: [l.get("ID") for l in el.findall("Language")]  # noqa: E731
    assert langs(products[0]) == ["fr-fr", "en-us", "ca-es"]
    assert products[1].get("ID") == "VisioProRetail" and langs(products[1]) == ["fr-fr", "en-us", "ca-es"]
    # langues de vérification seules, sans répéter celles de l'interface
    assert products[2].get("ID") == "ProofingTools" and langs(products[2]) == ["de-de", "es-es"]


def test_visio_sans_variante_regionale():
    root = _parse(catalog.office_config("business", languages=["fr-ca", "en-gb"], addons=["project"]))
    assert [l.get("ID") for l in root.findall("Add/Product")[1].findall("Language")] == ["fr-fr", "en-us"]


@pytest.mark.parametrize("langs,proofing", [(["ca-es"], None), (["xx-yy"], None), (["fr-fr"], ["MatchOS"])])
def test_refus_langues(langs, proofing):
    with pytest.raises(ValueError):
        catalog.office_config("business", languages=langs, proofing=proofing)


def test_liste_des_langues():
    codes = [c for c, _, _ in catalog.OFFICE_LANGUAGES]
    assert codes[0] == "MatchOS" and len(codes) == len(set(codes)) > 90
    assert all(c == c.lower() or c == "MatchOS" for c in codes)
    assert catalog.LANGUAGE_NAMES["fr-fr"] == "Français" and "fr-fr" in catalog.FULL_LANGUAGES
    assert "eu-es" not in catalog.FULL_LANGUAGES


def test_canaux_proposes():
    choices = catalog.channel_choices("business")
    assert choices[0][:2] == ("Current", "Canal actuel (conseillé)")
    assert [c for c, _, _ in choices] == ["Current", "MonthlyEnterprise", "SemiAnnual", "CurrentPreview",
                                          "BetaChannel"]
    assert catalog.channel_choices("ltsc2024") == [("PerpetualVL2024", "Version perpétuelle 2024",
                                                    catalog.CHANNEL_HINTS["PerpetualVL2024"])]
    assert catalog.channel_choices("home2024")[0][1] == "Version 2024 (correctifs mensuels)"
    assert catalog.channel_choices("enterprise-apps")[0][0] == "MonthlyEnterprise"


def test_refus_application_inconnue():
    with pytest.raises(ValueError):
        catalog.office_config("business", exclude=["Bing"])


def test_tous_les_produits_ont_un_canal_valide():
    for p in catalog.OFFICE_PRODUCTS:
        assert p["default_channel"] in p["channels"]
        assert set(p["channels"]) <= set(catalog.CHANNELS)
        for addon in catalog.ADDONS.values():
            assert p["family"] in addon["ids"]


@pytest.mark.parametrize("locale,code", [
    ("fr_FR.UTF-8", "fr-fr"), ("fr_CA.UTF-8", "fr-ca"), ("en_US.UTF-8", "en-us"), ("en_GB", "en-gb"),
    ("pt_BR.UTF-8", "pt-br"), ("de_AT.UTF-8", "de-de"), ("C.UTF-8", "MatchOS"), ("", "MatchOS"),
    ("xx_YY", "MatchOS"), ("ca_ES.UTF-8", "ca-es"), ("sr_RS@latin", "sr-latn-rs"), ("zh_TW.UTF-8", "zh-tw"),
    ("nb_NO.UTF-8", "nb-no"), ("es_AR.UTF-8", "es-es"),
])
def test_langue_selon_la_locale(locale, code):
    assert catalog.language_for_locale(locale) == code


def test_script_office_contient_la_configuration():
    xml = catalog.office_config("home")
    script = catalog.office_script(xml)
    assert script.startswith("$ConfigXml = @'\n<Configuration>")
    assert "\n'@\n" in script and "setup.exe" in script and "VASISTAS-RESULT" in script
    assert "BetaChannel" not in catalog.OFFICE_PS1.read_text()


def test_script_winget():
    s = catalog.winget_script(["Microsoft.PowerBI", "7zip.7zip"], "install")
    assert s.startswith("$ids = @('Microsoft.PowerBI', '7zip.7zip')\n$action = 'install'")
    assert "Install-One $id 'machine'" in s and "@NOAPPLICABLE@" not in s and "-1978335135" in s
    with pytest.raises(ValueError):
        catalog.winget_script(["x'; Remove-Item"], "install")
    with pytest.raises(ValueError):
        catalog.winget_script(["a"], "upgrade")


def test_catalogue_des_applications():
    keys = [a["key"] for a in catalog.APPS]
    assert len(keys) == len(set(keys))
    assert all(a["category"] in catalog.CATEGORIES for a in catalog.APPS)
    ids = [w for a in catalog.APPS for _, w, _ in a["variants"]]
    assert len(ids) == len(set(ids)) and all(" " not in i for i in ids)
    assert all(a["winget_id"] == a["variants"][0][1] for a in catalog.APPS)
    assert catalog.variant_choices("visual-studio")[:3] == [
        ("community", "Community 2026"), ("professional", "Professional 2026"), ("enterprise", "Enterprise 2026")]


def test_langue_des_applications():
    assert catalog.language_choices("acrobat") == []  # installateur multilingue : langue de Windows
    assert "fr-FR" in catalog.language_choices("powerbi")
    assert catalog.language_choices("ssms", "20") == []  # ancien installateur sans module linguistique
    assert catalog.app_language("powerbi", "fr_FR.UTF-8") == "fr-FR"
    assert catalog.app_language("visual-studio", "fr-ca") == "fr-FR"
    assert catalog.app_language("visual-studio", "eu-es") is None


def test_plan_d_installation():
    ids, custom, locale, owner = catalog._install_plan(
        ["powerbi", "visual-studio", "acrobat", "ssms"],
        {"visual-studio": "professional", "acrobat": "pro", "ssms": "20"}, "fr_FR.UTF-8")
    assert ids == ["Microsoft.PowerBI", "Microsoft.VisualStudio.Professional", "Adobe.Acrobat.Pro",
                   "Microsoft.SQLServerManagementStudio"]
    assert custom == {"Microsoft.PowerBI": "LANGUAGE=fr-FR",
                      "Microsoft.VisualStudio.Professional": "--addProductLang fr-FR"}
    assert locale == {} and owner["Adobe.Acrobat.Pro"] == "acrobat"
    with pytest.raises(ValueError):
        catalog._install_plan(["acrobat"], {"acrobat": "inconnue"})


def test_script_winget_avec_options():
    s = catalog.winget_script(["Microsoft.PowerBI"], "install", {"Microsoft.PowerBI": "LANGUAGE=fr-FR"},
                              {"Microsoft.PowerBI": "fr-FR"})
    assert "$custom = @{'Microsoft.PowerBI' = 'LANGUAGE=fr-FR'}" in s
    assert "$locale = @{'Microsoft.PowerBI' = 'fr-FR'}" in s
    with pytest.raises(ValueError):
        catalog.winget_script(["a.b"], "install", {"a.b": "x'; Remove-Item C:"})
    with pytest.raises(ValueError):
        catalog.winget_script(["a.b"], "install", None, {"a.b": "fr FR"})


def test_lecture_du_resultat():
    out = ("Téléchargement…\r\n{pas du json\r\n"
           'VASISTAS-RESULT {"Microsoft.PowerBI":{"ok":true,"code":0,"installed":true}}\r\n')
    assert catalog.parse_result(out) == {"Microsoft.PowerBI": {"ok": True, "code": 0, "installed": True}}
    assert catalog.parse_result("rien") == {}
    assert catalog.parse_result(None) == {}


def test_execution_par_requete_simulee():
    sent = []

    def fake(req, timeout):
        sent.append((req, timeout))
        return {"code": 0, "out": 'VASISTAS-RESULT {"7zip.7zip":{"ok":true,"code":-1978335135,"installed":true}}'}

    res = catalog.install_apps(["7zip"], request=fake)
    assert res == {"7zip": {"ok": True, "code": -1978335135, "installed": True}}
    assert "'7zip.7zip'" in sent[0][0]["exec"] and sent[0][1] == catalog.LONG_TIMEOUT_S
    assert catalog.status(["7zip"], request=fake) == {"7zip": True}


def test_versions_installees():
    out = ('VASISTAS-RESULT {"Microsoft.VisualStudio.Community":{"installed":false},'
           '"Microsoft.VisualStudio.2022.Professional":{"installed":true}}')
    res = catalog.installed_variants(["visual-studio"], request=lambda req, timeout: {"code": 0, "out": out})
    assert res == {"visual-studio": ["professional-2022"]}


def test_erreur_de_l_hote():
    with pytest.raises(RuntimeError):
        catalog.install_apps(["7zip"], request=lambda req, timeout: {"error": "invité pas prêt"})
    with pytest.raises(RuntimeError):
        catalog.install_apps(["7zip"], request=lambda req, timeout: {"code": 1, "out": "plantage"})


def test_canal_d_une_installation_existante():
    out = ('VASISTAS-RESULT {"installed":true,"products":["O365ProPlusRetail"],"version":"16.0",'
           '"channel":"http://officecdn.microsoft.com/pr/492350f6-3a01-4f97-b9c0-c7c6ddf67d60","apps":[]}')
    res = catalog.office_status(request=lambda req, timeout: {"code": 0, "out": out})
    assert res["channel"] == "Current"


def test_codes_winget_lisibles():
    assert catalog.describe_code(0) == "installé"
    assert catalog.describe_code(-1978335216) == "échec (code 0x8a150010)"
