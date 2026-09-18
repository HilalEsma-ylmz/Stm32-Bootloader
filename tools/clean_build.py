"""Remove generated output directories, then rebuild both projects."""
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
targets = [root / project / config
           for project in ("application_stm32", "bootloader_stm32")
           for config in ("Debug", "Release")]
targets.append(root / "build")
for path in targets:
    if path.is_symlink() or path.resolve() != path.absolute() or not path.resolve().is_relative_to(root):
        raise RuntimeError(f"Unsafe output directory: {path}")
for path in targets:
    if path.exists():
        shutil.rmtree(path)
        print(f"Removed generated directory: {path}", flush=True)
subprocess.run([sys.executable, str(root / "tools/build_firmware.py")], check=True)
