"""Version de Vasistas et adresses du projet."""

VERSION = "0.9.1"
# version expérimentale : les mises à jour proposées incluent les préversions GitHub
PRERELEASE = True
GITHUB_REPO = "melvincouwez-alt/vasistas"
WEBSITE = f"https://github.com/{GITHUB_REPO}"
ISSUES = f"{WEBSITE}/issues"


def parse(version):
    """« v0.5.1-beta.2 » -> ((0, 5, 1), « beta.2 ») ; comparable, la préversion avant la finale."""
    v = version.strip().lstrip("vV")
    core, _, pre = v.partition("-")
    nums = []
    for part in core.split("."):
        digits = "".join(c for c in part if c.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3]), pre


def newer(candidate, current=VERSION):
    """Vrai si `candidate` est plus récente que `current` (0.5.0-beta < 0.5.0 < 0.5.1)."""
    (a, pa), (b, pb) = parse(candidate), parse(current)
    if a != b:
        return a > b
    if pa == pb:
        return False
    return bool((not pa) or (pb and pa > pb))
