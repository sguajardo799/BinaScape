# Spec Delta

## REMOVED Requirements

### Requirement: Legacy circular-array angular convention preserved
**Reason**: The known risk is corrected by `unify-yaw-convention`. The circular-array geometry now follows the canonical pose convention (`spatial-pose-convention`), so microphone 0 and `fixed_position` azimuth 0 agree for every receiver yaw.
**Migration**: The backend keeps rendering the descriptor's microphone positions without recomputing them. Array outputs produced before this change used the opposite ring direction and must be regenerated; they must not be mixed with new outputs for DOA labels.
