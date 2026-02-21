import logging
from typing import Optional

import typer

from dicomhawk import new_dicomhawk
from dicomhawk.config import settings
from dicomhawk.server import ServerConfig

serve_app = typer.Typer(help="dicomhawk runner")


@serve_app.command()
def serve(
    ports: Optional[str] = typer.Option(
        None,
        "-p", "--ports",
        help=(
            "Port(s) to listen on. Single port (11112) or comma-separated list (104,11112). "
            "Defaults to DICOM_PORTS env var, then [11112]."
        ),
    ),
    ae_title: Optional[str] = typer.Option(
        None,
        "--ae-title",
        help="AE title to present to connecting clients. Defaults to DICOM_AE_TITLE env var.",
    ),
    impl_uid: Optional[str] = typer.Option(
        None,
        "--impl-uid",
        help="Implementation class UID. Defaults to DICOM_IMPLEMENTATION_UID env var.",
    ),
    impl_name: Optional[str] = typer.Option(
        None,
        "--impl-name",
        help="Implementation version name. Defaults to DICOM_IMPLEMENTATION_NAME env var.",
    ),
    block_scanners: Optional[bool] = typer.Option(
        None,
        "--block-scanners/--no-block-scanners",
        help="Block known scanner IPs. Defaults to BLOCK_SCANNERS env var.",
    ),
    integrity_check: Optional[bool] = typer.Option(
        None,
        "--integrity-check/--no-integrity-check",
        help="Enable DICOM file integrity checks. Defaults to INTEGRITY_CHECK env var.",
    ),
):
    """Start the DICOM honeypot server.

    Flags take priority over environment variables. Unset flags fall back
    to the corresponding env var or a safe default.
    """
    logging.basicConfig(
        level=logging.DEBUG if not settings.PROD else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("dicomhawk")

    # --- Resolve ports ---
    # Flag → env var (DICOM_PORTS) → settings default
    if ports is not None:
        # Accept both "11112" and "104,11112"
        resolved_ports = [int(p.strip()) for p in ports.split(",")]
    else:
        resolved_ports = settings.DICOM.PORTS  # already parsed from DICOM_PORTS env var

    # --- Build ServerConfig: flags override settings ---
    config = ServerConfig()
    config.PORTS            = resolved_ports
    config.HOST             = settings.DICOM.SERVER_HOST
    config.STORAGE_DIR      = settings.DICOM.STORAGE_DIR
    config.C_STORE_DIR      = settings.DICOM.C_STORE_DIR
    config.DATABASE         = settings.DICOM.DATABASE
    config.HASH_STORE       = settings.DICOM.HASH_STORE
    config.CANARY_PDF       = settings.DICOM.CANARY_PDF
    config.AE_TITLE         = ae_title   or settings.DICOM.AE_TITLE
    config.IMPLEMENTATION_UID  = impl_uid  or settings.DICOM.IMPLEMENTATION_UID
    config.IMPLEMENTATION_NAME = impl_name or settings.DICOM.IMPLEMENTATION_NAME
    config.INTEGRITY_CHECK  = integrity_check if integrity_check is not None else settings.DICOM.INTEGRITY_CHECK
    config.MAX_ASSOC        = 10

    # block_scanners is read by higher-level logic via settings; flag can override at runtime
    if block_scanners is not None:
        # Patch the global settings object so downstream code sees the flag value
        object.__setattr__(settings, "BLOCK_SCANNERS", block_scanners)

    hp = new_dicomhawk(logger, [], config)
    hp.start()