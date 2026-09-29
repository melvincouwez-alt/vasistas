"""Langue, clavier et fuseau horaire de Windows, déduits de ceux du système Linux.

Servent au fichier de réponses de l'installation (autounattend.xml) : Windows s'installe dans
la langue de l'utilisateur, avec son clavier et son fuseau horaire. L'ISO de Windows doit
contenir cette langue (ISO téléchargé dans la même langue : voir winiso.py).
"""

import locale
import os
import subprocess
from pathlib import Path

# disposition XKB -> identifiant de disposition Windows (langue:clavier)
KEYBOARDS = {
    "fr": "040c:0000040c", "fr(oss)": "040c:0000040c", "fr(azerty)": "040c:0000040c",
    "fr(bepo)": "040c:0000040c", "be": "080c:0000080c", "ch": "0807:00000807", "ch(fr)": "100c:0000100c",
    "ca": "0c0c:00001009", "ca(fr)": "0c0c:00001009", "us": "0409:00000409", "us(intl)": "0409:00020409",
    "gb": "0809:00000809", "de": "0407:00000407", "at": "0c07:00000407", "es": "0c0a:0000040a",
    "it": "0410:00000410", "pt": "0816:00000816", "br": "0416:00010416", "nl": "0413:00020409",
    "se": "041d:0000041d", "no": "0414:00000414", "dk": "0406:00000406", "fi": "040b:0000040b",
    "pl": "0415:00000415", "cz": "0405:00000405", "tr": "041f:0000041f", "ru": "0419:00000419",
    "ua": "0422:00000422", "gr": "0408:00000408", "hu": "040e:0000040e", "ro": "0418:00010418",
    "jp": "0411:{03B5835F-F03C-411B-9CE2-AA23E1171E36}{A76C93D9-5523-4E90-AAFA-4DB112F9AC76}",
}
# langue Linux -> disposition par défaut (quand la disposition XKB est inconnue)
LANG_KEYBOARD = {"fr": "fr", "en": "us", "de": "de", "es": "es", "it": "it", "pt": "pt", "nl": "us",
                 "sv": "se", "nb": "no", "da": "dk", "fi": "fi", "pl": "pl", "cs": "cz", "tr": "tr",
                 "ru": "ru", "uk": "ua", "el": "gr", "hu": "hu", "ro": "ro", "ja": "jp"}
# fuseau IANA -> fuseau Windows (extrait du tableau windowsZones du CLDR)
TIMEZONES = {
    "Europe/Paris": "Romance Standard Time", "Europe/Brussels": "Romance Standard Time",
    "Europe/Madrid": "Romance Standard Time", "Europe/Copenhagen": "Romance Standard Time",
    "Europe/Berlin": "W. Europe Standard Time", "Europe/Zurich": "W. Europe Standard Time",
    "Europe/Rome": "W. Europe Standard Time", "Europe/Amsterdam": "W. Europe Standard Time",
    "Europe/Vienna": "W. Europe Standard Time", "Europe/Stockholm": "W. Europe Standard Time",
    "Europe/Oslo": "W. Europe Standard Time", "Europe/Luxembourg": "W. Europe Standard Time",
    "Europe/Monaco": "W. Europe Standard Time", "Europe/Andorra": "W. Europe Standard Time",
    "Europe/Warsaw": "Central European Standard Time", "Europe/Prague": "Central Europe Standard Time",
    "Europe/Budapest": "Central Europe Standard Time", "Europe/London": "GMT Standard Time",
    "Europe/Dublin": "GMT Standard Time", "Europe/Lisbon": "GMT Standard Time",
    "Europe/Athens": "GTB Standard Time", "Europe/Bucharest": "GTB Standard Time",
    "Europe/Helsinki": "FLE Standard Time", "Europe/Kiev": "FLE Standard Time",
    "Europe/Kyiv": "FLE Standard Time", "Europe/Istanbul": "Turkey Standard Time",
    "Europe/Moscow": "Russian Standard Time", "Africa/Casablanca": "Morocco Standard Time",
    "Africa/Algiers": "W. Central Africa Standard Time", "Africa/Tunis": "W. Central Africa Standard Time",
    "Africa/Dakar": "Greenwich Standard Time", "Africa/Abidjan": "Greenwich Standard Time",
    "Africa/Kinshasa": "W. Central Africa Standard Time", "Africa/Johannesburg": "South Africa Standard Time",
    "America/New_York": "Eastern Standard Time", "America/Toronto": "Eastern Standard Time",
    "America/Montreal": "Eastern Standard Time", "America/Chicago": "Central Standard Time",
    "America/Denver": "Mountain Standard Time", "America/Phoenix": "US Mountain Standard Time",
    "America/Los_Angeles": "Pacific Standard Time", "America/Vancouver": "Pacific Standard Time",
    "America/Halifax": "Atlantic Standard Time", "America/Sao_Paulo": "E. South America Standard Time",
    "America/Mexico_City": "Central Standard Time (Mexico)", "America/Martinique": "SA Western Standard Time",
    "America/Guadeloupe": "SA Western Standard Time", "America/Cayenne": "SA Eastern Standard Time",
    "Indian/Reunion": "Mauritius Standard Time", "Indian/Mayotte": "E. Africa Standard Time",
    "Pacific/Noumea": "Central Pacific Standard Time", "Pacific/Tahiti": "Hawaiian Standard Time",
    "Asia/Tokyo": "Tokyo Standard Time", "Asia/Shanghai": "China Standard Time",
    "Asia/Kolkata": "India Standard Time", "Asia/Dubai": "Arabian Standard Time",
    "Australia/Sydney": "AUS Eastern Standard Time", "UTC": "UTC", "Etc/UTC": "UTC",
}


