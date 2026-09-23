from pathlib import Path
from typing import Annotated, Literal

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


class ReceiverRandomYawOrientationStrategy(StrictConfigModel):
    type: Literal["random_yaw"]
    yaw_deg: RangeFloat
    pitch_deg: FixedFloat
    roll_deg: FixedFloat


class ReceiverRandomYawPitchOrientationStrategy(StrictConfigModel):
    type: Literal["random_yaw_pitch"]
    yaw_deg: RangeFloat
    pitch_deg: RangeFloat
    roll_deg: RangeFloat


ReceiverOrientationStrategy = Annotated[
    ReceiverRandomYawOrientationStrategy | ReceiverRandomYawPitchOrientationStrategy,
    Field(discriminator="type"),
]


class ReceiverSamplingConfig(StrictConfigModel):
    one_receiver_per_scene: bool
    position_strategy: ReceiverPositionStrategy
    orientation_strategy: ReceiverOrientationStrategy


class ReceiverOutputConfig(StrictConfigModel):
    enabled: bool
    ir_catalog_path: Path
    file_pattern: str
    num_hrtfs: int = 1
    output_subdir: str
    num_channels: int
    required: bool = False


class ReceiverOutputsConfig(StrictConfigModel):
    binaural_hrtf: ReceiverOutputConfig
    bte_rear_hartf: ReceiverOutputConfig | None = None
    bte_front_hartf: ReceiverOutputConfig | None = None


class ShoeboxGeometryConfig(StrictConfigModel):
    type: Literal["shoebox"]
    probability: float
    length_m: RangeFloat
    width_m: RangeFloat


class TrapezoidGeometryConfig(StrictConfigModel):
    type: Literal["trapezoid"]
    probability: float
    base_a_m: RangeFloat
    base_b_m: RangeFloat
    depth_m: RangeFloat
    top_offset_m: RangeFloat


class LShapeGeometryConfig(StrictConfigModel):
    type: Literal["l_shape"]
    probability: float
    outer_length_m: RangeFloat
    outer_width_m: RangeFloat
    cutout_length_m: RangeFloat
    cutout_width_m: RangeFloat
    removed_corners: list[
        Literal["north_east", "north_west", "south_east", "south_west"]
    ]


RoomShapeConfig = Annotated[
    ShoeboxGeometryConfig | TrapezoidGeometryConfig | LShapeGeometryConfig,
    Field(discriminator="type"),
]


class RoomGeometryConfig(StrictConfigModel):
    height_m: RangeFloat
    shape_mix: list[RoomShapeConfig]


class RoomMaterialsConfig(StrictConfigModel):
    walls: list[str]
    floor: list[str]
    ceiling: list[str]


class RoomSamplingConfig(StrictConfigModel):
    max_rt30_s: float = 1.0
    geometry: RoomGeometryConfig
    materials: RoomMaterialsConfig


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
    azimuths_deg: list[float] | None = None
    elevations_deg: list[float] | None = None
    distances_m: list[float] | None = None


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
    max_scene_attempts: int = 50
    max_receiver_attempts: int = 50


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
    force_rerun: bool = False
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
