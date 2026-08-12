"""Prove that Vision OS and the production frontend were not modified.

Two checks, both evidence-producing rather than assertion-producing:

1. **Git.** Both `backend/` and `frontend/` are their own git repositories, so
   `git status --porcelain` is the authoritative answer to "did anything change".
   This is the check that matters, and it is the one a release reviewer should
   read.

2. **Hash manifest.** A SHA-256 per file under `backend/app/vision_os/` and
   `frontend/`, written to `docs/manifest-<name>.json`. Re-running compares
   against the stored manifest and reports any drift. This survives the case
   where git is unavailable, and it produces an artefact a release record can
   cite.

Usage:
    python scripts/verify_untouched.py            # verify against stored manifests
    python scripts/verify_untouched.py --write    # (re)create the manifests

Exit code is 0 only when both targets are clean.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ATLAS = REPO.parent

TARGETS = {
    "vision_os": ATLAS / "backend" / "app" / "vision_os",
    "frontend": ATLAS / "frontend",
}

#: Never hashed: build output, caches and dependencies are expected to churn and
#: their movement says nothing about whether source was modified.
SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".next",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    "venv",
    "dist",
    "build",
    ".turbo",
    "coverage",
}


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def manifest_for(root: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in iter_files(root):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digests[str(path.relative_to(root)).replace("\\", "/")] = digest
    return digests


def git_status(root: Path) -> tuple[bool, str]:
    """(clean, detail). A repo we cannot query is reported, never assumed clean."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"git unavailable: {type(exc).__name__}: {exc}"

    if result.returncode != 0:
        return False, f"git exited {result.returncode}: {result.stderr.strip()}"

    changes = [line for line in result.stdout.splitlines() if line.strip()]
    if changes:
        return False, "\n".join(f"    {line}" for line in changes)
    return True, "working tree clean"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="(re)create the manifests")
    args = parser.parse_args()

    docs = REPO / "docs"
    docs.mkdir(exist_ok=True)
    ok = True

    print("=" * 72)
    print("  Vision OS Validation Console — untouched-source verification")
    print("=" * 72)

    for name, root in TARGETS.items():
        print(f"\n[{name}]  {root}")
        if not root.exists():
            print("  MISSING — cannot verify")
            ok = False
            continue

        current = manifest_for(root)
        print(f"  files hashed: {len(current)}")

        stored_path = docs / f"manifest-{name}.json"
        if args.write:
            stored_path.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  manifest written: {stored_path.name}")
        elif stored_path.exists():
            stored = json.loads(stored_path.read_text(encoding="utf-8"))
            added = sorted(set(current) - set(stored))
            removed = sorted(set(stored) - set(current))
            changed = sorted(
                path for path in set(current) & set(stored) if current[path] != stored[path]
            )
            if added or removed or changed:
                ok = False
                print(f"  DRIFT — +{len(added)} -{len(removed)} ~{len(changed)}")
                for path in (added + removed + changed)[:20]:
                    print(f"    {path}")
            else:
                print("  manifest matches — no file changed")
        else:
            print(f"  no stored manifest ({stored_path.name}); run with --write to create one")

        # The git repo lives one level up for vision_os (backend/ is the repo root).
        repo_root = ATLAS / "backend" if name == "vision_os" else root
        clean, detail = git_status(repo_root)
        print(f"  git: {'CLEAN' if clean else 'DIRTY'}")
        if not clean:
            print(detail)
            ok = False

    print("\n" + "=" * 72)
    print(f"  RESULT: {'PASS — nothing was modified' if ok else 'FAIL — see above'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
