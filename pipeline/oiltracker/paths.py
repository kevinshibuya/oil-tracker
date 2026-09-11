"""Where the repository keeps configuration that is not code."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
PORTS_DIR = CONFIG_DIR / "ports"
VESSELS_DIR = CONFIG_DIR / "vessels"
AIS_SOURCES = CONFIG_DIR / "ais_sources.yaml"
DATA_DIR = REPO_ROOT / "data"
