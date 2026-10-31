"""Small fork boundary: version checks stay available, checkout swaps do not."""
from pathlib import Path


MESSAGE = (
    "This customized WebUI uses controlled upgrades. Merge and verify an upstream "
    "release on the custom branch before deploying it; browser updates are disabled."
)


def is_controlled(root: Path) -> bool:
    # Presence is the policy. An unreadable/malformed marker must not make a
    # customized checkout silently opt back into destructive self-updates.
    marker = Path(root) / "custom-build.json"
    return marker.exists() or marker.is_symlink()


def refusal(target: str, root: Path) -> dict | None:
    if target == "webui" and is_controlled(root):
        return {"ok": False, "reason": "controlled_update", "message": MESSAGE,
                "manual_update": True, "target": target}
    return None


def decorate(payload: dict, root: Path) -> dict:
    result = dict(payload)
    if is_controlled(root):
        result["webui"] = {**(result.get("webui") or {}), "manual_update": True,
                           "update_policy": "controlled", "update_instruction": MESSAGE}
    return result
