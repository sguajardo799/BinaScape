# spatial-pose-convention Specification

## Purpose

Define one orientation convention for poses in the public scene frame and require every component that turns angles into directions, or directions into angles, to use it consistently.

## Requirements

### Requirement: Canonical public pose convention
The public frame SHALL be right-handed `(x, y, z)` in meters with +Y vertical. For yaw `y` and pitch `p` in degrees, the forward direction SHALL be `(cos p·cos y, sin p, −cos p·sin y)`. Yaw 0 and pitch 0 SHALL point to +X; positive yaw SHALL be a right-hand rotation about +Y; positive pitch SHALL tilt toward +Y. Roll SHALL NOT change the forward direction. The up direction SHALL be +Y.

#### Scenario: Zero orientation faces +X
- **WHEN** a pose has yaw 0, pitch 0 and any roll
- **THEN** its forward direction is `[1, 0, 0]` within 1e-12

#### Scenario: Positive yaw turns toward -Z
- **WHEN** a pose has yaw +90 and pitch 0
- **THEN** its forward direction is `[0, 0, -1]` within 1e-12

### Requirement: Rendered left/right meaning
Binaural WAV channel 1 SHALL be the left ear and channel 2 the right ear. Because the public→RAVEN mapping reflects Z, the rendered scene is mirrored: at yaw 0 the right ear SHALL face public −Z, and a positive azimuth SHALL be heard on the right.

#### Scenario: Positive azimuth is heard on the right
- **WHEN** a receiver with any yaw has a `fixed_position` source at azimuth +90, elevation 0, rendered with RAVEN and an HRTF
- **THEN** WAV channel 2 (right ear) has higher energy than channel 1 (left ear)

#### Scenario: Negative azimuth is heard on the left
- **WHEN** the same receiver has a `fixed_position` source at azimuth −90
- **THEN** WAV channel 1 (left ear) has higher energy than channel 2 (right ear)

### Requirement: Fixed-position sources relative to receiver orientation
A `fixed_position` source with azimuth `a`, elevation `e` and distance `d` SHALL be placed at the receiver position plus `d` times the canonical forward direction for yaw `receiver_yaw + a` and pitch `receiver_pitch + e`. Receiver roll SHALL be ignored.

#### Scenario: Azimuth zero is in front for any yaw
- **WHEN** a receiver has yaw Y and pitch 0 and a `fixed_position` source has azimuth 0, elevation 0 and distance d
- **THEN** the source lies at receiver position + d·forward(Y, 0), i.e. along the receiver's forward direction

#### Scenario: Yaw +90 with azimuth 0
- **WHEN** a receiver at `[2, 1, 3]` has yaw +90 and the source has azimuth 0, elevation 0, distance 1
- **THEN** the source is at `[2, 1, 2]`

### Requirement: Wall-facing source orientation
A source with orientation strategy `facing_surface_normal` SHALL receive yaw such that its canonical forward direction equals the inward horizontal normal of its wall, with pitch 0 and roll 0.

#### Scenario: Wall with inward normal +Z
- **WHEN** a source is placed on a wall whose inward normal is `[0, 0, 1]`
- **THEN** its yaw is −90 and its forward direction is `[0, 0, 1]`

### Requirement: RAVEN adapter preserves canonical directions
For manifests using the polygon coordinate contract (schema 2.0 and 3.0), the MATLAB adapter SHALL compute receiver and source view vectors with the canonical convention in the public frame and then apply the same public→RAVEN reflection `[1, 1, −1]` applied to positions. Manifests outside that contract SHALL keep their existing behavior.

#### Scenario: Receiver yaw +90 in schema 3.0
- **WHEN** a schema 3.0 receiver has position `[1.0, 1.5, 2.0]` and yaw +90, pitch 0
- **THEN** RAVEN receives position `[1.0, 1.5, -2.0]` and view vector `[0, 0, 1]`

#### Scenario: Source in front stays in front in RAVEN
- **WHEN** a schema 3.0 receiver with yaw Y has a `fixed_position` source at azimuth 0
- **THEN** the source position minus the receiver position in RAVEN coordinates is parallel to and in the same direction as the receiver's RAVEN view vector, for Y in {−135, −90, 0, 45, 90, 180}

#### Scenario: Legacy manifests unchanged
- **WHEN** a manifest without schema 2.0 or 3.0 has receiver yaw +90
- **THEN** RAVEN receives the same view vector as before this change, `[0, 0, 1]`

### Requirement: Render validation uses adapter directions
Render validation SHALL derive expected source view vectors with the same convention and reflection as the RAVEN adapter for each schema.

#### Scenario: Validation agrees with adapter
- **WHEN** a schema 3.0 source has yaw +90 and pitch 0
- **THEN** validation expects RAVEN view vector `[0, 0, 1]`

### Requirement: Analyzer direction of arrival
The output analyzer SHALL report global DOA as the azimuth of the receiver→source vector `(dx, dy, dz)` in the public frame using the canonical convention, `atan2(−dz, dx)` in degrees wrapped to `[−180, 180)`, and receiver-relative DOA as global DOA minus receiver yaw, wrapped to `[−180, 180)`. Elevation SHALL remain `atan2(dy, hypot(dx, dz))`.

#### Scenario: Source along +X
- **WHEN** the source is at receiver + `[1, 0, 0]` and receiver yaw is 0
- **THEN** global DOA is 0 and relative DOA is 0

#### Scenario: Fixed-position round trip
- **WHEN** a receiver with yaw Y has a `fixed_position` source at azimuth a and elevation 0
- **THEN** the analyzer reports relative DOA equal to a, wrapped to `[−180, 180)`, within 1e-6
