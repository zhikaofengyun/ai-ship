"""An alpha mask in the visible clip box, after oriented decoding and contain/cover fit."""

from math import isfinite

from .errors import DomainError


def normalize_mask(value, audio_only=False):
    if value is None:
        return None
    if (
        audio_only
        or not isinstance(value, dict)
        or set(value) not in ({"space", "rect"}, {"space", "rect", "shape"})
    ):
        raise DomainError("INVALID_CLIP_MASK", "画面遮罩需要指定构图后的归一化矩形。")
    shape = value.get("shape", "rectangle")
    if shape not in ("rectangle", "ellipse"):
        raise DomainError("INVALID_CLIP_MASK", "当前支持矩形与椭圆遮罩。")
    rect = value["rect"]
    if (
        value["space"] != "post_fit_clip"
        or not isinstance(rect, list)
        or len(rect) != 4
        or not all(type(n) in (float, int) and 0 <= n <= 1 and isfinite(n) for n in rect)
    ):
        raise DomainError("INVALID_CLIP_MASK", "矩形坐标应为 0 到 1 的左、上、右、下边界。")
    left, top, right, bottom = rect
    if left >= right or top >= bottom:
        raise DomainError("INVALID_CLIP_MASK", "遮罩区域必须具有正宽度和高度。")
    return {
        "space": "post_fit_clip",
        "rect": list(rect),
        **({"shape": "ellipse"} if shape == "ellipse" else {}),
    }


def alpha_filter(mask, opacity):
    left, top, right, bottom = mask["rect"]
    region = f"gte(X,W*{left:.12g})*lt(X,W*{right:.12g})*gte(Y,H*{top:.12g})*lt(Y,H*{bottom:.12g})"
    if mask.get("shape") == "ellipse":
        cx, cy = (left + right) / 2, (top + bottom) / 2
        rx, ry = (right - left) / 2, (bottom - top) / 2
        region = f"lte(pow((X+0.5-W*{cx:.12g})/(W*{rx:.12g}),2)+pow((Y+0.5-H*{cy:.12g})/(H*{ry:.12g}),2),1)"
    return f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({opacity})*{region}':interpolation=nearest"
