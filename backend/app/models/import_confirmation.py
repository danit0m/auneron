# Register only the two import tables in the existing application metadata.
# Import does not execute DDL. Historical references have no cascading FK.
from app.database.database import Base
from app.import_confirmation_schema import receipts_table, audit_events_table

import_confirmation_receipts = receipts_table.to_metadata(Base.metadata)
import_confirmation_audit_events = audit_events_table.to_metadata(Base.metadata)
