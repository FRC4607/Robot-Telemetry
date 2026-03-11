"""
Device-to-subsystem mapping for Slap Shot (FRC 4607, 2026).
CAN bus: "kachow"
Source: FRC4607/comp-bot2026 ClosedLoopControl branch

Signal keys in the exported wpilog follow the pattern:
    Phoenix6/TalonFX-{id}/{Signal}
    Phoenix6/CANcoder-{id}/{Signal}
    Phoenix6/Pigeon2-{id}/{Signal}
"""

# --- Swerve Drive (from TunerConstants.java) ---
SWERVE_MODULES = {
    "Front Left":  {"drive": 1,  "steer": 3,  "cancoder": 3},
    "Front Right": {"drive": 40, "steer": 28, "cancoder": 2},
    "Back Left":   {"drive": 18, "steer": 16, "cancoder": 16},
    "Back Right":  {"drive": 19, "steer": 17, "cancoder": 17},
}
SWERVE_DRIVE_GEAR_RATIO = 6.026785714285714
SWERVE_STEER_GEAR_RATIO = 26.09090909090909
SWERVE_COUPLE_RATIO = 3.857142857142857
SWERVE_WHEEL_RADIUS_INCHES = 2.0
SWERVE_SPEED_AT_12V = 5.12  # m/s
SWERVE_SLIP_CURRENT = 120.0  # A

# --- Intake Arm (IntakeArm.java) ---
INTAKE_ARM_MOTOR = 48
INTAKE_ARM_CANCODER = 24
INTAKE_ARM_SENSOR_TO_MECH = 2.25
INTAKE_ARM_ROTOR_TO_SENSOR = 23
INTAKE_ARM_MAX_AMPERAGE = 80

# --- Intake Wheels (IntakeWheels.java) ---
INTAKE_WHEELS_MOTOR = 45
INTAKE_WHEELS_MAX_AMPERAGE = 80

# --- Indexer (Indexer.java) ---
INDEXER_MOTOR = 26
INDEXER_MAX_AMPERAGE = 80

# --- Chamber (Chamber.java) ---
CHAMBER_MOTOR = 55
CHAMBER_MAX_AMPERAGE = 80

# --- Turret (Turret.java) ---
TURRET_MOTOR = 32
TURRET_ENCODER1 = 50
TURRET_ENCODER2 = 51
TURRET_ROTOR_TO_MECH = 10.2
TURRET_MAX_AMPERAGE = 80

# --- Hood (Hood.java, TalonFXS) ---
HOOD_MOTOR = 52
HOOD_MAX_AMPERAGE = 80

# --- Flywheel (Flywheel.java) ---
FLYWHEEL_MOTOR1 = 50
FLYWHEEL_MOTOR2 = 51
FLYWHEEL_MAX_AMPERAGE = 80

# --- Climber ---
CLIMBER_OUTER_MOTOR1 = 23
CLIMBER_OUTER_MOTOR2 = 6
CLIMBER_INNER_MOTOR1 = 15
CLIMBER_INNER_MOTOR2 = 56
CLIMBER_MAX_AMPERAGE = 80
CLIMBER_INCHES_PER_REV = 0.19864

# --- IMU ---
PIGEON_ID = 0

# --- All TalonFX IDs (for power metrics) ---
ALL_TALON_IDS = set()
for _mod in SWERVE_MODULES.values():
    ALL_TALON_IDS.add(_mod["drive"])
    ALL_TALON_IDS.add(_mod["steer"])
for _id in [
    INTAKE_ARM_MOTOR, INTAKE_WHEELS_MOTOR, INDEXER_MOTOR, CHAMBER_MOTOR,
    TURRET_MOTOR, HOOD_MOTOR, FLYWHEEL_MOTOR1, FLYWHEEL_MOTOR2,
    CLIMBER_OUTER_MOTOR1, CLIMBER_OUTER_MOTOR2,
    CLIMBER_INNER_MOTOR1, CLIMBER_INNER_MOTOR2,
]:
    ALL_TALON_IDS.add(_id)

ALL_CANCODER_IDS = set()
for _mod in SWERVE_MODULES.values():
    ALL_CANCODER_IDS.add(_mod["cancoder"])
for _id in [INTAKE_ARM_CANCODER, TURRET_ENCODER1, TURRET_ENCODER2]:
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
