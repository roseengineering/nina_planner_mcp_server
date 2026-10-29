from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for every model that describes a *plan file*.

    ``extra="forbid"``: a key that is not in the schema is refused instead of
    silently dropped. Dropping would let a typo (``exposure`` for
    ``exposure_time_seconds``) or a field removed from the schema (the flat
    spec's old ``exposure_time_seconds``) load as if it were still honoured —
    the plan would then mean something other than what it says.

    Models that parse N.I.N.A.'s own API responses are deliberately *not*
    subclasses: N.I.N.A. may add fields of its own, and there refusing the
    payload would break us on someone else's change.
    """

    model_config = ConfigDict(extra="forbid")
