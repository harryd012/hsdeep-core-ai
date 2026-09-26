"""Verify the Device Action Plane routes are registered on the FastAPI app."""
import sys
sys.path.insert(0, r"I:\AI-LAB\hsdeep-core-ai\backend")

from app.main import app  # noqa: E402

targets = [r for r in app.routes if getattr(r, "path", "").startswith("/api/device-actions")
           or getattr(r, "path", "").endswith("/capabilities")]
print(f"device action routes: {len(targets)}")
for r in targets:
    print(f"  {sorted(getattr(r, 'methods', []) or [])} {r.path}")

# Confirm the router did not collide with existing paths.
all_paths = [getattr(r, "path", "") for r in app.routes]
dupes = {p for p in all_paths if all_paths.count(p) > 1 and p.startswith("/api/device-actions")}
print("duplicate device-action paths:", dupes or "none")
print("total app routes:", len(all_paths))