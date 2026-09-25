from sqlalchemy.orm import Session

from . import settings as settings_svc


def compute_fees(db: Session, side: str, price: float, qty: int) -> dict[str, float]:
    amount = price * qty
    commission_rate = settings_svc.get_float(db, "commission_rate")
    commission_min = settings_svc.get_float(db, "commission_min")
    stamp_rate = settings_svc.get_float(db, "stamp_tax_rate")
    transfer_rate = settings_svc.get_float(db, "transfer_fee_rate")

    commission = max(amount * commission_rate, commission_min)
    stamp = amount * stamp_rate if side == "sell" else 0.0
    transfer = amount * transfer_rate
    return {
        "fee_commission": round(commission, 2),
        "fee_stamp": round(stamp, 2),
        "fee_transfer": round(transfer, 2),
    }
