from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentConfig(StrictConfigModel):
    experiment_id: str
    description: str
    scene_type: Literal["static", "dynamic"]
    random_seed: int


class ExecutionConfig(StrictConfigModel):
    num_simulations: int
    num_workers: int
    overwrite_existing: bool = False
    resume_if_possible: bool = True
    save_scene_manifest: bool = True
    save_render_metadata: bool = True


class RavenConfig(StrictConfigModel):
    base_rpf_file: Path


class RenderConfig(StrictConfigModel):
    sample_rate_hz: int
    generate_rir: bool = False
    generate_brir: bool = True
    simulation_type_rt: bool = True
    simulation_type_is: bool = True
    num_particles: int = 60000
    is_order_ps: int = 2


class ColoredNoiseStrategyConfig(StrictConfigModel):
    type: Literal["colored"]
    colors: list[Literal["white", "pink", "brown"]]


class AudioFolderNoiseStrategyConfig(StrictConfigModel):
    type: Literal["audio_folder"]
    noise_type: str
    audio_dir: Path
    file_pattern: str = "*.wav"


BackgroundNoiseStrategyConfig = ColoredNoiseStrategyConfig | AudioFolderNoiseStrategyConfig


class BackgroundNoiseConfig(StrictConfigModel):
    enabled: bool = False
    allow_multiple_layers: bool = False
    snr_db: float = 0.0
    strategies: list[BackgroundNoiseStrategyConfig] = Field(default_factory=list)


class RangeFloat(StrictConfigModel):
    min: float
    max: float


class FixedFloat(StrictConfigModel):
    fixed: float


class MarginXYZ(StrictConfigModel):
    x: float
    y: float
    z: float


class ReceiverPositionStrategy(StrictConfigModel):
    type: Literal["random_uniform_inside_room"]
    margin_m: MarginXYZ
    fixed_height_m: RangeFloat


class ReceiverOrientationStrategy(StrictConfigModel):
    type: Literal["random_yaw"]
    yaw_deg: RangeFloat
    pitch_deg: FixedFloat
    roll_deg: FixedFloat


class ReceiverSamplingConfig(StrictConfigModel):
    one_receiver_per_scene: bool
    position_strategy: ReceiverPositionStrategy
    orientation_strategy: ReceiverOrientationStrategy


class ReceiverOutputConfig(StrictConfigModel):
    enabled: bool
    ir_catalog_path: Path
    file_pattern: str
    output_subdir: str
    num_channels: int
    required: bool = False


class ReceiverOutputsConfig(StrictConfigModel):
    binaural_hrtf: ReceiverOutputConfig
    bte_rear_hartf: ReceiverOutputConfig | None = None
    bte_front_hartf: ReceiverOutputConfig | None = None


class RoomDimensionsConfig(StrictConfigModel):
    length: RangeFloat
    width: RangeFloat
    height: RangeFloat


class RoomMaterialsConfig(StrictConfigModel):
    walls: list[str]
    floor: list[str]
    ceiling: list[str]


class SemanticSurfacesConfig(StrictConfigModel):
    enable_walls: bool = True
    enable_floor: bool = True
    enable_ceiling: bool = True


class RoomSamplingConfig(StrictConfigModel):
    dimensions_m: RoomDimensionsConfig
    materials: RoomMaterialsConfig
    semantic_surfaces: SemanticSurfacesConfig


class TimingConfig(StrictConfigModel):
    allow_offsets: bool
    start_time_s: RangeFloat
    total_duration_s: float | None = None


class GainRangeConfig(StrictConfigModel):
    min: float
    max: float


class SourceOrientationStrategy(StrictConfigModel):
    type: str
    yaw_deg: RangeFloat | None = None
    pitch_deg: FixedFloat | None = None
    roll_deg: FixedFloat | None = None


class SpatialPolicyConfig(StrictConfigModel):
    type: str
    targets: dict[str, float] | None = None
    min_radius_from_receiver_m: float | None = None


class SourceTypeConfig(StrictConfigModel):
    event_type: str
    role: Literal["base", "optional"]
    min_count: int
    max_count: int
    probability: float
    audio_dir: Path
    directivity: Path | None = None
    spatial_policy: SpatialPolicyConfig
    orientation_strategy: SourceOrientationStrategy | None = None


class SourceSamplingConfig(StrictConfigModel):
    min_sources: int
    max_sources: int
    timing: TimingConfig
    gain_db: GainRangeConfig
    default_orientation_strategy: SourceOrientationStrategy
    source_types: list[SourceTypeConfig]


class SceneValidationConfig(StrictConfigModel):
    min_distance_source_to_receiver_m: float
    min_distance_between_sources_m: float
    require_sources_inside_room: bool = True
    require_receiver_inside_room: bool = True
    max_sampling_attempts_per_scene: int = 50


class NamingConfig(StrictConfigModel):
    scene_id_prefix: str
    wav_pattern: str
    metadata_pattern: str


class OutputsConfig(StrictConfigModel):
    artifact_root: Path
    run_name: str | None = None
    naming: NamingConfig


class ClarityRunnerConfig(StrictConfigModel):
    backend_project_path: Path | None = None
    entrypoint: str = "clarity-backend"
    auto_submit: bool = False
    use_uv: bool = True


class HearingDegradationConfig(StrictConfigModel):
    enabled: bool = False
    input_targets: list[str]
    hearing_profiles_path: Path
    output_dir: Path | None = None
    runner: ClarityRunnerConfig = ClarityRunnerConfig()


class AppConfig(StrictConfigModel):
    experiment: ExperimentConfig
    execution: ExecutionConfig
    raven: RavenConfig
    render: RenderConfig
    background_noise: BackgroundNoiseConfig = Field(default_factory=BackgroundNoiseConfig)
    receiver_sampling: ReceiverSamplingConfig
    receiver_outputs: ReceiverOutputsConfig
    room_sampling: RoomSamplingConfig
    source_sampling: SourceSamplingConfig
    scene_validation: SceneValidationConfig
    outputs: OutputsConfig
    hearing_degradation: HearingDegradationConfig
