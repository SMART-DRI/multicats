from dataclasses import dataclass


@dataclass
class StaticConfig:
    """Cluster-operator configuration. Fixed at daemon startup.

    Scheduler-specific parameters (e.g. COAST weights) live in the
    scheduler's own config, not here.
    """

    provider: str  # "slurm_cli"
    scheduler: str  # name of the scheduler module, e.g. "coast", "fifo"
    controller: str  # "slurm"

    microbatch_size: int = 300  # seconds between queue polls

    def validate(self) -> None:
        valid_providers = {"slurm_cli"}
        valid_controllers = {"slurm"}

        if self.provider not in valid_providers:
            raise ValueError(f"provider must be one of {valid_providers}")
        if self.controller not in valid_controllers:
            raise ValueError(f"controller must be one of {valid_controllers}")
        if self.microbatch_size <= 0:
            raise ValueError("microbatch_size must be positive")
