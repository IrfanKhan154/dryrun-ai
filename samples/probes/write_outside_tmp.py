from pathlib import Path

Path("/outside-dryrun-probe.txt").write_text("probe", encoding="utf-8")
