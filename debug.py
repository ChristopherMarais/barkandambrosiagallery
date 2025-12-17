import os
import sys
from pathlib import Path

print(f"Current Working Directory: {os.getcwd()}")

# 1. Check for blocking file
target_path = Path("beetlesgallery")
if target_path.is_file():
    print(f"\n[CRITICAL ERROR] Found a FILE named 'beetlesgallery'. This blocks the folder.")
    print("Deleting the blocking file...")
    os.remove(target_path)
    print("[FIXED] Blocking file deleted.")

# 2. Check for directory
if not target_path.is_dir():
    print(f"\n[ERROR] 'beetlesgallery' folder not found at {target_path.absolute()}")
    sys.exit(1)
else:
    print(f"\n[OK] 'beetlesgallery' folder exists.")

# 3. Check for __init__.py
init_file = target_path / "__init__.py"
if not init_file.exists():
    print(f"[MISSING] __init__.py is missing.")
    print("Creating __init__.py...")
    with open(init_file, 'w') as f:
        f.write("") # Create empty file
    print("[FIXED] Created __init__.py")
else:
    print(f"[OK] __init__.py exists.")

# 4. Attempt Import
print("\n--- Testing Import ---")
sys.path.append(os.getcwd()) # Ensure current dir is in path
try:
    import beetlesgallery
    print(f"[SUCCESS] Imported 'beetlesgallery'")
    import beetlesgallery.settings
    print(f"[SUCCESS] Imported 'beetlesgallery.settings'")
    print("\nREADY! You can now run your migration.")
except ImportError as e:
    print(f"\n[FAIL] Still cannot import: {e}")
    print("Double check that beetlesgallery/settings.py exists.")