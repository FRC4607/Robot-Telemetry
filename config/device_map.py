"""Device-to-subsystem mapping for FRC 4607, 2026 (second robot "Power Play").
CAN bus: "kachow"
Source: FRC4607/comp-bot2-2026 main branch (rework merged)

Signal keys in the exported wpilog follow the pattern:
    Phoenix6/TalonFX-{id}/{Signal}
    Phoenix6/CANcoder-{id}/{Signal}
    Phoenix6/Pigeon2-{id}/{Signal}
"""

# --- Swerve Drive (from TunerConstants.java) ---
SWERVE_MODULES = {
    "Front Left":  {"drive": 23, "steer": 22, "cancoder": 22},
    "Front Right": {"drive": 0,  "steer": 1,  "cancoder": 1},
    "Back Left":   {"drive": 2,  "steer": 3,  "cancoder": 3},
    "Back Right":  {"drive": 21, "steer": 20, "cancoder": 20},
}
SWERVE_DRIVE_GEAR_RATIO = 6.026785714285714
SWERVE_STEER_GEAR_RATIO = 26.09090909090909
SWERVE_COUPLE_RATIO = 3.857142857142857
SWERVE_WHEEL_RADIUS_INCHES = 1.973
SWERVE_SPEED_AT_12V = 5.12  # m/s
SWERVE_SLIP_CURRENT = 120.0  # A

# --- Intake Arm (IntakeArm.java) ---
INTAKE_ARM_MOTOR = 15
INTAKE_ARM_CANCODER = 15
INTAKE_ARM_SENSOR_TO_MECH = 2
INTAKE_ARM_ROTOR_TO_SENSOR = 23
INTAKE_ARM_MAX_AMPERAGE = 80

# --- Intake Wheels (IntakeWheels.java) ---
INTAKE_WHEELS_MOTOR = 14
INTAKE_WHEELS_MAX_AMPERAGE = 80

# --- Indexer (Indexer.java) ---
INDEXER_MOTOR = 13
INDEXER_MAX_AMPERAGE = 80

# --- Left Chamber (LeftChamber.java) ---
LEFT_CHAMBER_MOTOR = 6
LEFT_CHAMBER_MAX_AMPERAGE = 80

# --- Right Chamber (RightChamber.java) ---
RIGHT_CHAMBER_MOTOR = 17
RIGHT_CHAMBER_MAX_AMPERAGE = 80

# --- Left Turret (LeftTurret.java) ---
LEFT_TURRET_MOTOR = 7
LEFT_TURRET_ENCODER1 = 31
LEFT_TURRET_ENCODER2 = 32
LEFT_TURRET_ROTOR_TO_MECH = 10.2
LEFT_TURRET_MAX_AMPERAGE = 80

# --- Right Turret (RightTurret.java) ---
RIGHT_TURRET_MOTOR = 16
RIGHT_TURRET_ENCODER1 = 41
RIGHT_TURRET_ENCODER2 = 42
RIGHT_TURRET_ROTOR_TO_MECH = 10.2
RIGHT_TURRET_MAX_AMPERAGE = 80

# --- Left Hood (LeftHood.java) ---
LEFT_HOOD_MOTOR = 8
LEFT_HOOD_MAX_AMPERAGE = 80

# --- Right Hood (RightHood.java) ---
RIGHT_HOOD_MOTOR = 9
RIGHT_HOOD_MAX_AMPERAGE = 80

# --- Left Flywheel (LeftFlywheel.java) ---
LEFT_FLYWHEEL_MOTOR1 = 4
LEFT_FLYWHEEL_MOTOR2 = 5
LEFT_FLYWHEEL_MAX_AMPERAGE = 80

# --- Right Flywheel (RightFlywheel.java) ---
RIGHT_FLYWHEEL_MOTOR1 = 19
RIGHT_FLYWHEEL_MOTOR2 = 18
RIGHT_FLYWHEEL_MAX_AMPERAGE = 80

# --- IMU ---
PIGEON_ID = 0

# --- All TalonFX IDs (for power metrics) ---
ALL_TALON_IDS = set()
for _mod in SWERVE_MODULES.values():
    ALL_TALON_IDS.add(_mod["drive"])
    ALL_TALON_IDS.add(_mod["steer"])
for _id in [
    INTAKE_ARM_MOTOR, INTAKE_WHEELS_MOTOR, INDEXER_MOTOR,
    LEFT_CHAMBER_MOTOR, RIGHT_CHAMBER_MOTOR,
    LEFT_TURRET_MOTOR, RIGHT_TURRET_MOTOR,
    LEFT_HOOD_MOTOR, RIGHT_HOOD_MOTOR,
    LEFT_FLYWHEEL_MOTOR1, LEFT_FLYWHEEL_MOTOR2,
    RIGHT_FLYWHEEL_MOTOR1, RIGHT_FLYWHEEL_MOTOR2,
]:
    ALL_TALON_IDS.add(_id)

ALL_CANCODER_IDS = set()
for _mod in SWERVE_MODULES.values():
    ALL_CANCODER_IDS.add(_mod["cancoder"])
for _id in [
    INTAKE_ARM_CANCODER,
    LEFT_TURRET_ENCODER1, LEFT_TURRET_ENCODER2,
    RIGHT_TURRET_ENCODER1, RIGHT_TURRET_ENCODER2,
]:
    ALL_CANCODER_IDS.add(_id)


def talon_key(device_id: int, signal: str) -> str:
    """Build a Phoenix6 TalonFX signal key."""
    return f"Phoenix6/TalonFX-{device_id}/{signal}"


def cancoder_key(device_id: int, signal: str) -> str:
    """Build a Phoenix6 CANcoder signal key."""
    return f"Phoenix6/CANcoder-{device_id}/{signal}"


def pigeon_key(signal: str, device_id: int = PIGEON_ID) -> str:
    """Build a Phoenix6 Pigeon2 signal key."""
    return f"Phoenix6/Pigeon2-{device_id}/{signal}"
