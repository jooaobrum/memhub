from memhub.types import MemoryBase
class Incident(MemoryBase):
    equipment: str
    fault_code: str
    fix: str