def linux_locale():
    """« fr_FR » d'après LC_ALL, LC_MESSAGES ou LANG ; « en_US » à défaut."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value and value not in ("C", "POSIX") and not value.startswith("C."):
            return value.split(".")[0].split("@")[0]
    loc = locale.getlocale()[0]
    return loc or "en_US"


def windows_locale(loc=None):
    """« fr_FR » -> « fr-FR »."""
    loc = loc or linux_locale()
    lang, _, country = loc.partition("_")
    return f"{lang.lower()}-{country.upper()}" if country else lang.lower()


def _xkb_layout():
    """Première disposition du bureau (« fr+oss » -> « fr(oss) »), sinon None."""
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.input-sources", "sources"],
                             capture_output=True, text=True, timeout=3).stdout
        start = out.find("('xkb', '")
        if start >= 0:
            value = out[start + 9:out.find("'", start + 9)]
            layout, _, variant = value.partition("+")
            return f"{layout}({variant})" if variant else layout
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for line in Path("/etc/default/keyboard").read_text().splitlines():
            if line.startswith("XKBLAYOUT="):
                return line.split("=", 1)[1].strip('"').split(",")[0]
    except OSError:
        pass
    return None


def windows_keyboard(loc=None):
    layout = _xkb_layout()
    if layout in KEYBOARDS:
        return KEYBOARDS[layout]
    if layout and layout.split("(")[0] in KEYBOARDS:
        return KEYBOARDS[layout.split("(")[0]]
    lang = (loc or linux_locale()).split("_")[0]
    return KEYBOARDS.get(LANG_KEYBOARD.get(lang, "us"))


def linux_timezone():
    try:
        target = os.readlink("/etc/localtime")
        return target.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        pass
    try:
        return Path("/etc/timezone").read_text().strip()
    except OSError:
        return "UTC"


def windows_timezone(tz=None):
    return TIMEZONES.get(tz or linux_timezone(), "UTC")


# langue d'un ISO de Windows (nom Microsoft, voir winiso.py) -> langue de l'interface. Le
# fichier de réponses doit citer une langue que l'ISO contient, sinon l'installation s'arrête.
ISO_UI_LANGUAGES = {
    "Arabic": "ar-SA", "Brazilian Portuguese": "pt-BR", "Bulgarian": "bg-BG",
    "Chinese (Simplified)": "zh-CN", "Chinese (Traditional)": "zh-TW", "Croatian": "hr-HR",
    "Czech": "cs-CZ", "Danish": "da-DK", "Dutch": "nl-NL", "English": "en-US",
    "English International": "en-GB", "Estonian": "et-EE", "Finnish": "fi-FI", "French": "fr-FR",
    "French Canadian": "fr-CA", "German": "de-DE", "Greek": "el-GR", "Hebrew": "he-IL",
    "Hungarian": "hu-HU", "Italian": "it-IT", "Japanese": "ja-JP", "Korean": "ko-KR",
    "Latvian": "lv-LV", "Lithuanian": "lt-LT", "Norwegian": "nb-NO", "Polish": "pl-PL",
    "Portuguese": "pt-PT", "Romanian": "ro-RO", "Russian": "ru-RU", "Serbian Latin": "sr-Latn-RS",
    "Slovak": "sk-SK", "Slovenian": "sl-SI", "Spanish": "es-ES", "Spanish (Mexico)": "es-MX",
    "Swedish": "sv-SE", "Thai": "th-TH", "Turkish": "tr-TR", "Ukrainian": "uk-UA",
}


def unattend_values(iso_language=None):
    """Valeurs à placer dans autounattend.xml. `iso_language` : langue de l'ISO (nom Microsoft) ;
    sans elle, la langue du système."""
    loc = linux_locale()
    ui = ISO_UI_LANGUAGES.get(iso_language) or windows_locale(loc)
    return {"@UILANG@": ui, "@LOCALE@": windows_locale(loc), "@KEYBOARD@": windows_keyboard(loc),
            "@TIMEZONE@": windows_timezone()}
