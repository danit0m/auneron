# Register the import tables and the title-reference side table (TASK054)
# in the existing application metadata. Import does not execute DDL.
# Historical references have no cascading FK; the side table's FK to
# accounts is RESTRICT. Single definition in app.import_confirmation_schema.
from app.database.database import Base
from app.import_confirmation_schema import receipts_table, audit_events_table, title_references_table

import_confirmation_receipts = receipts_table.to_metadata(Base.metadata)
import_confirmation_audit_events = audit_events_table.to_metadata(Base.metadata)
account_title_references = title_references_table.to_metadata(Base.metadata)
