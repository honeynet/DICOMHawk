import logging

import requests

logger = logging.getLogger(__name__)

_NBIA_BASE = "https://services.cancerimagingarchive.net/nbia-api/services/v1"


class TciaClient:
    def __init__(self, base_url: str = _NBIA_BASE, timeout: int = 30):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def get_series(self, collection: str, modality: str = "CT") -> list[dict]:
        try:
            r = requests.get(
                f"{self._base}/getSeries",
                params={"Collection": collection, "Modality": modality},
                timeout=self._timeout,
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error(f"TCIA getSeries failed: {exc}")
            return []

    def get_sop_uids(self, series_uid: str) -> list[str]:
        try:
            r = requests.get(
                f"{self._base}/getSOPInstanceUIDs",
                params={"SeriesInstanceUID": series_uid},
                timeout=self._timeout,
            )
            r.raise_for_status()
            items = r.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error(f"TCIA getSOPInstanceUIDs failed for {series_uid}: {exc}")
            return []
        return [uid for item in items if (uid := item.get("SOPInstanceUID"))]

    def download_image(self, series_uid: str, sop_uid: str) -> bytes | None:
        try:
            r = requests.get(
                f"{self._base}/getSingleImage",
                params={"SeriesInstanceUID": series_uid, "SOPInstanceUID": sop_uid},
                timeout=self._timeout,
            )
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            logger.error(f"TCIA getSingleImage failed for {sop_uid}: {exc}")
            return None
