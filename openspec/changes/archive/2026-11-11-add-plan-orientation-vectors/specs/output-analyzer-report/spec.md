## ADDED Requirements

### Requirement: Floor plan orientation arrows
Each floor plan in "Positions by geometry" SHALL draw an orientation arrow from every receiver or source marker that has both a position and a yaw, pointing along plan direction `(cos yaw, −sin yaw)` in `(X, Z)` per the canonical pose convention. Arrows SHALL share one fixed on-screen length, use the marker color, ignore pitch and roll, and SHALL NOT change plan bounds, scale or marker positions.

#### Scenario: Receiver yaw 0 points to +X
- **WHEN** a scene's receiver has yaw 0
- **THEN** its plan arrow points toward increasing X on screen with no vertical component

#### Scenario: Receiver yaw +90 points to −Z
- **WHEN** a scene's receiver has yaw +90
- **THEN** its plan arrow points toward decreasing Z, i.e. downward on screen, with no horizontal component

#### Scenario: Source arrow uses source yaw
- **WHEN** a source has yaw −90
- **THEN** its plan arrow points toward increasing Z (upward on screen) in that source's event color

#### Scenario: Arrows do not move the plan
- **WHEN** the same scene is plotted with and without orientation data
- **THEN** the polygon, axis ticks and marker coordinates are identical

#### Scenario: Missing yaw draws no arrow
- **WHEN** a source has a valid position but no valid yaw
- **THEN** its marker is drawn, no arrow is drawn for it, and the legend reports the orientation count as drawn n and excluded ≥ 1

### Requirement: Orientation legend entry
A floor plan legend SHALL include an entry describing the arrows as the horizontal projection of yaw (yaw 0 = +X, positive yaw toward −Z; pitch and roll not shown) with the counts of orientations drawn and excluded. When no actor has an arrow, the entry SHALL read "Orientation: Not available" followed by the reason.

#### Scenario: Legend with all orientations
- **WHEN** a scene has a receiver and two sources, all with valid position and yaw
- **THEN** the legend states orientations drawn n=3, excluded=0

#### Scenario: Legend without orientations
- **WHEN** no actor in a scene has a valid yaw
- **THEN** the legend contains "Orientation: Not available" and the plan contains no arrows

### Requirement: Source yaw in source records
The analyzer SHALL read each manifest source's `orientation_deg.yaw` into a `source_yaw_deg` field of source records, in degrees in the public frame, and include it in the sources export. A missing or non-numeric yaw SHALL yield an empty value and SHALL NOT exclude the source. Sources whose spatial data is excluded for an unknown coordinate frame SHALL have an empty `source_yaw_deg`. Existing source fields and values SHALL remain unchanged.

#### Scenario: Manifest source yaw exported
- **WHEN** a manifest source has `orientation_deg.yaw` = −90
- **THEN** its source record and the sources export have `source_yaw_deg` = −90

#### Scenario: Missing source yaw
- **WHEN** a manifest source has no `orientation_deg`
- **THEN** its `source_yaw_deg` is empty and all its other fields are unchanged
