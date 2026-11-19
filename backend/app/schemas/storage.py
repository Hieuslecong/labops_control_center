from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, List

class BaseStorageSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

class StoragePoolInfoResponse(BaseStorageSchema):
    host_id: str = Field(alias="hostId")
    storage_id: str = Field(alias="storageId")
    device: str
    filesystem_uuid: str = Field(alias="filesystemUuid")
    mount_point: str = Field(alias="mountPoint")
    filesystem: str
    capacity_gib: float = Field(alias="capacityGib")
    used_gib: float = Field(alias="usedGib")
    free_gib: float = Field(alias="freeGib")
    allocatable_gib: float = Field(alias="allocatableGib")
    reserved_gib: float = Field(alias="reservedGib")
    quota_backend: str = Field(alias="quotaBackend")
    quota_supported: bool = Field(alias="quotaSupported")
    quota_enforced: bool = Field(alias="quotaEnforced")
    read_only: bool = Field(alias="readOnly")
    mounted: bool
    eligible: bool
    ineligibility_reason: Optional[str] = Field(None, alias="ineligibilityReason")
    last_verified_at: str = Field(alias="lastVerifiedAt")
    verification_status: str = Field(alias="verificationStatus")

class StorageValidateRequest(BaseStorageSchema):
    host_id: str = Field(alias="hostId")
    storage_id: str = Field(alias="storageId")
    requested_quota_gib: int = Field(alias="requestedQuotaGib", ge=1)

class StorageValidateResponse(BaseStorageSchema):
    valid: bool
    host_id: str = Field(alias="hostId")
    storage_id: str = Field(alias="storageId")
    requested_quota_gib: int = Field(alias="requestedQuotaGib")
    allocatable_gib: Optional[float] = Field(None, alias="allocatableGib")
    mount_point: Optional[str] = Field(None, alias="mountPoint")
    member_storage_dir_preview: Optional[str] = Field(None, alias="memberStorageDirPreview")
    error: Optional[str] = None

class MemoryPressureTelemetry(BaseStorageSchema):
    some_avg10: float = Field(0.0, alias="someAvg10")
    some_avg60: float = Field(0.0, alias="someAvg60")
    some_avg300: float = Field(0.0, alias="someAvg300")
    full_avg10: float = Field(0.0, alias="fullAvg10")
    full_avg60: float = Field(0.0, alias="fullAvg60")
    full_avg300: float = Field(0.0, alias="fullAvg300")
    pressure_level: str = Field("NONE", alias="pressureLevel")
    is_high_pressure: bool = Field(False, alias="isHighPressure")

class HostResourceProfileResponse(BaseStorageSchema):
    host_id: str = Field(alias="hostId")
    total_cores: int = Field(alias="totalCores")
    cpu_scheduling_mode: str = Field("SHARED_WEIGHTED", alias="cpuSchedulingMode")
    default_cpu_weight: int = Field(100, alias="defaultCpuWeight")
    pids_limit: int = Field(4096, alias="pidsLimit")
    total_ram_bytes: int = Field(alias="totalRamBytes")
    host_reserve_bytes: int = Field(alias="hostReserveBytes")
    shared_budget_bytes: int = Field(alias="sharedBudgetBytes")
    memory_protection_mode: str = Field("SHARED_HOST_PROTECTED", alias="memoryProtectionMode")
    memory_pressure: Optional[MemoryPressureTelemetry] = Field(None, alias="memoryPressure")
