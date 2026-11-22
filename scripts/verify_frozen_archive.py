"""Check the original experiment against its archived git tree, not today's engine."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(commit="f3f8161"):
    manifest = json.loads((ROOT / "data/independent_validation/v1/manifest.json").read_text(encoding="utf-8"))
    expected = {**manifest["engine"], **{"data/independent_validation/v1/" + key: value
                                       for key, value in manifest["files"].items()}}
    for name, digest in expected.items():
        contents = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
        if hashlib.sha256(contents).hexdigest() != digest:
            raise ValueError(f"Archived frozen hash mismatch: {name}")
    print(f"Archived frozen files verified: {len(expected)}; original engine commit: {commit}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="f3f8161")
    verify(parser.parse_args().commit)
