"""Per-garment variation: every garment on the rail gets its own hood fall,
sleeve pose and folds, drawn deterministically from its slot index.

Most hoods differ only slightly; about `bent_share` of the garments get one or
both sleeves swung slightly forward and bent at the elbow, the rest hang
straight. Every sleeve also gets its own fullness and 0 to 2 broad, soft
diagonal folds.
"""

import random

NEUTRAL = {"hood": {}, "sleeves": {}, "fullness": {}, "sleeve_folds": [], "wrinkle_seed": None}


def slot_shape(cfg, index):
    var = cfg["variation"]
    rng = random.Random(f"{var['seed']}-{index}")  # string seeds are hashed: no correlation between slots
    hood = {key: cfg["hood"].get(key, 0.0) + rng.uniform(-spread, spread) for key, spread in var["hood"].items()}
    s = var["sleeves"]
    pose, fullness = {}, {}
    if rng.random() < s["bent_share"]:
        sides = ["pos", "neg"] if rng.random() < s["both_share"] else [rng.choice(["pos", "neg"])]
        for side in sides:
            pose[side] = {"forward_deg": rng.uniform(*s["forward_deg"]), "elbow_deg": rng.uniform(*s["elbow_deg"])}
    folds = []
    for side in ("pos", "neg"):
        fullness[side] = rng.uniform(*s["fullness"])
        for _ in range(rng.choice(s["fold_counts"])):
            folds.append({
                "side": side,
                "t": rng.uniform(0.3, 0.75),
                "angle_deg": rng.uniform(20.0, 55.0) * rng.choice((-1.0, 1.0)),
                "face": rng.choice(("front", "back", "both")),
                "strength": rng.uniform(0.6, 1.0),
            })
    return {"hood": hood, "sleeves": pose, "fullness": fullness, "sleeve_folds": folds, "wrinkle_seed": var["seed"] * 1000 + index}
