from typing import Set, Callable, Tuple, Dict
import pandas as pd
import numpy as np
import math

pd.options.mode.chained_assignment = None

boolConv = lambda x: x.replace({"true": 1, "false": 0})


def reduceRadianError(rad: float) -> float:
    """Takes a values in radians in and returns how close the value is to 0 radians taking loop around into account."""
    error = rad % (2 * math.pi)
    theError = min(abs(error), abs(error - (2 * math.pi)))
    if theError == abs(error):
        return error
    else:
        return error - (2 * math.pi)


# Key: ((yellow max, red max), (yellow average, red average))
channelMapping: Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]] = {
    "Front Left/turn": ("Front Left Turn Motor", (40, 60), (5, 20)),
    "Front Right/turn": ("Front Right Turn Motor", (40, 60), (5, 20)),
    "Rear Left/turn": ("Rear Left Turn Motor", (40, 60), (5, 20)),
    "Rear Right/turn": ("Rear Right Turn Motor", (40, 60), (5, 20)),
    "Front Left/drive": ("Front Left Drive Motor", (40, 60), (20, 40)),
    "Front Right/drive": ("Front Right Drive Motor", (40, 60), (20, 40)),
    "Rear Left/drive": ("Rear Left Drive Motor", (40, 60), (20, 40)),
    "Rear Right/drive": ("Rear Right Drive Motor", (40, 60), (20, 40)),
}


def GenerateMotorCurrentMetrics() -> Dict[
    str, Callable[[pd.DataFrame], Tuple[int, str]]
]:
    result = {}
    for key in channelMapping.keys():
        result[
            f"{channelMapping[key][0]} Max Current"
        ] = lambda df, key=key: ProcessMaxCurrent(key, df)
        result[
            f"{channelMapping[key][0]} Average Current"
        ] = lambda df, key=key: ProcessAverageCurrent(key, df)
    return result


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    """Returns a list of the metrics contained in this group and their corresponding functions."""
    normalDict = {
        "FL Turning Encoder Alignment": ProcessFrontLeftTurningEncoderAlignment,
        "FL Drive Motor Max Temp": ProcessFrontLeftDriveMaxTemp,
        "FL Turn Motor Max Temp": ProcessFrontLeftTurnMaxTemp,
        "FL Turn Motor Mean Error": ProcessFrontLeftTurnMeanError,
        "FL Drive Motor Mean Error": ProcessFrontLeftDriveMeanError,
        "FR Turning Encoder Alignment": ProcessFrontRightTurningEncoderAlignment,
        "FR Drive Motor Max Temp": ProcessFrontRightDriveMaxTemp,
        "FR Turn Motor Max Temp": ProcessFrontRightTurnMaxTemp,
        "FR Turn Motor Mean Error": ProcessFrontRightTurnMeanError,
        "FR Drive Motor Mean Error": ProcessFrontRightDriveMeanError,
        "RL Turning Encoder Alignment": ProcessRearLeftTurningEncoderAlignment,
        "RL Drive Motor Max Temp": ProcessRearLeftDriveMaxTemp,
        "RL Turn Motor Max Temp": ProcessRearLeftTurnMaxTemp,
        "RL Turn Motor Mean Error": ProcessRearLeftTurnMeanError,
        "RL Drive Motor Mean Error": ProcessRearLeftDriveMeanError,
        "RR Turning Encoder Alignment": ProcessRearRightTurningEncoderAlignment,
        "RR Drive Motor Max Temp": ProcessRearRightDriveMaxTemp,
        "RR Turn Motor Max Temp": ProcessRearRightTurnMaxTemp,
        "RR Turn Motor Mean Error": ProcessRearRightTurnMeanError,
        "RR Drive Motor Mean Error": ProcessRearRightDriveMeanError,
    }
    return normalDict | GenerateMotorCurrentMetrics()


def ProcessFrontLeftTurningEncoderAlignment(
    robotTelemetry: pd.DataFrame,
) -> Tuple[int, str]:
    return ProcessTurningEncoderAlignment("Front Left", robotTelemetry)


def ProcessFrontLeftDriveMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Front Left/drive", robotTelemetry)


def ProcessFrontLeftTurnMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Front Left/turn", robotTelemetry)


def ProcessFrontLeftDriveMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessDriveMotorMeanError("Front Left", robotTelemetry)


def ProcessFrontLeftTurnMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessTurnMotorMeanError("Front Left", robotTelemetry)


def ProcessFrontRightTurningEncoderAlignment(
    robotTelemetry: pd.DataFrame,
) -> Tuple[int, str]:
    return ProcessTurningEncoderAlignment("Front Right", robotTelemetry)


def ProcessFrontRightDriveMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Front Right/drive", robotTelemetry)


def ProcessFrontRightTurnMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Front Right/turn", robotTelemetry)


def ProcessFrontRightDriveMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessDriveMotorMeanError("Front Right", robotTelemetry)


def ProcessFrontRightTurnMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessTurnMotorMeanError("Front Right", robotTelemetry)


def ProcessRearLeftTurningEncoderAlignment(
    robotTelemetry: pd.DataFrame,
) -> Tuple[int, str]:
    return ProcessTurningEncoderAlignment("Rear Left", robotTelemetry)


def ProcessRearLeftDriveMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Rear Left/drive", robotTelemetry)


def ProcessRearLeftTurnMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Rear Left/turn", robotTelemetry)


def ProcessRearLeftDriveMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessDriveMotorMeanError("Rear Left", robotTelemetry)


def ProcessRearLeftTurnMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessTurnMotorMeanError("Rear Left", robotTelemetry)


def ProcessRearRightTurningEncoderAlignment(
    robotTelemetry: pd.DataFrame,
) -> Tuple[int, str]:
    return ProcessTurningEncoderAlignment("Rear Right", robotTelemetry)


def ProcessRearRightDriveMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Rear Right/drive", robotTelemetry)


def ProcessRearRightTurnMaxTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessMaxMotorTemp("Rear Right/turn", robotTelemetry)


def ProcessRearRightDriveMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessDriveMotorMeanError("Rear Right", robotTelemetry)


