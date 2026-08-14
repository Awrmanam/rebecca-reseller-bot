import hashlib, hmac, json
from decimal import Decimal
from typing import Any

def _normalize(value: Any) -> Any:
    if isinstance(value, dict): return {k:_normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list): return [_normalize(x) for x in value]
    return value
def signature(payload: dict[str, Any], secret: str) -> str:
    data={k:v for k,v in payload.items() if k != "verify_hash"}
    canonical=json.dumps(_normalize(data), ensure_ascii=False, separators=(",",":"), sort_keys=True)
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha1).hexdigest()
def verify(payload: dict[str, Any], secret: str) -> bool:
    supplied=payload.get("verify_hash")
    return isinstance(supplied,str) and bool(supplied) and hmac.compare_digest(supplied, signature(payload,secret))
def exact_amount(value: Any) -> Decimal: return Decimal(str(value))
