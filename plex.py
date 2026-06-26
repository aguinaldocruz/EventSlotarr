import logging
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

logger = logging.getLogger("plugins.eventslotarr")


def bool_setting(value, default=False):
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")

    return bool(value)


def plex_request(method, url, token, timeout=30):
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "X-Plex-Token": token,
            "Accept": "application/xml",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.read()


def refresh_plex_tv_guide(params):
    enabled = bool_setting(
        params.get("refresh_plex_on_epg_change"),
        default=False,
    )

    if not enabled:
        logger.info("[EventSlotarr] Plex guide refresh disabled")
        return False

    plex_url = str(params.get("plex_url") or "").strip().rstrip("/")
    plex_token = str(params.get("plex_token") or "").strip()

    if not plex_url or not plex_token:
        logger.warning(
            "[EventSlotarr] Plex guide refresh enabled but plex_url or plex_token is empty"
        )
        return False

    try:
        status, body = plex_request(
            "GET",
            f"{plex_url}/livetv/dvrs",
            plex_token,
            timeout=20,
        )

        if status != 200:
            logger.error(
                "[EventSlotarr] Failed to list Plex DVRs: HTTP %s",
                status,
            )
            return False

        root = ET.fromstring(body)
        dvrs = root.findall(".//Dvr")

        if not dvrs:
            logger.warning("[EventSlotarr] No Plex DVR devices found")
            return False

        refreshed = False

        for dvr in dvrs:
            dvr_id = dvr.attrib.get("key")
            title = dvr.attrib.get("title", "")

            if not dvr_id:
                continue

            logger.info(
                "[EventSlotarr] Refreshing Plex guide for DVR id=%s title=%r",
                dvr_id,
                title,
            )

            try:
                refresh_status, _ = plex_request(
                    "POST",
                    f"{plex_url}/livetv/dvrs/{dvr_id}/reloadGuide",
                    plex_token,
                    timeout=30,
                )

                if refresh_status in (200, 201, 202, 204):
                    logger.info(
                        "[EventSlotarr] Plex guide refresh requested for DVR id=%s",
                        dvr_id,
                    )
                    refreshed = True
                else:
                    logger.error(
                        "[EventSlotarr] Plex guide refresh failed for DVR id=%s: HTTP %s",
                        dvr_id,
                        refresh_status,
                    )

            except Exception as ex:
                logger.exception(
                    "[EventSlotarr] Plex guide refresh error for DVR id=%s: %s",
                    dvr_id,
                    ex,
                )

        return refreshed

    except urllib.error.HTTPError as ex:
        logger.error(
            "[EventSlotarr] Plex HTTP error: %s %s",
            ex.code,
            ex.reason,
        )
        return False

    except Exception as ex:
        logger.exception("[EventSlotarr] Plex guide refresh error: %s", ex)
        return False

