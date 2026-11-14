from datetime import datetime, timedelta


def plus_seconds(value: str, seconds: float) -> str:
    return (
        (datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(seconds=seconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
