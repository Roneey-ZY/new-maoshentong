"""Verify vendored assets and export the actual generated UMD wrappers for CI."""
import argparse
import hashlib
import json
from pathlib import Path

from design_system import ASSETS, motion_sources


def check():
    for folder, manifest in [("vendor", "gsap-release.json"), ("fonts", "font-release.json")]:
        metadata = json.loads((ASSETS / folder / manifest).read_text(encoding="utf-8"))
        for name, expected in metadata["files"].items():
            if hashlib.sha256((ASSETS / folder / name).read_bytes()).hexdigest() != expected:
                raise ValueError("Local design asset checksum failed: " + name)
    scripts = motion_sources()
    if len(scripts) != 3:
        raise ValueError("Unexpected trusted script bundle structure")
    return {"evidence_type": "actual_generated_script_wrappers_not_browser_acceptance", "loaders": scripts[:2]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(check()), encoding="utf-8")
    print("Verified local GSAP and font hashes; exported actual runtime wrappers.")
