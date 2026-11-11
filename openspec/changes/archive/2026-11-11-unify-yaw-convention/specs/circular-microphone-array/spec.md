# Spec Delta

## MODIFIED Requirements

### Requirement: Horizontal yaw-only microphone geometry
The array SHALL consist of uniformly spaced omnidirectional point microphones without head or housing effects. Its center SHALL equal the sampled receiver position. For zero-based microphone index k, its public position SHALL be `center + radius_m * [cos(theta_k), 0, -sin(theta_k)]`, where `theta_k = yaw + 360*k/N` in degrees. Microphone 0 SHALL lie along the receiver forward direction (`spatial-pose-convention`). Pitch and roll SHALL NOT change the array geometry.

#### Scenario: Four microphones at zero yaw
- **WHEN** a four-microphone array has center `[cx, cy, cz]`, radius r, and yaw zero
- **THEN** microphone offsets in order are `[r,0,0]`, `[0,0,-r]`, `[-r,0,0]`, and `[0,0,r]`, within numerical tolerance

#### Scenario: Yaw rotation without tilt
- **WHEN** receiver yaw changes by 90 degrees while pitch or roll also changes
- **THEN** microphone 0 moves from the positive X offset to the negative Z offset and every microphone retains the center's Y coordinate

#### Scenario: Microphone 0 agrees with fixed-position azimuth zero
- **WHEN** a receiver with yaw Y carries an array and a `fixed_position` source at azimuth 0
- **THEN** the offset of microphone 0 from the center points in the same horizontal direction as the source offset from the receiver
