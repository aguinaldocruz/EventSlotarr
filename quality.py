QUALITY_PRIORITIES = {
    "HD": 0,
    "HEVC": 1,
    "H265": 1,
    "FHD": 2,
    "1080": 3,
    "720": 4,
    "SD": 5
}


def normalize_quality(quality):
    if not quality:
        return "OTHER"

    quality = quality.upper()

    if quality == "HD":
        return "HD"
    if "HEVC" in quality:
        return "HEVC"
    if "H265" in quality:
        return "H265"
    if "FHD" in quality:
        return "FHD"
    if "1080" in quality:
        return "1080"
    if "720" in quality:
        return "720"
    if "SD" in quality:
        return "SD"

    return "OTHER"


def quality_score(quality):
    return QUALITY_PRIORITIES.get(normalize_quality(quality), 999)
