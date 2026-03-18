from dataclasses import dataclass
from datetime import datetime


@dataclass
class DbModelBase:
    updated_at: datetime | None = None