def ProcessRearRightTurnMeanError(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessTurnMotorMeanError("Rear Right", robotTelemetry)


def ProcessTurningEncoderAlignment(
    moduleKey: str, robotTelemetry: pd.DataFrame
) -> Tuple[int, str]:
    """Checks the alignment of the integrated NEO encoder with the external encoder's position.
    Args:
        moduleKey: The key of the module to check alignment for
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.
    Raises:
        None
    """
    # Grab data
    neoEncoder = robotTelemetry[
        robotTelemetry["Key"] == f"/swerve/{moduleKey}/turn/position"
    ]
    if neoEncoder.empty:
        return -1, "metric_not_implemented"
    absEncoder = robotTelemetry[
        robotTelemetry["Key"] == f"/swerve/{moduleKey}/turn/absolute"
    ]
    if absEncoder.empty:
        return -1, "metric_not_implemented"
    homes = robotTelemetry[robotTelemetry["Key"] == f"/swerve/{moduleKey}/home"]
    if homes.empty:
        return -1, "metric_not_implemented"
    # Cut out the garbage data at the beginning
    neoEncoder = neoEncoder.iloc[10:]
    absEncoder = absEncoder.iloc[10:]
    # Convert to numerics
    neoEncoder["Value"] = pd.to_numeric(neoEncoder["Value"])
    absEncoder["Value"] = pd.to_numeric(absEncoder["Value"])
    homes["Value"] = pd.to_numeric(homes["Value"])
    # Trim to smallest list
    minLen = min(len(neoEncoder), len(absEncoder))
    neoEncoder = neoEncoder.iloc[:minLen]
    absEncoder = absEncoder.iloc[:minLen]
    # Home offsets
    prevStart = 0
    for i in range(len(homes)):
        if prevStart != 0:
            absEncoder.update(
                absEncoder[
                    (absEncoder.index >= prevStart)
                    & (absEncoder.index < homes.index[i])
                ]["Value"]
                - homes["Value"].iloc[i]
            )
        prevStart = homes.index[i]
    absEncoder.update(
        absEncoder[absEncoder.index >= prevStart]["Value"]
        - homes["Value"].iloc[len(homes) - 1]
    )
    # Convert to one dataframe
    test = pd.DataFrame({"ABS": absEncoder["Value"], "NEO": neoEncoder["Value"]})
    # Interpolate
    test = test.interpolate(limit_direction="both")
    # Wrap NEO Encoder between 0 and 2pi
    test["NEO"] = test["NEO"] % (2 * math.pi)
    # Compute error
    test["Error"] = (test["ABS"] - test["NEO"]).abs()
    # Get rid of values three standard deviations away from the mean
    threeStd = test["Error"].std() * 3
    errorsFilt = test[abs(test["Error"] - test["Error"].mean()) <= threeStd]["Error"]
    # Get the maximum error and reduce error
    maxError = reduceRadianError(errorsFilt.max())
    # Calculate stoplight
    stoplight = 0
    if maxError > math.radians(12):
        stoplight = 2
    elif maxError > math.radians(8) and stoplight == 0:
        stoplight = 1
    return stoplight, f"{str(maxError)} rad"


def ProcessMaxMotorTemp(motorKey: str, robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    """Checks the temperature of a motor in the swerve drive
    Args:
        moduleKey: The key of the motor to check alignment for
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.
    Raises:
        None
    """
    # Grab data
    values = robotTelemetry[robotTelemetry["Key"] == f"/swerve/{motorKey}/temp"]
    if values.empty:
        return -1, "metric_not_implemented"
    # Cut out the garbage data at the beginning
    values = values.iloc[10:]
    # Convert to Numpy array
    valuesNp = pd.to_numeric(values["Value"]).to_numpy()
    maxTemp = valuesNp.max()
    stoplight = 0
    if maxTemp > 50:
        stoplight = 2
    elif maxTemp > 40 and stoplight == 0:
        stoplight = 1
    return stoplight, str(maxTemp)


def ProcessMeanMotorError(
    procesKey: str, setpointKey: str, robotTelemetry: pd.DataFrame
) -> float:
    """Checks the mean error of a motor in the swerve drive. Returns the mean value for motor-specific processing.
    Args:
        processKey: The key of the process variable to test
        setpointKey: The key of the setpoint the process variable is trying to track
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.
    Raises:
        None
    """
    # Grab data
    process = robotTelemetry[robotTelemetry["Key"] == procesKey]
    if process.empty:
        return np.nan
    setpoint = robotTelemetry[robotTelemetry["Key"] == setpointKey]
    if setpoint.empty:
        return np.nan
    fmsMode = robotTelemetry[robotTelemetry["Key"] == "DS:enabled"]
    if fmsMode.empty:
        return np.nan
    # Cut out the garbage data at the beginning
    process = process.iloc[10:]
    setpoint = setpoint.iloc[10:]
    # Convert to numeric
    process["Value"] = pd.to_numeric(process["Value"])
    setpoint["Value"] = pd.to_numeric(setpoint["Value"])
    # Only get data when robot is enabled
    lastDisabled = -1
    lastEnabled = 0
    processSlices = []
    setpointSlices = []
    for i in range(len(fmsMode)):
        if fmsMode["Value"].iloc[i] == True:
            lastEnabled = fmsMode.index[i]
        elif fmsMode["Value"].iloc[i] == False:
            lastDisabled = fmsMode.index[i]
        if lastDisabled > lastEnabled:
            processSlices.append(
                process[
                    (process.index >= lastEnabled) & (process.index <= lastDisabled)
                ]["Value"]
            )
            setpointSlices.append(
                setpoint[
                    (setpoint.index >= lastEnabled) & (setpoint.index <= lastDisabled)
                ]["Value"]
            )
            processSlices.append(process[process.index >= lastEnabled]["Value"])
            setpointSlices.append(setpoint[setpoint.index >= lastEnabled]["Value"])
    if lastDisabled < lastEnabled:
        processSlices.append(process[process.index >= lastEnabled]["Value"])
        setpointSlices.append(setpoint[setpoint.index >= lastEnabled]["Value"])
    processSeries = pd.concat(processSlices)
    setpointSeries = pd.concat(setpointSlices)
    processSeries = processSeries.drop_duplicates()
    setpointSeries = setpointSeries.drop_duplicates()
    # Create dataframe
    df = pd.DataFrame({"Proc": processSeries, "Set": setpointSeries})
    # Interpolate
    df = df.interpolate(limit_process="both")
    # Calculate error
    df["Error"] = df["Proc"] - df["Set"]
    # Filter values three standard deviations away from mean and get the maximum
    threeStd = df["Error"].std() * 3
    # Get rid of outliers
    df = df[abs(df["Error"] - df["Error"].mean()) <= threeStd]
    # Get the mean
    return df["Error"].mean()


def ProcessDriveMotorMeanError(
    moduleKey: str, robotTelemetry: pd.DataFrame
) -> Tuple[int, str]:
    """Checks the error of the drive motor in a swerve drive module
    Args:
        moduleKey: The key of the module to check error for
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.
    Raises:
        None
    """
    mean = abs(
        ProcessMeanMotorError(
            f"/swerve/{moduleKey}/drive/velocity",
            f"/swerve/{moduleKey}/drive/setpoint",
            robotTelemetry,
        )
    )
    stoplight = 0
    if mean > 0.1:
        stoplight = 2
    if mean > 0.05 and stoplight == 0:
        stoplight = 1
    return stoplight, f"{str(mean)} m/s"


def ProcessTurnMotorMeanError(
    moduleKey: str, robotTelemetry: pd.DataFrame
) -> Tuple[int, str]:
    """Checks the error of the turn motor in a swerve drive module
    Args:
        moduleKey: The key of the module to check error for
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.
    Raises:
        None
    """
    mean = abs(
        ProcessMeanMotorError(
            f"/swerve/{moduleKey}/turn/position",
            f"/swerve/{moduleKey}/turn/setpoint",
            robotTelemetry,
        )
    )
    mean = reduceRadianError(mean)
    if np.isnan(mean):
        return -1, "metric_not_implemented"
    stoplight = 0
    if mean > math.radians(12):
        stoplight = 2
    if mean > math.radians(8) and stoplight == 0:
        stoplight = 1
    return stoplight, f"{str(mean)} rad"


def ProcessMaxCurrent(motorKey: str, robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    """Process the maximum current draw of a swerve drive motor and uses it to determine a severity.

    Args:
        motorKey: The motor key to check
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    currents = robotTelemetry[robotTelemetry["Key"] == f"/swerve/{motorKey}/current"]
    if currents.empty:
        return -1, "metric_not_implemented"
    currents["Value"] = pd.to_numeric(currents["Value"])
    # Find the mean
    maxCurrent = (np.convolve(currents["Value"].to_numpy(), np.ones(50), "valid") / 50).max()
    stoplight = 0
    if maxCurrent > channelMapping[motorKey][1][1]:
        stoplight = 2
    elif maxCurrent > channelMapping[motorKey][1][0] and stoplight != 2:
        stoplight = 1
    return stoplight, f"{maxCurrent} A"


def ProcessAverageCurrent(
    motorKey: str, robotTelemetry: pd.DataFrame
) -> Tuple[int, str]:
    """Process the average current draw of a swerve drive motor and uses it to determine a severity.

    Args:
        motorKey: The motor key to check
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    currents = robotTelemetry[robotTelemetry["Key"] == f"/swerve/{motorKey}/current"]
    if currents.empty:
        return -1, "metric_not_implemented"
    currents["Value"] = pd.to_numeric(currents["Value"])
    # Find the mean
    maxCurrent = currents["Value"].mean()
    stoplight = 0
    if maxCurrent > channelMapping[motorKey][2][1]:
        stoplight = 2
    elif maxCurrent > channelMapping[motorKey][2][0] and stoplight != 2:
        stoplight = 1
    return stoplight, f"{maxCurrent} A"
