from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for every model that describes a *plan file*.

    ``extra="forbid"``: a key that is not in the schema is refused instead of
    silently dropped — a typo (``exposure`` for ``exposure_time_seconds``)
    would otherwise load as if it were honoured. Models that parse N.I.N.A.'s
    own API responses are deliberately *not* subclasses: N.I.N.A. may add
    fields of its own, and there refusing the payload would break us.
    """

    model_config = ConfigDict(extra="forbid")
