"""Machine-readable progress for isolated jobs; never persist exception messages."""
import json
from pathlib import Path


def write_stage(config_path: str, stage: str) -> None:
    config = Path(config_path)
    if config.name != "_config.yaml":
        return
    path = config.parent / "_pipeline.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"stage": stage}), encoding="utf-8")
    temporary.replace(path)
